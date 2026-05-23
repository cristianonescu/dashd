"""Device transports.

`Link` is the transport interface; `SerialLink` is the USB-CDC implementation
and `BleLink` the Bluetooth LE one. `BleLink` is imported lazily — a missing
`bleak` install must not break the USB-only path.
"""
from __future__ import annotations

from dashd.transport.base import Link
from dashd.transport.serial_link import SerialLink, find_port

__all__ = ["Link", "SerialLink", "find_port", "BleLink"]


def __getattr__(name: str):
    # Lazy: only import ble_link (and therefore bleak) when BleLink is used.
    if name == "BleLink":
        from dashd.transport.ble_link import BleLink
        return BleLink
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
