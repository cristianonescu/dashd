"""Transport-link interface.

A `Link` is the device connection seen by the rest of the agent. There are
two implementations: `SerialLink` (USB-CDC) and `BleLink` (Bluetooth LE).
The runtime's `link_loop` is written against this interface so neither the
collectors nor the push loop know or care which transport is in use.

This is a `typing.Protocol` — structural, zero runtime cost. An object is a
`Link` if it has these members; no explicit subclassing is required.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Link(Protocol):
    """The device transport contract.

    All implementations are single-writer: callers must serialize `send`
    through one task (the runtime uses a single TX queue for exactly this).
    """

    @property
    def connected(self) -> bool:
        """True when the link is open and usable."""
        ...

    def connect(self, max_backoff: float = 30.0,
                should_stop: Callable[[], bool] | None = None) -> None:
        """Block until connected, retrying with backoff. `should_stop`, if
        given, is polled so a shutdown can interrupt the retry loop; callers
        must check `connected` afterwards."""
        ...

    def close(self) -> None:
        """Close the link. Safe to call when already closed."""
        ...

    def send(self, msg: dict[str, Any]) -> bool:
        """Send one message. Returns False on failure (caller may reconnect)."""
        ...

    def read_events(self) -> list[dict[str, Any]]:
        """Drain any pending inbound messages from the device."""
        ...
