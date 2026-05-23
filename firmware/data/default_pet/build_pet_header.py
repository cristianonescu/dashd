#!/usr/bin/env python3
"""Convert spritesheet.webp + pet.json → default_pet.h.

Produces a single C++ header that the firmware can include directly. The
output stays under ~350 KB at the default 48×48 target size.

Output schema (paraphrased):
    struct PetFrame   { uint16_t pixels[FRAME_W * FRAME_H]; uint8_t mask[…]; };
    struct PetAnim    { const char* name; uint16_t first_frame; uint16_t frame_count; };
    constexpr int     PET_FRAME_W, PET_FRAME_H, PET_TOTAL_FRAMES, PET_ANIM_COUNT;
    constexpr PetAnim PET_ANIMS[];
    constexpr PetFrame PET_FRAMES[];

Pixel encoding:
    pixels[i]  = RGB565 (uint16_t, MSB first not required; native LE)
    mask[i/8]  = 1 bit per pixel: 1 if opaque, 0 if transparent
                 (bit 7 = leftmost pixel of the byte)

Frames are downsampled with Lanczos and recentered so the visible sprite
fits the target square; transparent pixels around the silhouette stay
transparent and the firmware just skips them when blitting.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import numpy as np

HERE = Path(__file__).parent
SHEET = HERE / "spritesheet.webp"
MANIFEST = HERE / "pet.json"
OUT = HERE.parent.parent / "include" / "default_pet.h"

# Target frame size on the device. 48×48 looks right as a corner overlay on
# a 240×320 portrait display. Adjust here if you want bigger/smaller.
FRAME_W = 48
FRAME_H = 48


def rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def fit_square(im: Image.Image, size: int) -> Image.Image:
    """Trim transparent margins, scale longest side to `size`, center in canvas."""
    arr = np.array(im)
    alpha = arr[..., 3]
    if alpha.any():
        ys, xs = np.where(alpha > 0)
        y0, y1 = ys.min(), ys.max() + 1
        x0, x1 = xs.min(), xs.max() + 1
        im = im.crop((x0, y0, x1, y1))
    w, h = im.size
    scale = size / max(w, h)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    im = im.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - new_w) // 2, (size - new_h) // 2), im)
    return canvas


def encode_frame(im: Image.Image) -> tuple[list[int], list[int]]:
    """Return (rgb565_pixels, alpha_mask_bytes) for one frame."""
    arr = np.array(im)
    # alpha < 64 → transparent. Tweakable but works for clean pixel-art sprites.
    alpha = (arr[..., 3] >= 64).astype(np.uint8).flatten()
    pixels = []
    for r, g, b in arr[..., :3].reshape(-1, 3):
        pixels.append(rgb565(int(r), int(g), int(b)))
    # Pack mask: 8 pixels per byte, bit 7 = leftmost.
    mask = []
    for i in range(0, len(alpha), 8):
        b = 0
        for k in range(8):
            if i + k < len(alpha) and alpha[i + k]:
                b |= 1 << (7 - k)
        mask.append(b)
    return pixels, mask


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    rows = manifest["grid"]["rows"]
    cols = manifest["grid"]["cols"]
    states = manifest["states"]
    assert rows == len(states), f"rows {rows} != states {len(states)}"

    sheet = Image.open(SHEET).convert("RGBA")
    sw, sh = sheet.size
    cell_w, cell_h = sw // cols, sh // rows
    print(f"sheet: {sw}x{sh}, cell: {cell_w}x{cell_h}, target: {FRAME_W}x{FRAME_H}")

    frames_per_state: list[list[tuple[list[int], list[int]]]] = []
    for row, state in enumerate(states):
        row_frames = []
        for col in range(cols):
            cell = sheet.crop((col * cell_w, row * cell_h,
                               (col + 1) * cell_w, (row + 1) * cell_h))
            if not np.array(cell)[..., 3].any():
                # Empty cell: end of this animation early.
                continue
            row_frames.append(encode_frame(fit_square(cell, FRAME_W)))
        frames_per_state.append(row_frames)
        print(f"  {state}: {len(row_frames)} frames")

    # Flatten + emit
    total_frames = sum(len(s) for s in frames_per_state)
    raw_bytes = total_frames * (FRAME_W * FRAME_H * 2 + (FRAME_W * FRAME_H + 7) // 8)
    print(f"total frames: {total_frames}, ~{raw_bytes/1024:.1f} KB raw")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write(f"// AUTO-GENERATED from {SHEET.name} + {MANIFEST.name}\n")
        f.write(f"// Original pet: {manifest['displayName']} by {manifest.get('creator','?')}\n")
        f.write(f"// Source: {manifest.get('source','?')}\n")
        f.write("// Re-run firmware/data/default_pet/build_pet_header.py to regenerate.\n\n")
        f.write("#pragma once\n#include <stdint.h>\n\n")
        f.write(f"constexpr int PET_FRAME_W = {FRAME_W};\n")
        f.write(f"constexpr int PET_FRAME_H = {FRAME_H};\n")
        f.write(f"constexpr int PET_TOTAL_FRAMES = {total_frames};\n")
        mask_bytes = (FRAME_W * FRAME_H + 7) // 8
        f.write(f"constexpr int PET_MASK_BYTES = {mask_bytes};\n\n")

        # Pixel storage: one flat array. Each frame is FRAME_W*FRAME_H uint16s.
        f.write("constexpr uint16_t PET_PIXELS[] = {\n")
        for state_frames in frames_per_state:
            for pixels, _mask in state_frames:
                line = ",".join(f"0x{p:04X}" for p in pixels)
                # 16 per line for readability
                tokens = pixels
                for i in range(0, len(tokens), 16):
                    f.write("  " + ",".join(f"0x{p:04X}" for p in tokens[i:i+16]) + ",\n")
        f.write("};\n\n")

        # Alpha masks
        f.write("constexpr uint8_t PET_MASKS[] = {\n")
        for state_frames in frames_per_state:
            for _pixels, mask in state_frames:
                for i in range(0, len(mask), 24):
                    f.write("  " + ",".join(f"0x{b:02X}" for b in mask[i:i+24]) + ",\n")
        f.write("};\n\n")

        # Animation table
        f.write("struct PetAnim { const char *name; uint16_t first_frame; uint16_t frame_count; };\n\n")
        f.write(f"constexpr int PET_ANIM_COUNT = {len(states)};\n")
        f.write("constexpr PetAnim PET_ANIMS[] = {\n")
        offset = 0
        for state, frames in zip(states, frames_per_state):
            f.write(f'  {{ "{state}", {offset}, {len(frames)} }},\n')
            offset += len(frames)
        f.write("};\n")

    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
