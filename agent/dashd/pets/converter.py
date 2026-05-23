"""Convert a `PetBundle` to a packed `.dpet` binary.

Container format (little-endian, byte-aligned):

    Magic      : 4 bytes  "DPET"
    Version    : uint8    (currently 1)
    Reserved   : 3 bytes  (zero)
    Frame W    : uint16
    Frame H    : uint16
    Frame count: uint16
    Anim count : uint16
    Anim table : anim_count × {
                   16 byte ASCII name (NUL-padded),
                   uint16 first_frame,
                   uint16 frame_count
                 }
    Frames     : frame_count × (W*H*2  RGB565 pixels  +  ((W*H + 7)//8) alpha-mask bytes)

The firmware reads this layout straight from LittleFS — no PNG decoding
on-device — so animation is fast and binary-aligned.
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from PIL import Image
import numpy as np

from dashd.pets import DPET_MAGIC, DPET_VERSION, FRAME_W, FRAME_H
from dashd.pets.downloader import PetBundle


# Default state list when the bundle's pet.json has no `states` array. Codex
# pets all use the same canonical 9-state grid in row order.
DEFAULT_STATES = ["idle", "run_right", "run_left", "wave", "jump",
                  "failed", "waiting", "running", "review"]
DEFAULT_GRID = {"rows": 9, "cols": 8}

NAME_FIELD_LEN = 16


@dataclass(frozen=True)
class DPet:
    raw: bytes
    frame_count: int
    anim_count: int
    states: list[str]
    frames_per_state: dict[str, int]


def _rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def _fit_square(im: Image.Image, size: int) -> Image.Image:
    """Crop to alpha bounding box, scale longest side to size, center."""
    arr = np.array(im)
    if arr.shape[-1] >= 4:
        alpha = arr[..., 3]
        if alpha.any():
            ys, xs = np.where(alpha > 0)
            im = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    w, h = im.size
    scale = size / max(w, h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - nw) // 2, (size - nh) // 2), im)
    return canvas


def _encode_frame(im: Image.Image) -> tuple[bytes, bytes]:
    arr = np.array(im)
    rgb = arr[..., :3].reshape(-1, 3)
    pixels = bytearray()
    for r, g, b in rgb.tolist():
        pixels += struct.pack("<H", _rgb565(int(r), int(g), int(b)))
    alpha = (arr[..., 3] >= 64).astype(np.uint8).flatten().tolist()
    mask = bytearray()
    for i in range(0, len(alpha), 8):
        v = 0
        for k in range(8):
            if i + k < len(alpha) and alpha[i + k]:
                v |= 1 << (7 - k)
        mask.append(v)
    return bytes(pixels), bytes(mask)


def convert(bundle: PetBundle,
            frame_w: int = FRAME_W,
            frame_h: int = FRAME_H) -> DPet:
    """Convert a downloaded bundle into a .dpet binary in memory."""
    if frame_w != frame_h:
        raise ValueError("frame_w and frame_h must be equal (square frames)")

    grid = bundle.manifest.get("grid") or DEFAULT_GRID
    rows = int(grid.get("rows", DEFAULT_GRID["rows"]))
    cols = int(grid.get("cols", DEFAULT_GRID["cols"]))
    states = bundle.manifest.get("states") or DEFAULT_STATES[:rows]
    if len(states) < rows:
        states = list(states) + DEFAULT_STATES[len(states):rows]
    states = states[:rows]

    sheet = Image.open(io.BytesIO(bundle.spritesheet_bytes)).convert("RGBA")
    sw, sh = sheet.size
    cw, ch = sw // cols, sh // rows

    frame_blobs: list[tuple[bytes, bytes]] = []
    frames_per_state: dict[str, int] = {}

    for row, state in enumerate(states):
        in_row = 0
        for col in range(cols):
            cell = sheet.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            if not np.array(cell)[..., 3].any():
                continue
            frame_blobs.append(_encode_frame(_fit_square(cell, frame_w)))
            in_row += 1
        frames_per_state[state] = in_row

    # Pack the header.
    out = io.BytesIO()
    out.write(DPET_MAGIC)                                 # 4
    out.write(struct.pack("<B3x", DPET_VERSION))          # 1 + 3 reserved
    out.write(struct.pack("<HH", frame_w, frame_h))       # 4
    out.write(struct.pack("<HH", len(frame_blobs), len(states)))  # 4

    offset = 0
    for state in states:
        name_bytes = state.encode("ascii", errors="replace")[:NAME_FIELD_LEN]
        out.write(name_bytes.ljust(NAME_FIELD_LEN, b"\x00"))
        count = frames_per_state.get(state, 0)
        out.write(struct.pack("<HH", offset, count))
        offset += count

    for pixels, mask in frame_blobs:
        out.write(pixels)
        out.write(mask)

    raw = out.getvalue()
    return DPet(
        raw=raw,
        frame_count=len(frame_blobs),
        anim_count=len(states),
        states=list(states),
        frames_per_state=frames_per_state,
    )


def parse_header(raw: bytes) -> dict:
    """Inspect a .dpet without rendering it. Useful for validation + UI status."""
    if len(raw) < 16 or raw[:4] != DPET_MAGIC:
        raise ValueError("not a dpet file (missing DPET magic)")
    version = raw[4]
    if version != DPET_VERSION:
        raise ValueError(f"unsupported dpet version {version}")
    frame_w, frame_h = struct.unpack_from("<HH", raw, 8)
    frame_count, anim_count = struct.unpack_from("<HH", raw, 12)
    states: list[tuple[str, int, int]] = []
    pos = 16
    for _ in range(anim_count):
        name = raw[pos:pos + NAME_FIELD_LEN].split(b"\x00", 1)[0].decode("ascii", "replace")
        first, count = struct.unpack_from("<HH", raw, pos + NAME_FIELD_LEN)
        states.append((name, first, count))
        pos += NAME_FIELD_LEN + 4
    return {
        "version": version,
        "frame_w": frame_w,
        "frame_h": frame_h,
        "frame_count": frame_count,
        "anim_count": anim_count,
        "states": states,
        "frames_offset": pos,
    }
