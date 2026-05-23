"""Device firmware OTA orchestration.

Bridges the GitHub Releases API and the device's OTA wire protocol:

  1. `check_for_update()` fetches the latest release JSON, returns the
     newest version + the URL of the .bin asset that matches the
     device's build variant (`ble` vs `usb`).

  2. `stream_update()` downloads the .bin into memory, computes its
     SHA256, then streams the bytes to the device using the same
     window-with-ACK pattern as the pet installer (see
     `dashd/pets/install.py`).

  3. Progress + completion events are published on the agent bus as
     `fw_update_*` so the Electron UI can render a progress bar.

Design notes:

  - The agent doesn't know *a priori* whether the device is running the
    `ble` or `usb` firmware variant. We look at the boot event's
    `variant` field (added in the firmware in this same phase); fall
    back to `ble` (the superset) if missing.

  - We refuse to update over BLE if the agent has seen an RSSI < -75
    dBm in the last minute; the host should plug in for the safer
    update. The UI surfaces this as a soft warning.

  - SHA256 of the downloaded image is sent to the device, which
    re-verifies the bytes after the last chunk lands before flipping
    the OTA slot.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("dashd.fw_update")

GITHUB_API = "https://api.github.com/repos/cristianonescu/dashd/releases/latest"
DOWNLOAD_TIMEOUT_S = 60.0
CHUNK_BYTES = 2048  # same as pet installer; well under BLE MTU
# Outstanding chunks before we wait for the oldest ACK.
#
# Pet installs use WINDOW=8 to keep the link full while the device's
# LittleFS writes (sub-millisecond) drain. Firmware OTA cannot afford
# that — each esp_ota_write blocks for ~30-50 ms while flash sector
# erase + program runs. With 2 KB chunks * 8 outstanding, the agent
# floods ~23 KB into the USB-CDC stream during a single write window,
# which overflows the ESP32-C3's small kernel RX buffer. Symptom: the
# device silently drops the tail of one chunk, logs
# `json parse: InvalidInput (len=NNN)`, and the OTA aborts at ~2%.
#
# WINDOW=1 → strictly send-and-wait per chunk. Throughput drops from
# theoretical ~40 KB/s to ~30 KB/s, but the transfer is reliable.
# Over USB the entire ~1 MB firmware still finishes in ~30 s.
WINDOW = 1


@dataclass(frozen=True)
class FirmwareRelease:
    """Parsed view of the latest GitHub Release for OTA purposes."""
    version: str                # "0.1.2" — leading 'v' stripped
    notes: str                  # markdown body
    asset_url_ble: str | None   # download URL for the BLE firmware .bin
    asset_url_usb: str | None   # download URL for the USB firmware .bin
    size_ble: int | None
    size_usb: int | None


def _parse_release(payload: dict) -> FirmwareRelease | None:
    tag = (payload.get("tag_name") or "").lstrip("v").strip()
    if not tag:
        return None
    notes = payload.get("body") or ""
    asset_url_ble = None
    asset_url_usb = None
    size_ble = None
    size_usb = None
    for a in payload.get("assets") or []:
        name = a.get("name") or ""
        url = a.get("browser_download_url")
        size = a.get("size")
        # Match dashd-firmware-v0.1.2-ble.bin / -usb.bin
        m = re.match(r"^dashd-firmware-v[\d.]+-(ble|usb)\.bin$", name)
        if not m:
            continue
        variant = m.group(1)
        if variant == "ble":
            asset_url_ble, size_ble = url, size
        elif variant == "usb":
            asset_url_usb, size_usb = url, size
    return FirmwareRelease(
        version=tag, notes=notes,
        asset_url_ble=asset_url_ble, asset_url_usb=asset_url_usb,
        size_ble=size_ble, size_usb=size_usb,
    )


async def check_for_update(client: httpx.AsyncClient | None = None
                           ) -> FirmwareRelease | None:
    """Fetch the latest release; return None on transport/auth failure.

    Does NOT compare against the device version — callers do that with
    `is_newer(latest, current)` because they own the device-version state.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S)
    try:
        r = await client.get(GITHUB_API, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        r.raise_for_status()
        return _parse_release(r.json())
    except Exception as e:
        log.warning("release check failed: %s", e)
        return None
    finally:
        if own_client:
            await client.aclose()


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` represents a newer version than `current`.

    Both are dotted strings ("0.1.2"). Missing / unparseable parts compare
    as 0 — so "0.1" vs "0.1.0" is a tie, not a downgrade.
    """
    def tup(v: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", v or "")
        return tuple(int(p) for p in parts)
    return tup(latest) > tup(current)


SendCmd = Callable[[dict], "asyncio.Future | None"]
WaitEvent = Callable[[Callable[[dict], bool]], "asyncio.Future[dict]"]
PublishEvent = Callable[[dict], None]


async def _download(url: str, *, on_progress: Callable[[int, int], None] | None = None
                    ) -> bytes:
    """GET the firmware blob into memory. ~1 MB is fine to hold."""
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            buf = bytearray()
            async for chunk in r.aiter_bytes(chunk_size=16 * 1024):
                buf.extend(chunk)
                if on_progress is not None:
                    on_progress(len(buf), total)
            return bytes(buf)


async def stream_update(release: FirmwareRelease, *, variant: str,
                        send_cmd: SendCmd, wait_event: WaitEvent,
                        publish: PublishEvent) -> bool:
    """Download + stream the firmware to the device.

    Returns True on success (the device reboots and we never see an ACK
    after that — that's by design). False if download or any chunk fails.

    Publishes `fw_update_state` events (`downloading`, `flashing`,
    `progress`, `done`, `error`) on the agent bus throughout.
    """
    url = release.asset_url_ble if variant == "ble" else release.asset_url_usb
    if not url:
        publish({"type": "event", "name": "fw_update_state",
                 "state": "error", "error": f"no {variant} asset in release"})
        return False

    publish({"type": "event", "name": "fw_update_state",
             "state": "downloading", "version": release.version})

    def _on_dl(got: int, total: int) -> None:
        publish({"type": "event", "name": "fw_update_state",
                 "state": "downloading", "bytes": got, "total": total or None,
                 "version": release.version})
    try:
        raw = await _download(url, on_progress=_on_dl)
    except Exception as e:
        publish({"type": "event", "name": "fw_update_state",
                 "state": "error", "error": f"download failed: {e}"})
        return False

    sha = hashlib.sha256(raw).hexdigest()
    log.info("firmware downloaded: %d bytes, sha=%s, version=%s",
             len(raw), sha, release.version)

    publish({"type": "event", "name": "fw_update_state",
             "state": "flashing", "version": release.version,
             "bytes": 0, "total": len(raw)})

    # Send the begin envelope and wait for fw_update_started ack.
    async def _emit(cmd):
        r = send_cmd(cmd)
        if asyncio.iscoroutine(r):
            await r

    started_fut = wait_event(
        lambda m: m.get("type") == "event" and m.get("name") == "fw_update_started")
    await _emit({"type": "cmd", "name": "fw_update_begin",
                 "size": len(raw), "sha256": sha, "version": release.version})
    try:
        started = await asyncio.wait_for(started_fut, timeout=10.0)
    except asyncio.TimeoutError:
        publish({"type": "event", "name": "fw_update_state",
                 "state": "error", "error": "device did not respond to begin"})
        return False
    if not started.get("ok"):
        publish({"type": "event", "name": "fw_update_state",
                 "state": "error", "error": started.get("error") or "begin rejected"})
        return False

    # Windowed chunk loop, same pattern as pet_install.
    total_chunks = (len(raw) + CHUNK_BYTES - 1) // CHUNK_BYTES
    pending: dict[int, "asyncio.Future[dict]"] = {}

    def _make_ack_waiter(seq: int) -> "asyncio.Future[dict]":
        return wait_event(
            lambda m, _s=seq:
                m.get("type") == "event"
                and m.get("name") == "fw_update_chunk_ack"
                and m.get("seq") == _s)

    def _check_ack(ack: dict, seq: int) -> None:
        if not ack.get("ok"):
            raise RuntimeError(f"device rejected chunk {seq}: {ack.get('error')}")

    try:
        for seq in range(total_chunks):
            piece = raw[seq * CHUNK_BYTES:(seq + 1) * CHUNK_BYTES]
            if len(pending) >= WINDOW:
                oldest = min(pending)
                ack = await asyncio.wait_for(pending.pop(oldest), timeout=15.0)
                _check_ack(ack, oldest)
            pending[seq] = asyncio.ensure_future(_make_ack_waiter(seq))
            await _emit({"type": "cmd", "name": "fw_update_chunk", "seq": seq,
                         "data": base64.b64encode(piece).decode("ascii")})
            # Publish coarse progress every ~16 KB; the device also emits its
            # own fw_update_progress events but those go to the UI too.
            sent_bytes = (seq + 1) * CHUNK_BYTES
            if seq % 8 == 0 or seq == total_chunks - 1:
                publish({"type": "event", "name": "fw_update_state",
                         "state": "flashing", "version": release.version,
                         "bytes": min(sent_bytes, len(raw)), "total": len(raw)})
        for seq in sorted(pending):
            ack = await asyncio.wait_for(pending.pop(seq), timeout=15.0)
            _check_ack(ack, seq)
    except Exception as e:
        for fut in pending.values():
            fut.cancel()
        await _emit({"type": "cmd", "name": "fw_update_abort"})
        publish({"type": "event", "name": "fw_update_state",
                 "state": "error", "error": str(e)})
        return False
    finally:
        pending.clear()

    # Send end. On success the device reboots, so we'll never see the ack —
    # treat the begin-of-disconnect as the success signal.
    await _emit({"type": "cmd", "name": "fw_update_end"})
    publish({"type": "event", "name": "fw_update_state",
             "state": "rebooting", "version": release.version})

    # Wait a bit for either `fw_update_done` (failure case) or the link to
    # drop (success — device is rebooting into the new slot).
    done_fut = wait_event(
        lambda m: m.get("type") == "event" and m.get("name") == "fw_update_done")
    try:
        done = await asyncio.wait_for(done_fut, timeout=5.0)
    except asyncio.TimeoutError:
        # No reply within 5 s — either the device rebooted (good) or hung
        # (bad, but rollback will handle it on next boot). The link layer
        # will reconnect and the post-boot version event will tell us.
        publish({"type": "event", "name": "fw_update_state",
                 "state": "done", "version": release.version})
        return True
    if done.get("ok"):
        publish({"type": "event", "name": "fw_update_state",
                 "state": "done", "version": release.version})
        return True
    publish({"type": "event", "name": "fw_update_state",
             "state": "error", "error": done.get("error") or "device-reported failure"})
    return False
