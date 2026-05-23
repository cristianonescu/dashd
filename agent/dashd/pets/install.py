"""End-to-end install: catalog slug/URL → downloaded → converted → streamed to device.

The streaming layer chunks a `.dpet` binary into base64-encoded JSON
messages the firmware (see firmware/src/usb_link.cpp:apply_cmd) consumes:

    {"type":"cmd","name":"pet_install_start","slug":"…","size":N}
    {"type":"cmd","name":"pet_install_chunk","seq":k,"data":"<base64 ≤2 KB>"}
    …
    {"type":"cmd","name":"pet_install_end"}

The device acks each chunk with a `pet_install_chunk_ack` event and each
phase with `pet_install_started` / `pet_install_ended`. The streamer uses
those chunk ACKs for real windowed flow control (see stream_to_device) so
it works identically over USB and BLE.

This module is callable from IPC handlers but also works standalone for
testing.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from dashd.pets import CACHE_DIR, FRAME_W, FRAME_H
from dashd.pets.catalog import PetEntry, lookup
from dashd.pets.converter import convert, DPet
from dashd.pets.downloader import download_bundle

log = logging.getLogger("dashd.pets.install")

# Raw bytes per chunk. Base64 expands by ~4/3 — keep the final JSON line
# well under the firmware's USB_RX_LINE_MAX (4 KB) including the envelope.
CHUNK_BYTES = 2048

# Type alias for a thing that can write a single JSON cmd to the device.
SendCmd = Callable[[dict], Awaitable[None] | None]


def cache_path(slug: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{slug}.dpet"


async def fetch_and_convert(slug_or_url: str,
                            client: httpx.AsyncClient | None = None,
                            use_cache: bool = True) -> tuple[PetEntry, DPet]:
    """Resolve a slug or URL, download, convert, cache. Returns the entry +
    in-memory DPet binary."""
    entry = lookup(slug_or_url)
    if entry is None:
        raise ValueError(f"unrecognized pet slug or URL: {slug_or_url}")

    cp = cache_path(entry.slug)
    if use_cache and cp.is_file():
        raw = cp.read_bytes()
        log.info("cache hit %s (%d bytes)", entry.slug, len(raw))
        # Wrap cached bytes in a DPet shape; states list is best-effort here.
        from dashd.pets.converter import parse_header
        info = parse_header(raw)
        return entry, DPet(
            raw=raw,
            frame_count=info["frame_count"],
            anim_count=info["anim_count"],
            states=[s[0] for s in info["states"]],
            frames_per_state={s[0]: s[2] for s in info["states"]},
        )

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        bundle = await download_bundle(entry.download_url, entry.slug, client=client)
        dpet = await asyncio.to_thread(convert, bundle, FRAME_W, FRAME_H)
        cp.write_bytes(dpet.raw)
        log.info("converted + cached %s (%d frames, %d bytes)",
                 entry.slug, dpet.frame_count, len(dpet.raw))
        return entry, dpet
    finally:
        if own_client:
            await client.aclose()


# Awaitable returned by a wait_ack(seq) call — resolves when the device
# acks chunk `seq`. None means "no ACK channel" → fall back to delay pacing.
WaitAck = Callable[[int], Awaitable[dict]]
# Awaitable returned by wait_started() — resolves with the device's
# `pet_install_started` event (or raises on timeout / link drop).
WaitStarted = Callable[[], Awaitable[dict]]


async def stream_to_device(slug: str, raw: bytes, send_cmd: SendCmd,
                           wait_ack: WaitAck | None = None,
                           wait_started: WaitStarted | None = None,
                           window: int = 8,
                           chunk_bytes: int = CHUNK_BYTES,
                           chunk_delay_s: float = 0.030) -> None:
    """Send a converted .dpet binary to the device in newline-JSON chunks.

    Flow control: when `wait_ack` is supplied, the sender keeps at most
    `window` chunks outstanding and waits for the device's
    `pet_install_chunk_ack` before sending past the window. This is real
    backpressure — it works identically over USB and BLE, where the old
    fixed 30 ms inter-chunk sleep would either stall or overrun.

    When `wait_ack` is None (standalone use / tests), it falls back to the
    legacy fixed-delay pacing.

    When `wait_started` is supplied, the sender awaits the device's
    `pet_install_started` event after sending START. If the device replies
    with ok=false (e.g. LittleFS mount failure), the stream aborts BEFORE
    any chunks are sent — preventing the "chunk without start" log flood
    on the device and the wasted upstream bandwidth.
    """
    async def _emit(cmd):
        r = send_cmd(cmd)
        if asyncio.iscoroutine(r):
            await r

    await _emit({"type": "cmd", "name": "pet_install_start",
                 "slug": slug, "size": len(raw)})

    # If the caller wired up an event waiter, await the device's
    # `pet_install_started` reply. Otherwise fall back to a fixed 50 ms
    # nap (legacy behaviour, preserved for tests).
    if wait_started is not None:
        ack = await wait_started()
        if isinstance(ack, dict) and ack.get("ok") is False:
            raise RuntimeError(
                f"device rejected pet_install_start for {slug!r} "
                f"(LittleFS mount failed?)"
            )
    else:
        await asyncio.sleep(0.050)

    total = (len(raw) + chunk_bytes - 1) // chunk_bytes
    # seq -> the ACK waiter task for that chunk (windowed flow control).
    # Each is wrapped as a Task so a mid-install failure can cancel it —
    # cancelling the task cancels the underlying event-demux future too.
    pending: dict[int, "asyncio.Future[dict]"] = {}

    def _check_ack(ack: object, seq: int) -> None:
        # The device sets ok=false on a chunk it failed to decode/write.
        # Abort rather than march on to pet_install_end with a corrupt pet.
        if isinstance(ack, dict) and ack.get("ok") is False:
            raise RuntimeError(f"device rejected pet chunk {seq}")

    def _drain_done() -> None:
        """Pop and check any waiters that already resolved. Catches an
        early ok=false (e.g. chunk 0 rejected) before the window fills
        — without this the failure isn't noticed until ~8 chunks have
        been buffered and sent."""
        for s in sorted(pending):
            fut = pending[s]
            if fut.done():
                # Resolved waiter — pull and validate. Raises on ok=false.
                ack = fut.result()
                del pending[s]
                _check_ack(ack, s)

    try:
        for seq in range(total):
            b = raw[seq * chunk_bytes:(seq + 1) * chunk_bytes]
            if wait_ack is not None:
                # Yield once so any ACK task that completed in the prior
                # iteration actually gets to run (without this, completed
                # tasks may not be marked `done()` yet when we inspect
                # them below — `await _emit(...)` on a non-suspending
                # send_cmd doesn't reschedule the event loop).
                await asyncio.sleep(0)
                # Eagerly check waiters that already resolved — catches a
                # fast-fail on chunk 0 before we'd otherwise notice (at
                # seq=window).
                _drain_done()
                # Drain the oldest ACK once the window is full, BEFORE
                # sending more — bounded outstanding chunks, no overrun.
                if len(pending) >= window:
                    oldest = min(pending)
                    _check_ack(await pending.pop(oldest), oldest)
                # Register the waiter BEFORE the chunk goes out so a fast
                # ACK can't arrive before we're listening.
                pending[seq] = asyncio.ensure_future(wait_ack(seq))
            await _emit({"type": "cmd", "name": "pet_install_chunk",
                         "seq": seq,
                         "data": base64.b64encode(b).decode("ascii")})
            if wait_ack is None and chunk_delay_s > 0:
                await asyncio.sleep(chunk_delay_s)

        # Drain remaining ACKs before asking the device to finalize.
        for seq in sorted(pending):
            _check_ack(await pending.pop(seq), seq)
    finally:
        # On a mid-stream failure (ACK timeout, link drop) cancel every
        # still-outstanding waiter so neither the coroutines nor their
        # event-demux futures leak.
        for fut in pending.values():
            fut.cancel()
        pending.clear()

    await asyncio.sleep(0.050)
    await _emit({"type": "cmd", "name": "pet_install_end"})


async def install(slug_or_url: str, send_cmd: SendCmd,
                  client: httpx.AsyncClient | None = None,
                  wait_ack: WaitAck | None = None,
                  wait_started: WaitStarted | None = None) -> str:
    """Top-level: download + convert + stream. Returns the installed slug.

    `wait_ack` enables ACK-windowed flow control (see stream_to_device).
    `wait_started` enables a START-handshake check so a device-side mount
    failure aborts before any chunks are uploaded."""
    entry, dpet = await fetch_and_convert(slug_or_url, client=client)
    await stream_to_device(entry.slug, dpet.raw, send_cmd,
                           wait_ack=wait_ack, wait_started=wait_started)
    return entry.slug
