"""macOS iMessage unread count via read-only SQLite on ~/Library/Messages/chat.db.

Requires **Full Disk Access** for the terminal / IDE that runs the agent
(System Settings → Privacy & Security → Full Disk Access). Without it,
SQLite returns `unable to open database file` and the collector reports a
one-time clear log line pointing the user at the setting, then keeps quiet.

Non-macOS platforms: returns None unconditionally.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from dashd.collectors.base import Collector

log = logging.getLogger("dashd.imessage")

DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"


class IMessageCollector(Collector):
    key = "messages.imessage"

    def __init__(self, enabled: bool = True, db_path: Path | None = None) -> None:
        super().__init__(enabled)
        self.db_path = db_path or DB_PATH
        self._warned_permissions = False

    async def collect(self) -> dict[str, Any] | None:
        if sys.platform != "darwin":
            return None
        if not self.db_path.is_file():
            # Messages.app has never been opened on this account.
            return {"unread": 0}

        # mode=ro + immutable=1: read-only, doesn't take any locks that could
        # interfere with Messages.app.
        uri = f"file:{self.db_path}?mode=ro&immutable=1"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.OperationalError as e:
            if not self._warned_permissions:
                self._warned_permissions = True
                log.warning(
                    "iMessage: cannot open %s (%s). "
                    "Grant Full Disk Access to your terminal/IDE in "
                    "System Settings → Privacy & Security → Full Disk Access, "
                    "then restart the agent.",
                    self.db_path, e,
                )
            return {"unread": -1}

        try:
            cur = con.cursor()
            # Incoming messages that have not been read. Matches the Messages.app
            # badge: all-time, not date-filtered.
            (count,) = cur.execute(
                "SELECT COUNT(*) FROM message WHERE is_read = 0 AND is_from_me = 0"
            ).fetchone()
            return {"unread": int(count)}
        except sqlite3.Error as e:
            log.warning("iMessage query failed: %s", e)
            return {"unread": -1}
        finally:
            con.close()
