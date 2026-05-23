"""Per-app RSS history + leak detection."""
from __future__ import annotations

from dashd.proc_history import ProcessHistory


def test_empty_history_has_no_leak():
    h = ProcessHistory()
    assert h.worst_leak(now=1000.0) is None


def test_below_threshold_does_not_leak():
    h = ProcessHistory(window_sec=120.0, delta_threshold_mb=200)
    h.record("Chrome", 500, now=0.0)
    h.record("Chrome", 599, now=60.0)
    assert h.worst_leak(now=60.0) is None  # +99 MB — under 200


def test_growth_above_threshold_flags():
    h = ProcessHistory(window_sec=120.0, delta_threshold_mb=200)
    h.record("Chrome", 500, now=0.0)
    h.record("Chrome", 950, now=90.0)
    leak = h.worst_leak(now=90.0)
    assert leak is not None
    assert leak.name == "Chrome"
    assert leak.delta_mb == 450
    assert 1.0 < leak.window_min < 2.0


def test_picks_worst_when_multiple():
    h = ProcessHistory(window_sec=180.0, delta_threshold_mb=100)
    h.record("Chrome", 500, now=0.0); h.record("Chrome", 700, now=60.0)
    h.record("Slack",  400, now=0.0); h.record("Slack",  800, now=60.0)
    leak = h.worst_leak(now=60.0)
    assert leak is not None and leak.name == "Slack"  # +400 vs +200


def test_window_eviction():
    h = ProcessHistory(window_sec=60.0, delta_threshold_mb=50)
    h.record("Chrome", 100, now=0.0)
    # The "now" passed to worst_leak is way past the window:
    h.record("Chrome", 500, now=1000.0)
    # Only one sample inside the window → no comparable anchor → no leak.
    assert h.worst_leak(now=1000.0) is None


def test_prune_drops_dead_keys():
    h = ProcessHistory()
    h.record("Chrome", 100); h.record("Slack", 50)
    h.prune({"Chrome"})
    # Slack history gone; Chrome still tracked
    assert "Slack" not in h._buf
    assert "Chrome" in h._buf
