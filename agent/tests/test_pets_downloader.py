"""ZIP download + unpacking."""
from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from dashd.pets.downloader import download_bundle


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_download_happy_path():
    sheet = b"\xff" * 64  # placeholder bytes; converter doesn't run here
    manifest = {"id": "claw-d", "displayName": "Claw'd"}
    zip_bytes = _make_zip({
        "pet.json": json.dumps(manifest).encode(),
        "spritesheet.webp": sheet,
    })

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        b = await download_bundle("https://example.com/x.zip", "claw-d", client=client)
    assert b.slug == "claw-d"
    assert b.manifest["displayName"] == "Claw'd"
    assert b.spritesheet_bytes == sheet


@pytest.mark.asyncio
async def test_download_missing_manifest():
    zip_bytes = _make_zip({"spritesheet.webp": b"x" * 8})

    def handler(req): return httpx.Response(200, content=zip_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="pet.json"):
            await download_bundle("https://x", "y", client=client)


@pytest.mark.asyncio
async def test_download_missing_spritesheet():
    zip_bytes = _make_zip({"pet.json": b'{"id":"x"}'})

    def handler(req): return httpx.Response(200, content=zip_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="spritesheet"):
            await download_bundle("https://x", "y", client=client)


@pytest.mark.asyncio
async def test_download_invalid_json():
    zip_bytes = _make_zip({
        "pet.json": b"{not valid json",
        "spritesheet.webp": b"x",
    })

    def handler(req): return httpx.Response(200, content=zip_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="not valid JSON"):
            await download_bundle("https://x", "y", client=client)
