"""codexpets.net catalog enumeration.

The site has no JSON API. The reliable enumeration paths we can use:

  1. /sitemap.xml — lists every /pets/<slug> + /gallery/<slug> URL.
     Best coverage (all ~842 pets when it works).
  2. /api/gallery-pets/<slug>/download — direct ZIP download we already
     confirmed works end-to-end.

This module's only job is "give me a list of {slug, name, url}" — and
yes, do it lazily so the UI doesn't block on first open.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import httpx

log = logging.getLogger("dashd.pets.catalog")

BASE_URL = "https://codexpets.net"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
DOWNLOAD_URL_TEMPLATE = f"{BASE_URL}/api/gallery-pets/{{slug}}/download"
GALLERY_URL_TEMPLATE = f"{BASE_URL}/gallery/{{slug}}"


@dataclass(frozen=True)
class PetEntry:
    slug: str
    name: str         # title-cased slug if we don't have a real name yet
    gallery_url: str  # human-readable page
    download_url: str # direct ZIP API endpoint


# Slugs are lowercase letters/digits with hyphens.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Locations the sitemap might surface a pet at — we accept any of them.
_PET_PATH_RES = [
    re.compile(r"^/gallery/([a-z0-9-]+)/?$"),
    re.compile(r"^/pets/([a-z0-9-]+)/?$"),
]


def parse_slug_from_url(url: str) -> str | None:
    """Try to extract a pet slug from any URL the user might paste."""
    if not url:
        return None
    url = url.strip()
    if _SLUG_RE.match(url):  # bare slug
        return url
    try:
        parsed = urlparse(url if "//" in url else f"https://{url}")
    except ValueError:
        return None
    if parsed.netloc and "codexpets" not in parsed.netloc.lower():
        return None
    path = parsed.path or ""
    for r in _PET_PATH_RES:
        m = r.match(path)
        if m:
            return m.group(1)
    return None


def slug_to_name(slug: str) -> str:
    """Fallback display name: 'pixel-coder' → 'Pixel Coder'."""
    return " ".join(p.capitalize() for p in slug.replace("_", "-").split("-"))


def _entries_from_sitemap_text(text: str) -> Iterable[PetEntry]:
    """Yield PetEntry rows from raw sitemap XML.

    We don't pull a full XML parser in for two tags — a regex over
    `<loc>...</loc>` is enough.
    """
    for m in re.finditer(r"<loc>([^<]+)</loc>", text):
        url = m.group(1).strip()
        slug = parse_slug_from_url(url)
        if not slug:
            continue
        yield PetEntry(
            slug=slug,
            name=slug_to_name(slug),
            gallery_url=GALLERY_URL_TEMPLATE.format(slug=slug),
            download_url=DOWNLOAD_URL_TEMPLATE.format(slug=slug),
        )


async def fetch_catalog(client: httpx.AsyncClient | None = None) -> list[PetEntry]:
    """Fetch + parse the sitemap. Returns deduped PetEntry list.

    Raises httpx.HTTPError on failure — caller decides whether to fall
    back to a smaller catalog or show an error.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        r = await client.get(SITEMAP_URL)
        r.raise_for_status()
        seen: set[str] = set()
        out: list[PetEntry] = []
        for e in _entries_from_sitemap_text(r.text):
            if e.slug in seen:
                continue
            seen.add(e.slug)
            out.append(e)
        log.info("catalog: %d unique pets from sitemap", len(out))
        return out
    finally:
        if own_client:
            await client.aclose()


def lookup(slug_or_url: str) -> PetEntry | None:
    """Resolve a slug or URL into a downloadable PetEntry without hitting
    the network. The caller is responsible for verifying that the slug
    actually exists by attempting a download."""
    slug = parse_slug_from_url(slug_or_url)
    if not slug:
        return None
    return PetEntry(
        slug=slug,
        name=slug_to_name(slug),
        gallery_url=GALLERY_URL_TEMPLATE.format(slug=slug),
        download_url=DOWNLOAD_URL_TEMPLATE.format(slug=slug),
    )
