"""Bundle → .dpet round-trip + header inspection."""
from __future__ import annotations

import io
import json

from PIL import Image
import pytest

from dashd.pets import DPET_MAGIC, DPET_VERSION
from dashd.pets.converter import convert, parse_header
from dashd.pets.downloader import PetBundle


def _make_sheet(rows: int, cols: int, cell: int) -> bytes:
    """Synthesize a sprite sheet where every cell is a non-empty colored square."""
    sheet = Image.new("RGBA", (cols * cell, rows * cell), (0, 0, 0, 0))
    for r in range(rows):
        for c in range(cols):
            # Each cell: a centered solid square with alpha=255
            cell_im = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
            pad = cell // 4
            for y in range(pad, cell - pad):
                for x in range(pad, cell - pad):
                    cell_im.putpixel((x, y), (10 + r * 25, 30 + c * 25, 200, 255))
            sheet.paste(cell_im, (c * cell, r * cell), cell_im)
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def test_convert_header_round_trip():
    rows, cols = 3, 4
    bundle = PetBundle(
        slug="test-pet",
        manifest={
            "id": "test-pet",
            "grid": {"rows": rows, "cols": cols},
            "states": ["idle", "wave", "jump"],
        },
        spritesheet_bytes=_make_sheet(rows, cols, 64),
    )
    dpet = convert(bundle, frame_w=16, frame_h=16)
    raw = dpet.raw

    # Magic + version
    assert raw[:4] == DPET_MAGIC
    assert raw[4] == DPET_VERSION

    info = parse_header(raw)
    assert info["frame_w"] == 16
    assert info["frame_h"] == 16
    assert info["anim_count"] == 3
    assert info["frame_count"] == rows * cols  # all cells are non-empty

    # Per-state name + first-frame stitching is contiguous.
    states = info["states"]
    assert [s[0] for s in states] == ["idle", "wave", "jump"]
    assert states[0][1] == 0 and states[0][2] == cols
    assert states[1][1] == cols and states[1][2] == cols
    assert states[2][1] == 2 * cols and states[2][2] == cols

    # Total payload length matches the header.
    bytes_per_frame = 16 * 16 * 2 + ((16 * 16) + 7) // 8
    expected = info["frames_offset"] + info["frame_count"] * bytes_per_frame
    assert len(raw) == expected


def test_convert_skips_empty_cells():
    # Two-row sheet where the second row is entirely transparent.
    sheet_top = _make_sheet(1, 3, 32)
    empty = Image.new("RGBA", (3 * 32, 32), (0, 0, 0, 0))
    sheet_full = Image.new("RGBA", (3 * 32, 64), (0, 0, 0, 0))
    sheet_full.paste(Image.open(io.BytesIO(sheet_top)), (0, 0))
    sheet_full.paste(empty, (0, 32))
    buf = io.BytesIO(); sheet_full.save(buf, format="PNG")

    bundle = PetBundle(
        slug="t", manifest={"grid": {"rows": 2, "cols": 3}, "states": ["a", "b"]},
        spritesheet_bytes=buf.getvalue(),
    )
    dpet = convert(bundle, frame_w=16, frame_h=16)
    info = parse_header(dpet.raw)
    assert info["frame_count"] == 3  # only the first row had any pixels
    assert info["states"][0][2] == 3
    assert info["states"][1][2] == 0


def test_parse_header_rejects_bad_magic():
    with pytest.raises(ValueError):
        parse_header(b"XXXX" + b"\x00" * 30)
