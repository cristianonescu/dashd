"""Per-app RSS history + memory-leak detection.

Each aggregated app row (post `_app_key` aggregation) gets its own short
rolling buffer of RSS samples. When the recent slope crosses a threshold
we flag the app and the suggestions engine surfaces it.

Stored on the agent process — not persisted. Resets whenever the agent
restarts; that's fine, it's a "what's growing right now?" detector, not
a long-term audit log.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Leak:
    name: str        # canonical app label (key from _app_key)
    delta_mb: int    # how much RSS grew across the window
    window_min: float

    def to_dict(self) -> dict:
        return {"name": self.name, "delta_mb": self.delta_mb,
                "window_min": round(self.window_min, 1)}


class ProcessHistory:
    """Rolling-window RSS history per app key.

    Keeps the last `max_samples` (timestamp, rss_mb) tuples per key. Detects
    leaks by comparing the oldest in-window sample to the newest: if RSS
    grew by ≥ `delta_threshold_mb` over the window, the key is flagged.
    """

    def __init__(self,
                 max_samples: int = 60,        # ~2 min at a 2 s tick
                 window_sec: float = 300.0,    # 5-minute leak window
                 delta_threshold_mb: int = 200) -> None:
        self.max_samples = max_samples
        self.window_sec = window_sec
        self.delta_threshold_mb = delta_threshold_mb
        self._buf: dict[str, deque[tuple[float, int]]] = {}

    def record(self, key: str, rss_mb: int, now: float | None = None) -> None:
        if not key:
            return
        if now is None:
            now = time.time()
        dq = self._buf.get(key)
        if dq is None:
            dq = deque(maxlen=self.max_samples)
            self._buf[key] = dq
        dq.append((now, rss_mb))

    def prune(self, live_keys: set[str]) -> None:
        """Drop history for apps that no longer appear in the snapshot.
        Cheap and bounds memory if helper processes churn names."""
        dead = [k for k in self._buf if k not in live_keys]
        for k in dead:
            self._buf.pop(k, None)

    def worst_leak(self, now: float | None = None) -> Leak | None:
        """Return the app with the largest in-window RSS growth, if any
        crossed the threshold."""
        if now is None:
            now = time.time()
        cutoff = now - self.window_sec
        best: Leak | None = None
        for key, dq in self._buf.items():
            # Find the oldest sample still inside the window.
            anchor: tuple[float, int] | None = None
            for ts, rss in dq:
                if ts >= cutoff:
                    anchor = (ts, rss)
                    break
            if anchor is None:
                continue
            latest_ts, latest_rss = dq[-1]
            if latest_ts <= anchor[0]:
                continue
            delta = latest_rss - anchor[1]
            if delta < self.delta_threshold_mb:
                continue
            window_min = (latest_ts - anchor[0]) / 60.0
            cand = Leak(name=key, delta_mb=int(delta), window_min=window_min)
            if best is None or cand.delta_mb > best.delta_mb:
                best = cand
        return best
