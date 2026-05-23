"""Serial transport: auto-detect ESP32-C3 by VID:PID, reconnect with backoff."""
from __future__ import annotations

import errno
import logging
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

import serial
from serial.tools import list_ports

from dashd.protocol import decode_line, encode_line

log = logging.getLogger("dashd.serial")

# Tracks the (port, pid) tuples we've already reported so we log each
# offender exactly once per session rather than every backoff retry.
# Capped so a long-running agent that sees rotating holder PIDs can't
# accumulate the set without bound.
_REPORTED_HOLDERS: set[tuple[str, int]] = set()
_REPORTED_HOLDERS_CAP = 64
# Ports for which we've already logged the "no holder — transient USB
# state" remediation hint, so it isn't repeated on every backoff retry.
_REPORTED_NO_HOLDER: set[str] = set()


def _diagnose_busy_port(port: str, exc: BaseException) -> None:
    """If the failure looks like a busy / in-use tty, run `lsof` for the
    port and log the offending PID + command. macOS errno 83 (EDEVERR)
    almost always means another process has the device open. We also
    cover EBUSY and EACCES on other platforms."""
    if sys.platform != "darwin" and not isinstance(exc, OSError):
        return
    err_no = getattr(exc, "errno", None)
    # On macOS: 83 EDEVERR, 16 EBUSY, 13 EACCES. On Linux: 16 / 13.
    if err_no not in (errno.EBUSY, errno.EACCES, 83):
        return
    lsof = shutil.which("lsof")
    if not lsof:
        return
    try:
        out = subprocess.run(
            [lsof, "-Fpcn", port],
            capture_output=True, text=True, timeout=2.0,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        log.debug("lsof probe failed: %s", e)
        return

    # `lsof -F` emits one field per line, prefixed by field-id char.
    # Records are delimited by the next `p<pid>` line.
    pid: int | None = None
    cmd: str | None = None
    holders: list[tuple[int, str]] = []
    for line in out.splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            if pid is not None:
                holders.append((pid, cmd or "?"))
            pid = int(val) if val.isdigit() else None
            cmd = None
        elif tag == "c":
            cmd = val
    if pid is not None:
        holders.append((pid, cmd or "?"))

    if not holders:
        # No one holds it — likely a transient kernel state (e.g. the
        # device just rebooted). Log the remediation hint once per port so
        # the exponential-backoff retry loop doesn't repeat it.
        if port not in _REPORTED_NO_HOLDER:
            _REPORTED_NO_HOLDER.add(port)
            log.warning(
                "  ↳ no other process holds %s — likely a transient USB state. "
                "Try unplugging and reseating the cable, or check `ls /dev/cu.usbmodem*` "
                "in case the device moved to a different node.", port
            )
        return

    # A holder reappeared — drop the no-holder suppression so a future
    # transient state is reported again.
    _REPORTED_NO_HOLDER.discard(port)
    for pid, cmd in holders:
        key = (port, pid)
        if key in _REPORTED_HOLDERS:
            continue
        if len(_REPORTED_HOLDERS) >= _REPORTED_HOLDERS_CAP:
            _REPORTED_HOLDERS.clear()
        _REPORTED_HOLDERS.add(key)
        log.warning("  ↳ %s is held by pid %d (%s) — run `kill %d` to free it",
                    port, pid, cmd, pid)


def find_port(vid: int, pid: int) -> str | None:
    """Return the first serial port matching the given USB VID/PID, else None."""
    for p in list_ports.comports():
        if p.vid == vid and p.pid == pid:
            return p.device
    return None


class SerialLink:
    """Blocking serial link with automatic port discovery and reconnect.

    Single-pending-write backpressure: callers send state messages whenever
    they want; failed writes trigger a reconnect on the next send.
    """

    def __init__(self, port: str | None, baud: int, vid: int, pid: int) -> None:
        self._configured_port = port
        self._baud = baud
        self._vid = vid
        self._pid = pid
        self._ser: serial.Serial | None = None
        self._rx_buf = bytearray()

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _resolve_port(self) -> str | None:
        if self._configured_port:
            return self._configured_port
        return find_port(self._vid, self._pid)

    def connect(self, max_backoff: float = 30.0,
                should_stop: Callable[[], bool] | None = None) -> None:
        """Block until connected, with exponential backoff (capped).

        `should_stop`, if given, is polled before each attempt and during
        the backoff sleep — when it returns True, `connect()` returns early
        even without a connection. This is what lets a SIGTERM-driven
        shutdown interrupt an agent that is sitting in the retry loop
        because no device is plugged in. Callers must check `.connected`
        afterwards.
        """
        delay = 1.0
        while not self.connected:
            if should_stop is not None and should_stop():
                return
            port = self._resolve_port()
            if port:
                try:
                    self._ser = serial.Serial(port, self._baud, timeout=0.1, write_timeout=2.0)
                    log.info("connected to %s @ %d", port, self._baud)
                    self._rx_buf.clear()
                    return
                except (serial.SerialException, OSError) as e:
                    log.warning("open %s failed: %s", port, e)
                    # macOS errno 83 (EDEVERR) almost always means another
                    # process has the tty open, or the device is in a weird
                    # transient state. Run `lsof` once so the user sees who
                    # the offender is without having to dig.
                    _diagnose_busy_port(port, e)
                    self._ser = None
            else:
                log.debug("no matching serial device yet (vid=%04X pid=%04X)", self._vid, self._pid)
            # Interruptible backoff sleep — poll should_stop every 250 ms so
            # shutdown isn't blocked for up to the full 30 s backoff.
            slept = 0.0
            while slept < delay:
                if should_stop is not None and should_stop():
                    return
                step = min(0.25, delay - slept)
                time.sleep(step)
                slept += step
            delay = min(delay * 2.0, max_backoff)

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def send(self, msg: dict[str, Any]) -> bool:
        """Send one message. Returns False on failure (caller may reconnect)."""
        if not self.connected:
            return False
        try:
            assert self._ser is not None
            self._ser.write(encode_line(msg))
            return True
        except (serial.SerialException, OSError) as e:
            log.warning("write failed: %s", e)
            self.close()
            return False

    def read_events(self) -> list[dict[str, Any]]:
        """Drain any pending newline-delimited messages from the device."""
        if not self.connected:
            return []
        try:
            assert self._ser is not None
            chunk = self._ser.read(self._ser.in_waiting or 1)
        except (serial.SerialException, OSError) as e:
            log.warning("read failed: %s", e)
            self.close()
            return []
        if not chunk:
            return []
        self._rx_buf.extend(chunk)
        msgs: list[dict[str, Any]] = []
        while b"\n" in self._rx_buf:
            line, _, rest = self._rx_buf.partition(b"\n")
            self._rx_buf = bytearray(rest)
            m = decode_line(bytes(line))
            if m is not None:
                msgs.append(m)
        return msgs
