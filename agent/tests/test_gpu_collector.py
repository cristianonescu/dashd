"""GPU collector — cross-platform best-effort detection."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from dashd.collectors.gpu import (
    GpuCollector, _macos_collect, _nvidia_collect, _linux_amd_collect,
)


def _proc(returncode=0, stdout="", stderr=""):
    """Build a CompletedProcess-shaped stub for `subprocess.run`."""
    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── nvidia-smi parse ──────────────────────────────────────────────────

def test_nvidia_collect_parses_csv_output():
    """A real-shaped nvidia-smi line maps to the expected fields."""
    csv = "NVIDIA GeForce RTX 4090, 42, 8192, 24576, 65, 220.50\n"
    with patch("dashd.collectors.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("dashd.collectors.gpu.subprocess.run", return_value=_proc(stdout=csv)):
        out = _nvidia_collect()
    assert out["available"] is True
    assert out["vendor"] == "NVIDIA"
    assert out["name"] == "NVIDIA GeForce RTX 4090"
    assert out["util_pct"] == 42
    assert out["vram_used_mb"] == 8192
    assert out["vram_total_mb"] == 24576
    assert out["temp_c"] == 65
    assert out["power_w"] == 220     # banker's rounding from 220.50
    assert out["count"] == 1


def test_nvidia_collect_multi_gpu_counts_rows():
    csv = ("NVIDIA RTX 4090, 30, 1000, 24576, 60, 200\n"
           "NVIDIA RTX 4080, 10,  500, 16384, 55, 150\n")
    with patch("dashd.collectors.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("dashd.collectors.gpu.subprocess.run", return_value=_proc(stdout=csv)):
        out = _nvidia_collect()
    assert out["available"] is True
    assert out["count"] == 2
    # Headline = first GPU.
    assert out["name"] == "NVIDIA RTX 4090"


def test_nvidia_collect_missing_binary_returns_unavailable():
    with patch("dashd.collectors.gpu.shutil.which", return_value=None):
        out = _nvidia_collect()
    assert out["available"] is False
    # Reason should be user-friendly, not "nvidia-smi not in PATH".
    assert "NVIDIA driver" in out["reason"]


def test_nvidia_collect_failed_subprocess_returns_unavailable():
    """e.g. nvidia-smi exists but driver isn't loaded."""
    with patch("dashd.collectors.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("dashd.collectors.gpu.subprocess.run",
               return_value=_proc(returncode=9, stdout="")):
        out = _nvidia_collect()
    assert out["available"] is False
    # No jargon like "rc=9" — friendly message instead.
    assert "rc=" not in out["reason"]
    assert "no GPUs" in out["reason"] or "nvidia-smi" in out["reason"]


# ── macOS ioreg parse ─────────────────────────────────────────────────

# `ioreg -a -r -c IOAccelerator` returns binary-plist XML. Build a
# realistic-shaped doc with the same keys our parser cares about.
import plistlib


def _ioreg_plist(perfstats: dict | None) -> bytes:
    """Synthesize an `ioreg -a` style array containing one
    IOAccelerator-shaped node."""
    if perfstats is None:
        node: dict = {"IOClass": "IOAccelerator"}
    else:
        node = {
            "IOClass": "IOAccelerator",
            "PerformanceStatistics": perfstats,
        }
    return plistlib.dumps([node])


def test_macos_collect_parses_real_ioreg_output():
    stats = {
        # Field set captured from a real Apple M5 Max box.
        "In use system memory (driver)": 0,
        "Alloc system memory": 2710470656,
        "Tiler Utilization %": 19,
        "Renderer Utilization %": 25,
        "Device Utilization %": 26,
        "In use system memory": 1672413184,
    }
    with patch("dashd.collectors.gpu.subprocess.run",
               return_value=_proc(stdout=_ioreg_plist(stats))):
        out = _macos_collect("Apple M5 Max")
    assert out["available"] is True
    assert out["vendor"] == "Apple"
    assert out["name"] == "Apple M5 Max"
    assert out["util_pct"] == 26
    # 2710470656 bytes / (1024*1024) = 2584 MB (integer division).
    assert out["vram_used_mb"] == 2584
    # Unified memory ⇒ no fixed total.
    assert out["vram_total_mb"] is None
    assert out["count"] == 1


def test_macos_collect_no_performance_stats_returns_unavailable():
    """The IOAccelerator class is present in the tree but missing the
    PerformanceStatistics dict (older Macs / virtualized GPUs)."""
    with patch("dashd.collectors.gpu.subprocess.run",
               return_value=_proc(stdout=_ioreg_plist(None))):
        out = _macos_collect("Apple M5 Max")
    assert out["available"] is False
    # User-facing reason should be friendly, not jargon.
    assert "did not expose" in out["reason"] or "no GPU" in out["reason"]


def test_macos_collect_subprocess_failure_returns_unavailable():
    with patch("dashd.collectors.gpu.subprocess.run",
               return_value=_proc(returncode=1)):
        out = _macos_collect(None)
    assert out["available"] is False
    # Friendly reason.
    assert "did not expose" in out["reason"]


def test_macos_collect_uses_renderer_util_when_device_util_missing():
    """Discrete-GPU systems don't populate Device Utilization but do
    populate Renderer Utilization — our parser falls back."""
    stats = {
        "Renderer Utilization %": 42,
        "In use system memory": 1073741824,   # 1 GiB
    }
    with patch("dashd.collectors.gpu.subprocess.run",
               return_value=_proc(stdout=_ioreg_plist(stats))):
        out = _macos_collect("GPU")
    assert out["util_pct"] == 42
    assert out["vram_used_mb"] == 1024


# ── Linux AMD sysfs ───────────────────────────────────────────────────

def test_linux_amd_collect_returns_unavailable_when_no_cards(tmp_path):
    """No /sys/class/drm/card* → available=false."""
    with patch("dashd.collectors.gpu.glob", return_value=[]):
        out = _linux_amd_collect()
    assert out["available"] is False


# ── Top-level dispatch ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collector_falls_back_to_unavailable_when_no_gpu_anywhere():
    """If NVIDIA + platform-native paths all return unavailable, the
    collector still returns a clean {available: false, reason: ...}
    dict — never None, never raises."""
    coll = GpuCollector(enabled=True)
    with patch("dashd.collectors.gpu._nvidia_collect",
               return_value={"available": False, "reason": "nvidia-smi not in PATH"}), \
         patch("dashd.collectors.gpu.platform.system", return_value="Linux"), \
         patch("dashd.collectors.gpu._linux_amd_collect",
               return_value={"available": False, "reason": "no AMD GPU under /sys/class/drm"}):
        result = await coll.collect()
    assert result["available"] is False
    assert "reason" in result


@pytest.mark.asyncio
async def test_collector_prefers_nvidia_over_native_when_both_present():
    """A Linux box with both NVIDIA and AMD GPUs should report NVIDIA
    (higher-fidelity / actively maintained CLI)."""
    coll = GpuCollector(enabled=True)
    nvidia_result = {"available": True, "vendor": "NVIDIA", "name": "RTX 4090",
                     "util_pct": 30, "vram_used_mb": 1024, "vram_total_mb": 24576,
                     "temp_c": 60, "power_w": 200, "count": 1}
    with patch("dashd.collectors.gpu._nvidia_collect", return_value=nvidia_result), \
         patch("dashd.collectors.gpu._linux_amd_collect") as amd_mock:
        result = await coll.collect()
    assert result["vendor"] == "NVIDIA"
    amd_mock.assert_not_called()
