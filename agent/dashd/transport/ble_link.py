"""BLE (Bluetooth LE) device transport — the `Link` implementation over BLE.

`bleak` is async; the runtime's `Link` interface is synchronous (link_loop
calls `connect` in a worker thread and `send`/`read_events` inline). So
`BleLink` runs bleak on its **own** asyncio loop in a dedicated thread, and
the synchronous `Link` methods marshal onto it via `run_coroutine_threadsafe`.

This deliberately keeps bleak's callbacks off the agent's main asyncio loop
(the threading hazard Codex flagged): notifications fire on the bleak-loop
thread, get reassembled into newline-delimited JSON there, and cross to the
agent thread through a thread-safe `queue.Queue`. The agent's event loop is
never touched by a bleak callback.

Radio-runtime behaviour is verified with a flashed device; the framing /
reassembly logic is unit-tested with `_feed_notify` directly.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Any, Callable

from dashd.protocol import PROTOCOL_VERSION, decode_line, encode_line

log = logging.getLogger("dashd.ble")

# Must match firmware/src/ble_transport.cpp.
SVC_UUID  = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_UUID   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # host → device (write)
TX_UUID   = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # device → host (notify)
AUTH_UUID = "6e400004-b5a3-f393-e0a9-e50e24dcca9e"   # host → device (pairing)

_RX_LINE_MAX = 4096   # mirror the firmware reassembly cap


class BlePairing:
    """One BLE pairing session, driven by IPC commands.

    Flow: begin() connects to the device — which makes it show its 6-digit
    code on screen. If this host already holds a trust token for that
    device, the token is presented and pairing completes silently;
    otherwise the caller must collect the code from the user and call
    submit_code(). On success the device returns a fresh trust token,
    which is persisted so future connects skip the code.

    Runs on the caller's loop (the agent's main loop) — plain coroutines.
    """

    def __init__(self, address: str, trust_store: Any) -> None:
        self._address = address
        self._trust = trust_store
        self._client: Any = None
        # The 6-digit code the device announces over BLE on connect — the
        # agent surfaces it in its logs / pairing UI. None until received.
        self.code: str | None = None
        self._token: str | None = None
        self._buf = bytearray()
        self._code_seen = asyncio.Event()
        self._paired = asyncio.Event()

    def _on_notify(self, _c: Any, data: bytearray) -> None:
        """TX-notify handler — reassembles newline JSON, captures the
        device's `ble_pair_code` announcement and the `ble_paired` token."""
        self._buf.extend(data)
        while b"\n" in self._buf:
            line, _, rest = bytes(self._buf).partition(b"\n")
            self._buf.clear()
            self._buf.extend(rest)
            msg = decode_line(line)
            if not msg:
                continue
            if msg.get("name") == "ble_pair_code" and msg.get("code"):
                self.code = str(msg["code"])
                self._code_seen.set()
            elif msg.get("name") == "ble_paired" and msg.get("token"):
                self._token = str(msg["token"])
                self._paired.set()

    async def begin(self) -> str:
        """Connect + subscribe. Returns 'paired' (silent token re-pair) or
        'awaiting_code' (device is showing its code; call submit_code).
        On 'awaiting_code', `self.code` holds the device-announced code."""
        from bleak import BleakClient
        self._client = BleakClient(self._address)
        await self._client.connect()
        # Subscribe immediately so the device's ble_pair_code announcement
        # (sent right after connect) isn't missed.
        await self._client.start_notify(TX_UUID, self._on_notify)
        token = self._trust.token_for(self._address)
        if token:
            await self._client.write_gatt_char(AUTH_UUID, token.encode(),
                                               response=True)
            await self._close()
            return "paired"
        # Untrusted — the device announces its code over BLE; give it a
        # moment to arrive so the caller can log / display it.
        try:
            await asyncio.wait_for(self._code_seen.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        return "awaiting_code"

    async def submit_code(self, code: str) -> str:
        """Write the user-entered code, wait for the device's trust token,
        persist it. Returns 'paired'; raises on timeout / bad code."""
        if self._client is None:
            raise RuntimeError("no active pairing session")
        await self._client.write_gatt_char(AUTH_UUID, code.encode(),
                                           response=True)
        try:
            await asyncio.wait_for(self._paired.wait(), timeout=15.0)
        finally:
            await self._close()
        self._trust.remember(self._address, self._token or "")
        return "paired"

    async def cancel(self) -> None:
        await self._close()

    async def _close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None


async def scan_devices(timeout: float = 8.0,
                       name_prefix: str = "dashd-") -> list[dict]:
    """Scan for dashd devices and return [{name, address, rssi}].

    Matches on the advertised name prefix or the dashd service UUID.
    Runs on whatever loop calls it (the agent's main loop is fine — this
    is a plain coroutine, no BleLink instance / dedicated thread needed).
    Used by the IPC `ble_scan` command to populate the pairing UI.
    """
    from bleak import BleakScanner   # lazy import — keep cost off USB-only runs

    found: dict[str, dict] = {}
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for dev, adv in discovered.values():
        name = dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if name.startswith(name_prefix) or SVC_UUID.lower() in uuids:
            found[dev.address] = {
                "name": name or "dashd",
                "address": dev.address,
                "rssi": getattr(adv, "rssi", None),
            }
    # Strongest signal first — nearest device at the top of the pairing list.
    return sorted(found.values(),
                  key=lambda d: d["rssi"] if d["rssi"] is not None else -999,
                  reverse=True)


class BleLink:
    """`Link` over BLE. Single-writer, like SerialLink."""

    def __init__(self, name_prefix: str = "dashd-",
                 trust_store: Any = None) -> None:
        self._name_prefix = name_prefix
        # Trust store — connect() only holds a device this host is paired
        # with, presenting the stored token to authenticate.
        self._trust = trust_store
        self._client: Any = None
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._rx = bytearray()      # notification reassembly (bleak-loop thread only)
        self._connected = False
        self._chunk = 20            # write size; raised after MTU negotiation
        self._closed = False        # terminal once close() is called
        # Set by _feed_notify when a hello_ack arrives — connect() awaits it
        # to confirm the auth token was accepted. Created lazily on the
        # bleak loop (an Event must live on the loop that sets/awaits it).
        self._hello_ack: asyncio.Event | None = None
        # The bleak loop + thread are created lazily on first connect() so a
        # BleLink can be constructed cheaply (and unit-tested) without one.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # ---- bleak-loop plumbing ------------------------------------------------

    def _ensure_loop(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="dashd-ble")
        self._thread.start()

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout: float | None = None):
        """Run a coroutine on the bleak loop from a sync caller; block on it."""
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    # ---- Link interface ----------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None

    def connect(self, max_backoff: float = 30.0,
                should_stop: Callable[[], bool] | None = None) -> None:
        """Scan for a dashd device, connect, subscribe to notifications.
        Retries with interruptible backoff like SerialLink."""
        if self._closed:
            # close() is terminal — the bleak loop is stopped. A caller must
            # build a fresh BleLink rather than reconnect a closed one.
            log.warning("ble connect() called on a closed BleLink — ignored")
            return
        self._ensure_loop()
        delay = 1.0
        while not self.connected:
            if should_stop is not None and should_stop():
                return
            try:
                self._call(self._connect_once(), timeout=30.0)
                if self.connected:
                    return
            except Exception as e:
                log.warning("ble connect failed: %s", e)
            slept = 0.0
            while slept < delay:
                if should_stop is not None and should_stop():
                    return
                time.sleep(min(0.25, delay - slept))
                slept += 0.25
            delay = min(delay * 2.0, max_backoff)

    async def _connect_once(self) -> None:
        from bleak import BleakClient, BleakScanner   # lazy: keep import cost off USB-only runs

        def _match(dev, adv) -> bool:
            if (dev.name or "").startswith(self._name_prefix):
                return True
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            return SVC_UUID.lower() in uuids

        dev = await BleakScanner.find_device_by_filter(_match, timeout=12.0)
        if dev is None:
            return

        # The live transport only holds a device this host has paired with.
        # An untrusted device is left alone so the pairing flow (BlePairing)
        # can reach it — and so we don't connect to a device we'd only get
        # frames dropped by (the firmware rejects unauthenticated BLE).
        token = self._trust.token_for(dev.address) if self._trust else None
        if not token:
            log.info("ble: %s not paired — pair it in Settings → Connection",
                     dev.address)
            return

        if self._hello_ack is None:
            self._hello_ack = asyncio.Event()
        self._hello_ack.clear()
        client = BleakClient(dev, disconnected_callback=self._on_disconnect)
        await client.connect()
        self._rx.clear()
        rx_char = client.services.get_characteristic(RX_UUID)
        # macOS CoreBluetooth: mtu_size is not the usable write-without-
        # response payload — prefer the characteristic's reported ceiling.
        wwr = getattr(rx_char, "max_write_without_response_size", 0) or 0
        self._chunk = max(20, wwr if wwr else client.mtu_size - 3)
        await client.start_notify(TX_UUID, self._on_notify)

        # Authenticate with the stored trust token, then verify the session
        # is really up by doing the hello handshake — if the token is stale
        # (firmware NVS reset) the device silently drops our frames and no
        # hello_ack comes back, so we must not report a live connection.
        await client.write_gatt_char(AUTH_UUID, token.encode(), response=True)
        hello = encode_line({"type": "cmd", "name": "hello",
                             "v": PROTOCOL_VERSION})
        for off in range(0, len(hello), self._chunk):
            await client.write_gatt_char(rx_char, hello[off:off + self._chunk],
                                         response=False)
        try:
            await asyncio.wait_for(self._hello_ack.wait(), timeout=6.0)
        except asyncio.TimeoutError:
            log.warning("ble: no hello_ack from %s — trust token may be "
                        "stale; re-pair in Settings → Connection", dev.address)
            try:
                await client.disconnect()
            except Exception:
                pass
            return

        self._client = client
        self._connected = True
        log.info("ble connected + authenticated to %s (chunk=%d B)",
                 dev.address, self._chunk)

    def _on_disconnect(self, _client) -> None:
        self._connected = False
        log.info("ble device disconnected")

    def _on_notify(self, _char, data: bytearray) -> None:
        """bleak notification callback (bleak-loop thread)."""
        self._feed_notify(bytes(data))

    def _feed_notify(self, data: bytes) -> None:
        """Reassemble newline-delimited JSON from a notification chunk and
        push complete messages onto the thread-safe event queue. Split out
        from `_on_notify` so the framing logic is unit-testable without a
        live BLE connection."""
        for b in data:
            if b == 0x0A:        # '\n'
                if self._rx:
                    msg = decode_line(bytes(self._rx))
                    if msg is not None:
                        self._events.put(msg)
                        # hello_ack confirms the device accepted our auth
                        # token — _connect_once awaits this.
                        if (msg.get("name") == "hello_ack"
                                and self._hello_ack is not None):
                            self._hello_ack.set()
                    self._rx.clear()
            elif b == 0x0D:      # '\r' — ignore
                pass
            elif len(self._rx) < _RX_LINE_MAX:
                self._rx.append(b)
            else:
                self._rx.clear()  # overlong line — resync at next newline

    def send(self, msg: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        try:
            self._call(self._send_bytes(encode_line(msg)), timeout=10.0)
            return True
        except Exception as e:
            log.warning("ble write failed: %s", e)
            self._connected = False
            return False

    async def _send_bytes(self, data: bytes) -> None:
        client = self._client
        if client is None:
            raise ConnectionError("ble not connected")
        rx = client.services.get_characteristic(RX_UUID)
        for off in range(0, len(data), self._chunk):
            await client.write_gatt_char(
                rx, data[off:off + self._chunk], response=False)

    def read_events(self) -> list[dict[str, Any]]:
        out: list[dict] = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def close(self) -> None:
        self._closed = True
        try:
            if self._client is not None and self._loop is not None:
                self._call(self._client.disconnect(), timeout=5.0)
        except Exception:
            pass
        self._client = None
        self._connected = False
        # Terminal: stop the bleak loop. connect() must not be called after.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
