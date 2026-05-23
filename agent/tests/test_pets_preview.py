"""Pet preview: default bundled + downloaded path + cache reuse."""
from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from PIL import Image

from dashd.pets import preview


def _make_zip() -> bytes:
    sheet = Image.new("RGBA", (192 * 8, 208 * 9), (0, 0, 0, 0))
    pad = 40
    for r in range(9):
        for c in range(8):
            cell = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
            for y in range(pad, 208 - pad):
                for x in range(pad, 192 - pad):
                    cell.putpixel((x, y), (30 + r * 15, 80 + c * 10, 200, 255))
            sheet.paste(cell, (c * 192, r * 208), cell)
    sb = io.BytesIO(); sheet.save(sb, format="WEBP")

    manifest = {"id": "tt", "displayName": "Tiny T",
                "creator": "tester",
                "grid": {"rows": 9, "cols": 8},
                "states": ["idle", "wave", "jump", "run", "sit",
                           "blink", "yawn", "dance", "sad"]}
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("pet.json", json.dumps(manifest))
        zf.writestr("spritesheet.webp", sb.getvalue())
    return z.getvalue()


@pytest.mark.asyncio
async def test_default_preview_is_bundled():
    p = await preview.get_preview("default")
    assert p.slug == "default"
    assert p.name == "Claw'd"
    assert p.mime == "image/webp"
    assert p.image_bytes[:4] == b"RIFF"   # WebP magic
    assert p.rows == 9 and p.cols == 8
    assert p.states[0] == "idle"
    assert p.creator == "krrsantan"


@pytest.mark.asyncio
async def test_download_then_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("dashd.pets.CACHE_DIR", tmp_path / "pets")
    monkeypatch.setattr("dashd.pets.preview.CACHE_DIR", tmp_path / "pets")
    zip_bytes = _make_zip()

    def handler(_req): return httpx.Response(200, content=zip_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = await preview.get_preview("tt", client=client)
    assert p.slug == "tt"
    assert p.name == "Tiny T"
    assert p.image_bytes[:4] == b"RIFF"
    assert p.rows == 9
    assert (tmp_path / "pets" / "tt.preview.webp").is_file()
    assert (tmp_path / "pets" / "tt.preview.json").is_file()


@pytest.mark.asyncio
async def test_cache_short_circuits_network(tmp_path, monkeypatch):
    monkeypatch.setattr("dashd.pets.CACHE_DIR", tmp_path / "pets")
    monkeypatch.setattr("dashd.pets.preview.CACHE_DIR", tmp_path / "pets")
    cache = tmp_path / "pets"; cache.mkdir(parents=True)
    sheet = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    buf = io.BytesIO(); sheet.save(buf, format="WEBP")
    (cache / "kached.preview.webp").write_bytes(buf.getvalue())
    (cache / "kached.preview.json").write_text(json.dumps({
        "name": "Kached", "rows": 9, "cols": 8,
        "states": ["idle"], "creator": "me", "source_url": "x",
    }))

    def fail(_req): raise AssertionError("network was used")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        p = await preview.get_preview("kached", client=client)
    assert p.name == "Kached" and p.creator == "me"


@pytest.mark.asyncio
async def test_unknown_url_rejected():
    with pytest.raises(ValueError):
        await preview.get_preview("https://elsewhere.example/foo")
