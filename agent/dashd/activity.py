"""Activity tracking — drives the push_loop's adaptive tick rate.

The agent's collectors (psutil per-process iteration, git porcelain
shellouts, IMAP polling, etc.) are the bulk of its CPU cost. There's no
point paying that cost every 2 s when *nobody* is watching:

  - The ESP device isn't connected (or never was).
  - No IPC client (Electron renderer, CLI tool) has the window visible.

This module owns the truth about whether any consumer is interested in
fresh state, so the push loop can drop from the configured fast tick
(default 2 s) to a much slower idle tick (default 30 s) and back the
moment something becomes active. The idle tick still runs so suggestions
and tray-icon status don't go stale, just at a fraction of the cost.

Per-client tracking (not a single global flag) is intentional — two
Electron windows could be open, one hidden one visible, and the hidden
one's "inactive" notification must not silence the visible one.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("dashd.activity")


class ActivityTracker:
    """Single source of truth for "is anyone consuming agent state?"

    Updated from two sides:
      - The IPC server, as clients connect / disconnect / signal visibility.
      - The runtime's usb_loop, as the device connects / disconnects.

    The push loop reads `has_active_consumer` once per tick to pick its
    next sleep duration.
    """

    def __init__(self) -> None:
        # Client IDs that have declared themselves "active" (window visible).
        # On disconnect the IPC server calls client_gone(cid) which prunes
        # any stale entries, so this set can't leak across reconnects.
        self._active_clients: set[int] = set()
        self._device_connected: bool = False
        self._listeners: list[Callable[[], None]] = []

    # ---- IPC server side --------------------------------------------------

    def client_set_active(self, cid: int, active: bool) -> None:
        """Mark a client active or idle. Called when the renderer signals
        a window show / hide via the `set_active` IPC cmd."""
        before = self.has_active_consumer
        if active:
            self._active_clients.add(cid)
        else:
            self._active_clients.discard(cid)
        if self.has_active_consumer != before:
            log.info("active consumer flipped → %s (active_clients=%d, "
                      "device=%s)",
                      self.has_active_consumer,
                      len(self._active_clients), self._device_connected)
            self._notify()

    def client_gone(self, cid: int) -> None:
        """Drop a client on disconnect. Safe to call even if the client
        never sent a set_active cmd."""
        before = self.has_active_consumer
        self._active_clients.discard(cid)
        if self.has_active_consumer != before:
            self._notify()

    # ---- USB / device side ------------------------------------------------

    def set_device_connected(self, connected: bool) -> None:
        before = self.has_active_consumer
        self._device_connected = connected
        if self.has_active_consumer != before:
            log.info("device-connected → %s (active=%s)",
                      connected, self.has_active_consumer)
            self._notify()

    # ---- Push loop side ---------------------------------------------------

    @property
    def has_active_consumer(self) -> bool:
        return self._device_connected or bool(self._active_clients)

    def on_change(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to transitions in has_active_consumer. Returns a
        function to unsubscribe. Used by the push loop to wake out of a
        long idle sleep the moment activity returns."""
        self._listeners.append(cb)
        def off() -> None:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass
        return off

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                log.exception("activity listener raised")
