"""GPU utilization collector — cross-platform, best-effort.

Detection chain (first one that yields data wins):

  1. **macOS** (Apple Silicon + Intel) — `ioreg -l -w 0 -r -c IOAccelerator`.
     The `PerformanceStatistics` dict exposes `Device Utilization %`,
     `In use system memory`, `Alloc system memory` without sudo. The
     device name comes from `system_profiler SPDisplaysDataType`.
     Apple Silicon has unified memory, so we surface `vram_used_mb`
     (currently allocated to GPU) with `vram_total_mb = None`.
  2. **NVIDIA (any OS)** — `nvidia-smi --query-gpu=…` CSV. Picks the
     first GPU (multi-GPU systems show `(+N more)`).
  3. **Linux AMD** — `/sys/class/drm/card*/device/gpu_busy_percent` for
     utilization; VRAM from `mem_info_vram_used/_total`. Names from
     `/sys/class/drm/card*/device/uevent`.
  4. **Linux Intel** — skipped for v0.1.12 (intel_gpu_top requires root
     and a kernel CAP). Returns `{available: false, reason: "intel_gpu_top
     unavailable"}`.

The collector is INTENTIONALLY lightweight: each backend is wrapped in
its own try/except, all exceptions are absorbed and turned into a
`available=false` reason so we never crash the agent over a GPU probe.
"""
from __future__ import annotations

import logging
import platform
import plistlib
import shutil
import subprocess
from glob import glob
from pathlib import Path
from typing import Any

from dashd.collectors.base import Collector

log = logging.getLogger("dashd.collectors.gpu")


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


# ── macOS ─────────────────────────────────────────────────────────────

def _macos_device_name() -> str | None:
    """Single-shot name probe via system_profiler. Slow (~150 ms) so
    we only run it once and cache. Returns just the first GPU's chipset
    (e.g. "Apple M5 Max")."""
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=2.0,
        )
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            line = line.strip()
            if line.startswith("Chipset Model:"):
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _find_perfstats(node: dict) -> dict | None:
    """Walk a parsed `ioreg -a` plist tree and return the first
    PerformanceStatistics dict we find. ioreg -a output is a list of
    IO-registry objects; each may have nested `IORegistryEntryChildren`."""
    if not isinstance(node, dict):
        return None
    ps = node.get("PerformanceStatistics")
    if isinstance(ps, dict):
        return ps
    children = node.get("IORegistryEntryChildren") or []
    for child in children:
        found = _find_perfstats(child)
        if found is not None:
            return found
    return None


