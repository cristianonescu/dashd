"""IPC server: token gate, fan-out, cmd dispatch."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dashd.activity import ActivityTracker
from dashd.bus import Bus
from dashd.ipc_server import IPCServer, ensure_token


@pytest.mark.asyncio
async def test_token_file_is_persistent(tmp_path):
    p = tmp_path / "ipc.token"
    t1 = ensure_token(p)
    t2 = ensure_token(p)
    assert t1 == t2
    assert (p.stat().st_mode & 0o777) == 0o600


async def _connect(port: int):
    return await asyncio.open_connection("127.0.0.1", port)


@pytest.mark.asyncio
async def test_hello_required_and_fanout(tmp_path):
    bus = Bus()
    cmds: list[dict] = []

    async def handler(m): cmds.append(m)

    srv = IPCServer(bus, handler, port=0, token_path=tmp_path / "ipc.token")
    port = await srv.start()
    try:
        # Wrong token closes the connection.
        r, w = await _connect(port)
        w.write(b'{"type":"hello","token":"wrong"}\n'); await w.drain()
        line = await asyncio.wait_for(r.readline(), 2.0)
        assert json.loads(line)["ok"] is False
        w.close(); await w.wait_closed()

        # Correct token + subscribe fan-out.
        token = (tmp_path / "ipc.token").read_text().strip()
        r, w = await _connect(port)
        w.write(json.dumps({"type": "hello", "token": token}).encode() + b"\n")
        await w.drain()
        ack = json.loads(await asyncio.wait_for(r.readline(), 2.0))
        assert ack["ok"] is True

        # Publish a state; the client should receive it.
        await asyncio.sleep(0.05)
        bus.publish({"type": "state", "ts": 1, "system": {"ram_pct": 42}})
        msg = json.loads(await asyncio.wait_for(r.readline(), 2.0))
        assert msg["type"] == "state"
        assert msg["system"]["ram_pct"] == 42

        # Send a cmd; handler should see it.
        w.write(json.dumps({"type": "cmd", "name": "show_page",
                            "page": "AI Spend"}).encode() + b"\n")
        await w.drain()
        await asyncio.sleep(0.05)
        assert cmds and cmds[-1]["name"] == "show_page"

        w.close(); await w.wait_closed()
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_no_state_frames_before_auth(tmp_path):
    """An unauthenticated client must not receive any bus state frames —
    the fan-out pump only starts after a successful hello."""
    bus = Bus()
    async def handler(m): pass

    srv = IPCServer(bus, handler, port=0, token_path=tmp_path / "ipc.token")
    port = await srv.start()
    try:
        r, w = await _connect(port)
        # Connect but DON'T send hello. Publish a state on the bus.
        await asyncio.sleep(0.05)
        bus.publish({"type": "state", "ts": 1, "system": {"ram_pct": 99}})
        await asyncio.sleep(0.05)
        # Nothing should be readable — the pump isn't subscribed yet.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(r.readline(), 0.3)
        w.close(); await w.wait_closed()
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_unauth_cmd_ignored(tmp_path):
    bus = Bus()
    seen: list[dict] = []

    async def handler(m): seen.append(m)

    srv = IPCServer(bus, handler, port=0, token_path=tmp_path / "ipc.token")
    port = await srv.start()
    try:
        r, w = await _connect(port)
        # Send a cmd without a hello.
        w.write(b'{"type":"cmd","name":"reload_config"}\n'); await w.drain()
        await asyncio.sleep(0.05)
        assert seen == []
        w.close(); await w.wait_closed()
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_sticky_agent_status_replayed_after_hello(tmp_path):
    """Regression test for the "device plugged-in at launch, UI shows
    disconnected until replug" bug. The agent publishes `agent_status`
    with sticky_key BEFORE the Electron IPC client connects. After the
    client hellos, it must receive the latest agent_status immediately
    — without needing a fresh connect/disconnect transition."""
    bus = Bus()
    async def handler(m): pass

    srv = IPCServer(bus, handler, port=0, token_path=tmp_path / "ipc.token")
    port = await srv.start()
    try:
        # Agent publishes status BEFORE any client connects.
        bus.publish(
            {"type": "event", "name": "agent_status",
             "connected": True, "port": "auto", "transport": "usb"},
            sticky_key="agent_status",
        )

        token = (tmp_path / "ipc.token").read_text().strip()
        r, w = await _connect(port)
        w.write(json.dumps({"type": "hello", "token": token}).encode() + b"\n")
        await w.drain()
        ack = json.loads(await asyncio.wait_for(r.readline(), 2.0))
        assert ack["ok"] is True

        # The client must receive the cached agent_status on subscribe.
        msg = json.loads(await asyncio.wait_for(r.readline(), 2.0))
        assert msg["type"] == "event"
        assert msg["name"] == "agent_status"
        assert msg["connected"] is True
        assert msg["transport"] == "usb"

        w.close(); await w.wait_closed()
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_set_active_routes_to_activity_tracker(tmp_path):
    """`set_active` is handled by the IPC server itself (not via the
    cmd handler) so the per-client identity is preserved."""
    bus = Bus()
    cmds: list[dict] = []
    async def handler(m): cmds.append(m)

    activity = ActivityTracker()
    srv = IPCServer(bus, handler, port=0, token_path=tmp_path / "ipc.token",
                    activity=activity)
    port = await srv.start()
    try:
        token = (tmp_path / "ipc.token").read_text().strip()

        # Two clients, both authenticate.
        r1, w1 = await _connect(port)
        w1.write(json.dumps({"type": "hello", "token": token}).encode() + b"\n")
        await w1.drain()
        await asyncio.wait_for(r1.readline(), 2.0)  # ack

        r2, w2 = await _connect(port)
        w2.write(json.dumps({"type": "hello", "token": token}).encode() + b"\n")
        await w2.drain()
        await asyncio.wait_for(r2.readline(), 2.0)  # ack

        # Client 1 declares active → tracker flips.
        w1.write(b'{"type":"cmd","name":"set_active","active":true}\n')
        await w1.drain()
        await asyncio.sleep(0.05)
        assert activity.has_active_consumer
        # set_active must NOT have hit the generic cmd handler.
        assert not any(c.get("name") == "set_active" for c in cmds)

        # Client 1 goes inactive — tracker drops back to idle.
        w1.write(b'{"type":"cmd","name":"set_active","active":false}\n')
        await w1.drain()
        await asyncio.sleep(0.05)
        assert not activity.has_active_consumer

        # Client 2 active, then disconnect — tracker prunes the stale flag.
        w2.write(b'{"type":"cmd","name":"set_active","active":true}\n')
        await w2.drain()
        await asyncio.sleep(0.05)
        assert activity.has_active_consumer
        w2.close(); await w2.wait_closed()
        await asyncio.sleep(0.05)
        assert not activity.has_active_consumer

        w1.close(); await w1.wait_closed()
    finally:
        await srv.stop()
