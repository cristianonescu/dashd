"""Catalog parsing + slug/URL resolution."""
from __future__ import annotations

import httpx
import pytest

from dashd.pets import catalog


def test_slug_from_bare_slug():
    assert catalog.parse_slug_from_url("pixel-coder") == "pixel-coder"
    assert catalog.parse_slug_from_url("claw-d") == "claw-d"


def test_slug_from_gallery_url():
    assert catalog.parse_slug_from_url("https://codexpets.net/gallery/claw-d") == "claw-d"
    assert catalog.parse_slug_from_url("https://codexpets.net/pets/pixel-coder/") == "pixel-coder"


def test_slug_from_unknown_host_rejected():
    assert catalog.parse_slug_from_url("https://example.com/gallery/foo") is None


def test_slug_invalid_chars_rejected():
    assert catalog.parse_slug_from_url("PixelCoder") is None  # uppercase
    assert catalog.parse_slug_from_url("foo bar") is None     # space


def test_slug_to_name():
    assert catalog.slug_to_name("pixel-coder") == "Pixel Coder"
    assert catalog.slug_to_name("hoop-duck") == "Hoop Duck"


def test_lookup_returns_entry():
    e = catalog.lookup("claw-d")
    assert e is not None
    assert e.slug == "claw-d"
    assert "claw-d" in e.download_url
    assert "claw-d" in e.gallery_url


def test_lookup_unknown_url_is_none():
    assert catalog.lookup("not a url") is None


def test_entries_from_sitemap_dedupes_and_filters():
    xml = b"""<?xml version="1.0"?>
    <urlset>
      <url><loc>https://codexpets.net/</loc></url>
      <url><loc>https://codexpets.net/gallery/claw-d</loc></url>
      <url><loc>https://codexpets.net/gallery/claw-d/</loc></url>
      <url><loc>https://codexpets.net/pets/pixel-coder</loc></url>
      <url><loc>https://codexpets.net/about</loc></url>
    </urlset>""".decode()
    entries = list(catalog._entries_from_sitemap_text(xml))
    slugs = [e.slug for e in entries]
    # Both `claw-d` URLs collapse via the caller's dedup, but the iterator
    # yields them; the public fetch_catalog dedupes — verify there:
    assert "claw-d" in slugs
    assert "pixel-coder" in slugs


@pytest.mark.asyncio
async def test_fetch_catalog_dedupes(monkeypatch):
    xml = """<?xml version="1.0"?>
    <urlset>
      <url><loc>https://codexpets.net/gallery/claw-d</loc></url>
      <url><loc>https://codexpets.net/gallery/claw-d/</loc></url>
      <url><loc>https://codexpets.net/gallery/hoop-duck</loc></url>
    </urlset>"""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        entries = await catalog.fetch_catalog(client=client)
    slugs = [e.slug for e in entries]
    assert slugs == ["claw-d", "hoop-duck"]
