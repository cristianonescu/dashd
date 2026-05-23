"""Pet catalog + downloader + sprite → .dpet converter.

This package handles everything between codexpets.net and the on-device
binary the firmware reads from LittleFS.

  catalog.py     — enumerate available pets (via sitemap.xml) + resolve
                   per-pet URLs (slug → download URL + preview URL)
  downloader.py  — fetch the per-pet ZIP and unpack pet.json + spritesheet
  converter.py   — sprite-sheet → packed .dpet binary at the on-device size

The .dpet container is the same layout the firmware reads (RGB565 pixels
+ 1-bit alpha mask + an animation index). Identical encoding to the
embedded Claw'd, just streamed instead of compiled in.
"""

DPET_MAGIC = b"DPET"
DPET_VERSION = 1

# On-device sprite target (matches PET_FRAME_W/H in firmware/include/default_pet.h).
FRAME_W = 48
FRAME_H = 48

# Cache root — one .dpet per pet slug, plus a manifest cache.
from pathlib import Path
CACHE_DIR = Path.home() / ".config" / "dashd" / "pets"
