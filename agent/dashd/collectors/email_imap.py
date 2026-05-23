"""IMAP unread count for the configured INBOX.

Uses aioimaplib for non-blocking IO. Password comes from the
DASHD_EMAIL_PASSWORD env var (never config.toml). One round-trip per tick:
LOGIN → STATUS INBOX UNSEEN → LOGOUT. We open a fresh connection each time —
simpler and avoids IMAP IDLE/keepalive logic for what's effectively a poll.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import aioimaplib

from dashd.collectors.base import Collector

log = logging.getLogger("dashd.email")

CONNECT_TIMEOUT = 10.0
_UNSEEN_RE = re.compile(rb"UNSEEN\s+(\d+)", re.IGNORECASE)


class EmailCollector(Collector):
    key = "messages.email"

    def __init__(
        self,
        enabled: bool = True,
        host: str = "",
        port: int = 993,
        username: str = "",
        password: str | None = None,
        mailbox: str = "INBOX",
    ) -> None:
        super().__init__(enabled)
        self.host = host
        self.port = port
        self.username = username
        self._password = password or os.environ.get("DASHD_EMAIL_PASSWORD") or ""
        self.mailbox = mailbox

    async def collect(self) -> dict[str, Any] | None:
        if not (self.host and self.username and self._password):
            return None

        client = aioimaplib.IMAP4_SSL(host=self.host, port=self.port, timeout=CONNECT_TIMEOUT)
        try:
            await asyncio.wait_for(client.wait_hello_from_server(), timeout=CONNECT_TIMEOUT)
            r = await client.login(self.username, self._password)
            if r.result != "OK":
                log.warning("imap login failed: %s", r.lines)
                return {"unread": -1}
            r = await client.select(self.mailbox)
            if r.result != "OK":
                return {"unread": -1}
            r = await client.search("UNSEEN")
            unread = -1
            if r.result == "OK" and r.lines:
                # SEARCH response: ["1 2 3 ...", "Search completed."] — count IDs in the first line.
                ids = r.lines[0].split()
                unread = len(ids)
            try:
                await client.logout()
            except Exception:
                pass
            return {"unread": unread}
        except (asyncio.TimeoutError, aioimaplib.Abort, OSError) as e:
            log.warning("imap collect failed: %s", e)
            return {"unread": -1}
