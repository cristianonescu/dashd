"""Local IPC server: same JSON line frames as USB, available on 127.0.0.1.

Wire protocol (newline-delimited JSON, UTF-8):

  Client → Server
    {"type": "hello", "token": "<contents of ~/.config/dashd/ipc.token>"}
    {"type": "cmd",   "name": "reload_config"}
    {"type": "cmd",   "name": "show_page", "page": "Messages"}
    {"type": "cmd",   "name": "set_brightness", "value": 80}

  Server → Client (only after a successful hello)
    {"type": "state", ...}                   — every aggregator tick
    {"type": "event", "name": "log",  ...}   — agent + firmware logs
    {"type": "event", "name": "agent_status", "connected": true, "port": "..."}

The token file is created on first launch (mode 0600). Loopback-only and a
shared-secret check prevents other local users / scripts from binding to the
agent and snooping. Not a security boundary — anyone with read access to your
home directory can read the token — but better than nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Callable, Awaitable

from dashd.activity import ActivityTracker
from dashd.bus import Bus

log = logging.getLogger("dashd.ipc")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 52317
TOKEN_PATH = Path.home() / ".config" / "dashd" / "ipc.token"

CmdHandler = Callable[[dict[str, Any]], Awaitable[None]]


def ensure_token(path: Path = TOKEN_PATH) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            tok = path.read_text().strip()
            if tok:
                return tok
        except OSError:
            pass
    tok = secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(tok)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return tok


class IPCServer:
    def __init__(
        self,
        bus: Bus,
        cmd_handler: CmdHandler,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        token_path: Path = TOKEN_PATH,
        activity: ActivityTracker | None = None,
    ) -> None:
        self.bus = bus
        self.cmd_handler = cmd_handler
        self.host = host
        self.port = port
        self._token = ensure_token(token_path)
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        # Optional — if absent, the IPC server still works but cannot tell
        # the runtime when a client becomes (in)active. Production wiring
        # always provides one.
        self.activity = activity
        self._next_client_id = 1

    async def start(self) -> int:
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
        except OSError as e:
            # Port busy: another dashd-agent is already running. Bail loudly
            # so the supervisor (or terminal user) sees it instead of starting
            # a second collector pipeline that nobody is reading.
            log.error("ipc port %d busy — is another dashd-agent running? %s",
                      self.port, e)
            raise
        sockets = self._server.sockets or ()
        actual_port = sockets[0].getsockname()[1] if sockets else self.port
        log.info("ipc listening on %s:%d", self.host, actual_port)
        return actual_port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        cid = self._next_client_id
        self._next_client_id += 1
        log.debug("ipc client connected: %s (cid=%d)", peer, cid)
        # Bus subscription + fan-out pump are deferred until the client has
        # authenticated — an unauthenticated client must not receive any
        # state frames (see docs/ipc.md).
        sub: asyncio.Queue | None = None
        pump_task: asyncio.Task | None = None
        authed = False
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                t = msg.get("type")
                if t == "hello":
                    authed = secrets.compare_digest(str(msg.get("token") or ""), self._token)
                    writer.write((json.dumps({
                        "type": "hello_ack",
                        "ok": authed,
                    }) + "\n").encode("utf-8"))
                    await writer.drain()
                    if not authed:
                        log.warning("ipc client %s failed auth", peer)
                        break
                    # Authenticated — only now subscribe and start fan-out.
                    if pump_task is None:
                        sub = self.bus.subscribe()
                        pump_task = asyncio.create_task(
                            self._pump_to_client(writer, sub))
                        self._tasks.add(pump_task)
                elif t == "cmd" and authed:
                    # `set_active` is a per-client signal — handled here so we
                    # can correlate the activity to *this* connection's cid,
                    # rather than letting the global cmd handler guess which
                    # client it came from.
                    if msg.get("name") == "set_active":
                        if self.activity is not None:
                            self.activity.client_set_active(
                                cid, bool(msg.get("active")))
                        continue
                    try:
                        await self.cmd_handler(msg)
                    except Exception as e:
                        log.warning("cmd handler failed: %s", e)
                # ignore unknown types
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if sub is not None:
                self.bus.unsubscribe(sub)
            if pump_task is not None:
                pump_task.cancel()
                self._tasks.discard(pump_task)
                # Await the cancellation so teardown is deterministic — the
                # pump has stopped writing before we close the socket.
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self.activity is not None:
                self.activity.client_gone(cid)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.debug("ipc client disconnected: %s (cid=%d)", peer, cid)

    async def _pump_to_client(
        self,
        writer: asyncio.StreamWriter,
        sub: asyncio.Queue[dict[str, Any]],
    ) -> None:
        try:
            while True:
                msg = await sub.get()
                writer.write((json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8"))
                try:
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    break
        except asyncio.CancelledError:
            pass
