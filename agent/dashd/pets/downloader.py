"""Download a pet bundle ZIP from codexpets.net and unpack its contents.

The bundle is small (≤ 2 MB observed) — we hold it all in memory.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass

import httpx

log = logging.getLogger("dashd.pets.downloader")


@dataclass(frozen=True)
class PetBundle:
    slug: str
    manifest: dict          # parsed pet.json
    spritesheet_bytes: bytes  # the WebP / PNG bytes


REQUIRED = ("pet.json",)
SHEET_NAMES = ("spritesheet.webp", "spritesheet.png", "spritesheet.jpg")


async def download_bundle(download_url: str, slug: str,
                          client: httpx.AsyncClient | None = None) -> PetBundle:
    """Fetch the ZIP, validate, and return a PetBundle. Raises ValueError on
    malformed bundles and httpx.HTTPError on network/HTTP failures."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        r = await client.get(download_url)
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(zf.namelist())

        for needed in REQUIRED:
            if needed not in names:
                raise ValueError(f"bundle missing required file: {needed}")
        try:
            manifest = json.loads(zf.read("pet.json").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"pet.json not valid JSON: {e}") from e

        sheet_name = None
        for name in SHEET_NAMES:
            if name in names:
                sheet_name = name
                break
        if sheet_name is None:
            raise ValueError(f"bundle missing spritesheet (looked for: {SHEET_NAMES})")
        sheet_bytes = zf.read(sheet_name)
        log.info("downloaded %s: manifest=%s, sheet=%d bytes",
                 slug, list(manifest.keys()), len(sheet_bytes))
        return PetBundle(slug=slug, manifest=manifest, spritesheet_bytes=sheet_bytes)
    finally:
        if own_client:
            await client.aclose()
