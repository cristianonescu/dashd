"""Pet preview pipeline.

Returns just enough for the UI to animate a small canvas: the original
spritesheet bytes (downscaled), grid dimensions, and the canonical state
names. Decouples preview from install — you can preview pets you've
never installed and the currently-active pet without re-fetching.

Cache layout (next to the `.dpet` cache):
    ~/.config/dashd/pets/<slug>.dpet              — converted, for the device
    ~/.config/dashd/pets/<slug>.preview.webp      — original sheet, downscaled
    ~/.config/dashd/pets/<slug>.preview.json      — grid + states
"""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from dashd.pets import CACHE_DIR
from dashd.pets.catalog import PetEntry, lookup, slug_to_name
from dashd.pets.converter import DEFAULT_GRID, DEFAULT_STATES
from dashd.pets.downloader import download_bundle

log = logging.getLogger("dashd.pets.preview")

# Downscale the sheet before shipping it through IPC so the JSON line stays
# small. 384 px on the longer axis keeps each per-state row > 32 px, which
# is enough for a crisp preview canvas.
PREVIEW_MAX_DIM = 384
PREVIEW_QUALITY = 82


@dataclass(frozen=True)
class Preview:
    slug: str
    name: str
    image_bytes: bytes        # WebP-encoded, downscaled
    mime: str                 # "image/webp"
    rows: int
    cols: int
    states: list[str]
    # Real frame count per row — so the UI cycler can wrap by anim, not by
    # the grid column count. Many pets have anims shorter than `cols`, so
    # without this the preview spills onto blank cells and flashes.
    frames_per_state: list[int] | None = None
    creator: str | None = None
    source_url: str | None = None


def _downscale_webp(src_bytes: bytes) -> bytes:
    """Downscale + re-encode as WebP. Keeps alpha."""
    im = Image.open(io.BytesIO(src_bytes)).convert("RGBA")
    w, h = im.size
    scale = min(1.0, PREVIEW_MAX_DIM / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                       Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=PREVIEW_QUALITY, method=4)
    return out.getvalue()


def _count_frames_per_row(src_bytes: bytes, rows: int, cols: int) -> list[int]:
    """Count cells per row with any non-transparent pixel. Cheap one-time
    scan; lets the UI animator wrap correctly per-animation."""
    im = Image.open(io.BytesIO(src_bytes)).convert("RGBA")
    arr = np.array(im)
    alpha = arr[..., 3]
    H, W = alpha.shape
    cw, ch = W // cols, H // rows
    out: list[int] = []
    for r in range(rows):
        n = 0
        for c in range(cols):
            cell = alpha[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            if cell.any():
                n += 1
            else:
                break  # frames within a row are packed left-to-right
        out.append(n)
    return out


def _preview_paths(slug: str) -> tuple[Path, Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return (CACHE_DIR / f"{slug}.preview.webp",
            CACHE_DIR / f"{slug}.preview.json")


def _default_bundle_bytes() -> bytes:
    """The Claw'd spritesheet that ships with the agent itself."""
    # importlib.resources works for both editable installs and PyInstaller
    # frozen executables (the .spec adds dashd/pets/data/ as a data dir).
    with resources.files("dashd.pets.data").joinpath("default-claw-d.webp").open("rb") as f:
        return f.read()


def _load_cached(slug: str) -> Preview | None:
    img_p, meta_p = _preview_paths(slug)
    if not (img_p.is_file() and meta_p.is_file()):
        return None
    try:
        meta = json.loads(meta_p.read_text())
        return Preview(
            slug=slug,
            name=meta.get("name", slug_to_name(slug)),
            image_bytes=img_p.read_bytes(),
            mime="image/webp",
            rows=int(meta.get("rows", DEFAULT_GRID["rows"])),
            cols=int(meta.get("cols", DEFAULT_GRID["cols"])),
            states=list(meta.get("states") or DEFAULT_STATES),
            frames_per_state=meta.get("frames_per_state"),
            creator=meta.get("creator"),
            source_url=meta.get("source_url"),
        )
    except Exception as e:
        log.warning("preview cache read failed for %s: %s", slug, e)
        return None


def _save_cache(p: Preview) -> None:
    img_p, meta_p = _preview_paths(p.slug)
    img_p.write_bytes(p.image_bytes)
    meta_p.write_text(json.dumps({
        "name": p.name, "rows": p.rows, "cols": p.cols, "states": p.states,
        "frames_per_state": p.frames_per_state,
        "creator": p.creator, "source_url": p.source_url,
    }))


async def get_preview(slug_or_url: str,
                      client: httpx.AsyncClient | None = None) -> Preview:
    """Return a Preview for the requested slug. Reads cache, falls back to
    download + cache. The "default" slug always returns the bundled
    Claw'd sheet — never hits the network."""
    if slug_or_url in ("default", "") or slug_or_url is None:
        sheet_bytes = _default_bundle_bytes()
        return Preview(
            slug="default",
            name="Claw'd",
            image_bytes=_downscale_webp(sheet_bytes),
            mime="image/webp",
            rows=DEFAULT_GRID["rows"], cols=DEFAULT_GRID["cols"],
            states=list(DEFAULT_STATES),
            frames_per_state=_count_frames_per_row(sheet_bytes, DEFAULT_GRID["rows"], DEFAULT_GRID["cols"]),
            creator="krrsantan",
            source_url="https://codexpets.net/gallery/claw-d",
        )

    entry = lookup(slug_or_url)
    if entry is None:
        raise ValueError(f"unrecognized pet slug or URL: {slug_or_url}")

    cached = _load_cached(entry.slug)
    if cached:
        return cached

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        bundle = await download_bundle(entry.download_url, entry.slug, client=client)
        rows = int((bundle.manifest.get("grid") or DEFAULT_GRID).get("rows", DEFAULT_GRID["rows"]))
        cols = int((bundle.manifest.get("grid") or DEFAULT_GRID).get("cols", DEFAULT_GRID["cols"]))
        states = list(bundle.manifest.get("states") or DEFAULT_STATES[:rows])
        name = bundle.manifest.get("displayName") or slug_to_name(entry.slug)
        creator = bundle.manifest.get("creator")
        preview = Preview(
            slug=entry.slug,
            name=name,
            image_bytes=_downscale_webp(bundle.spritesheet_bytes),
            mime="image/webp",
            rows=rows, cols=cols,
            states=states,
            frames_per_state=_count_frames_per_row(bundle.spritesheet_bytes, rows, cols),
            creator=creator,
            source_url=entry.gallery_url,
        )
        _save_cache(preview)
        return preview
    finally:
        if own_client:
            await client.aclose()
