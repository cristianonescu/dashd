"""WhatsApp unread count — best-effort, currently always null.

WhatsApp has no personal/desktop API. The on-screen dock badge is private to
the WhatsApp process; reading the macOS NotificationCenter SQLite is unreliable
and moves between OS versions; WhatsApp Web automation violates ToS and breaks
on UI changes.

The honest move for v1 is to expose the slot in the protocol so the firmware
renders it cleanly, but always report null. If a reliable read appears (e.g.
an official WhatsApp Business API endpoint exposed locally), it'll land here.
See docs/whatsapp.md.
"""
from __future__ import annotations

from typing import Any

from dashd.collectors.base import Collector


class WhatsAppCollector(Collector):
    key = "messages.whatsapp"

    async def collect(self) -> dict[str, Any] | None:
        return None