def _macos_collect(cached_name: str | None) -> dict[str, Any]:
    """Read GPU stats from IOAccelerator's PerformanceStatistics dict.
    Works on Apple Silicon AND Intel Macs (the ioreg class exposes the
    same key on both). No sudo required. Uses `ioreg -a` for binary-plist
    XML output we can parse with plistlib — more robust than scraping
    the human-readable form, which has split key-value pairs across
    lines on some macOS versions."""
    try:
        out = subprocess.run(
            ["ioreg", "-a", "-r", "-c", "IOAccelerator"],
            capture_output=True, timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _unavailable("macOS did not expose GPU statistics")
    if out.returncode != 0 or not out.stdout:
        return _unavailable("macOS did not expose GPU statistics")

    try:
        # ioreg -a wraps the registry in an XML <array>; parse the
        # whole document and walk for the first PerformanceStatistics.
        parsed = plistlib.loads(out.stdout)
    except (plistlib.InvalidFileException, ValueError, Exception):
        return _unavailable("could not parse ioreg output")

    ps = None
    if isinstance(parsed, list):
        for item in parsed:
            ps = _find_perfstats(item)
            if ps is not None:
                break
    elif isinstance(parsed, dict):
        ps = _find_perfstats(parsed)
    if ps is None:
        return _unavailable("macOS did not expose GPU statistics")

    def _i(key: str) -> int | None:
        v = ps.get(key)
        return int(v) if isinstance(v, (int, float)) else None

    util = _i("Device Utilization %")
    if util is None:
        util = _i("Renderer Utilization %")  # discrete-GPU fallback
    in_use_bytes = _i("In use system memory")
    alloc_bytes  = _i("Alloc system memory")
    if util is None and in_use_bytes is None:
        return _unavailable("macOS did not expose GPU statistics")

    # Prefer "Alloc system memory" (driver-allocated VRAM, includes
    # buffers etc.) — matches what Activity Monitor's GPU column shows.
    used_bytes = alloc_bytes if alloc_bytes is not None else (in_use_bytes or 0)
    used_mb = used_bytes // (1024 * 1024)

    return {
        "available": True,
        "vendor": "Apple",
        "name": cached_name or "GPU",
        "util_pct": util,
        # Unified memory: there's no fixed VRAM ceiling, so we omit a
        # total. The firmware page renders "Used 1234 MB (unified)".
        "vram_used_mb": used_mb if used_mb > 0 else None,
        "vram_total_mb": None,
        "temp_c": None,
        "power_w": None,
        "count": 1,
    }


# ── NVIDIA (any OS) ───────────────────────────────────────────────────

_NVIDIA_FIELDS = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"


def _nvidia_collect() -> dict[str, Any]:
    """Shell out to nvidia-smi if present. Returns available=false when
    the binary is missing — we treat missing as a non-error."""
    if not shutil.which("nvidia-smi"):
        return _unavailable("NVIDIA driver tools are not installed")
    try:
        out = subprocess.run(
            ["nvidia-smi",
             f"--query-gpu={_NVIDIA_FIELDS}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _unavailable("could not run nvidia-smi")
    if out.returncode != 0 or not out.stdout.strip():
        return _unavailable("nvidia-smi reported no GPUs")

    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if not lines:
        return _unavailable("nvidia-smi returned no rows")

    # First GPU is the headline; remaining are reported via `count`.
    fields = [f.strip() for f in lines[0].split(",")]
    if len(fields) < 6:
        return _unavailable("nvidia-smi output not recognized")
    def _i(s: str) -> int | None:
        try: return int(round(float(s)))
        except ValueError: return None

    return {
        "available": True,
        "vendor": "NVIDIA",
        "name": fields[0],
        "util_pct": _i(fields[1]),
        "vram_used_mb": _i(fields[2]),
        "vram_total_mb": _i(fields[3]),
        "temp_c": _i(fields[4]),
        "power_w": _i(fields[5]),
        "count": len(lines),
    }


# ── Linux AMD via sysfs ───────────────────────────────────────────────

def _linux_amd_collect() -> dict[str, Any]:
    """Read /sys/class/drm/card*/device for AMD GPU stats. No subprocess
    cost, very fast. We pick the first card with `gpu_busy_percent`."""
    cards = sorted(glob("/sys/class/drm/card[0-9]"))
    for card in cards:
        busy_path = Path(card) / "device" / "gpu_busy_percent"
        if not busy_path.exists():
            continue
        try:
            util = int(busy_path.read_text().strip())
        except (OSError, ValueError):
            continue
        # Best-effort vendor name from PCI device name.
        name = "AMD GPU"
        vram_used = vram_total = None
        try:
            for key, path in (
                ("vram_used",  Path(card) / "device" / "mem_info_vram_used"),
                ("vram_total", Path(card) / "device" / "mem_info_vram_total"),
            ):
                if path.exists():
                    v = int(path.read_text().strip()) // (1024 * 1024)
                    if key == "vram_used":  vram_used = v
                    if key == "vram_total": vram_total = v
        except (OSError, ValueError):
            pass
        return {
            "available": True,
            "vendor": "AMD",
            "name": name,
            "util_pct": util,
            "vram_used_mb": vram_used,
            "vram_total_mb": vram_total,
            "temp_c": None,
            "power_w": None,
            "count": 1,
        }
    return _unavailable("no AMD GPU detected")


# ── Top-level collector ───────────────────────────────────────────────

class GpuCollector(Collector):
    key = "gpu"

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(enabled)
        # Cache the macOS device name once — it's slow to fetch.
        self._macos_name: str | None = None
        if platform.system() == "Darwin":
            self._macos_name = _macos_device_name()

    async def collect(self) -> dict[str, Any] | None:
        sysname = platform.system()
        # Try NVIDIA first regardless of OS — it's the highest-fidelity
        # source when present.
        nv = _nvidia_collect()
        if nv.get("available"):
            return nv
        if sysname == "Darwin":
            return _macos_collect(self._macos_name)
        if sysname == "Linux":
            amd = _linux_amd_collect()
            if amd.get("available"):
                return amd
            return _unavailable("no supported GPU detected")
        # Windows non-NVIDIA: punt for v0.1.12.
        return _unavailable("GPU monitoring is not available on this system")
