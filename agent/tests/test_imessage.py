"""iMessage collector platform-gating and graceful permission failure."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from dashd.collectors.imessage_macos import IMessageCollector


@pytest.mark.asyncio
async def test_returns_none_on_non_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    c = IMessageCollector(enabled=True)
    assert await c.collect() is None


@pytest.mark.asyncio
async def test_returns_zero_when_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    c = IMessageCollector(enabled=True, db_path=tmp_path / "does-not-exist.db")
    assert await c.collect() == {"unread": 0}


@pytest.mark.asyncio
async def test_counts_unread_from_real_schema(monkeypatch, tmp_path):
    # Build a tiny chat.db with the same columns we query.
    db = tmp_path / "chat.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            is_read INTEGER,
            is_from_me INTEGER,
            date INTEGER,
            text TEXT
        );
        INSERT INTO message (is_read, is_from_me, date, text) VALUES
            (0, 0, 1, 'unread incoming 1'),
            (0, 0, 2, 'unread incoming 2'),
            (1, 0, 3, 'already read'),
            (0, 1, 4, 'outgoing pending');
    """)
    con.commit(); con.close()

    monkeypatch.setattr(sys, "platform", "darwin")
    c = IMessageCollector(enabled=True, db_path=db)
    assert await c.collect() == {"unread": 2}


@pytest.mark.asyncio
async def test_permission_error_returns_sentinel(monkeypatch, tmp_path, caplog):
    """When SQLite can't open the file (no FDA), we report -1 and log once."""
    monkeypatch.setattr(sys, "platform", "darwin")
    db = tmp_path / "chat.db"
    db.write_bytes(b"")  # exists but unreadable as sqlite
    # Force OperationalError by pointing to a chmod 000 file.
    db.chmod(0o000)
    try:
        c = IMessageCollector(enabled=True, db_path=db)
        with caplog.at_level("WARNING", logger="dashd.imessage"):
            r = await c.collect()
        assert r == {"unread": -1}
        assert "Full Disk Access" in caplog.text
        # Second call must not double-log.
        caplog.clear()
        with caplog.at_level("WARNING", logger="dashd.imessage"):
            r2 = await c.collect()
        assert r2 == {"unread": -1}
        assert "Full Disk Access" not in caplog.text
    finally:
        db.chmod(0o644)
