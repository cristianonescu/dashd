"""In-process pub/sub bus.

The aggregator publishes state frames here; both the USB transport and the IPC
server subscribe and forward to their own consumers. Keeps the pipeline core
unaware of how data leaves the process.

Backpressure: each subscriber gets a bounded asyncio.Queue. If a slow
subscriber fills it, the oldest item is dropped — we'd rather show a fresh
frame than block the producer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("dashd.bus")


class Bus:
    def __init__(self, queue_size: int = 8) -> None:
        self._queue_size = queue_size
        self._subs: list[asyncio.Queue[dict[str, Any]]] = []
        # Sticky messages — keyed by an arbitrary string the publisher
        # picks. The most recent value per key is replayed to any new
        # subscriber so late joiners don't miss one-shot transitions
        # (e.g. `agent_status` fires when the device link comes up;
        # the Electron IPC client typically connects AFTER that has
        # already happened — without replay, the UI never learns the
        # device is connected until an unplug/replug forces a fresh
        # transition).
        self._sticky: dict[str, dict[str, Any]] = {}

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._queue_size)
        # Replay sticky messages first so the subscriber sees them
        # before any newly-published frames.
        for msg in self._sticky.values():
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                break
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    def publish(self, msg: dict[str, Any], *, sticky_key: str | None = None) -> None:
        # If `sticky_key` is set, remember the latest value under that
        # key so it gets replayed to future subscribers (overwriting any
        # previous sticky under the same key).
        if sticky_key is not None:
            self._sticky[sticky_key] = msg
        # Best-effort fan-out. On full queue, drop the oldest and enqueue this one.
        for q in self._subs:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Race with another producer; just drop this frame for this subscriber.
                pass

    def clear_sticky(self, sticky_key: str | None = None) -> None:
        """Drop the remembered value for `sticky_key`, or all sticky
        values when called with no argument. Primarily for tests."""
        if sticky_key is None:
            self._sticky.clear()
        else:
            self._sticky.pop(sticky_key, None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
