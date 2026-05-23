"""Wire protocol: host → device JSON state messages, device → host events."""
from __future__ import annotations

import hashlib
import json
import platform
import socket
import time
import uuid
from typing import Any

# Per-frame protocol version. Bumped when the wire format changes in a way
# a peer would need to know about. Reserved now so future negotiation
# (and the future multi-computer feature) is additive, not breaking.
PROTOCOL_VERSION = 1


def _machine_source() -> dict[str, str]:
    """Stable per-machine identity for the state envelope.

    `id` is a hash of the MAC-based node id — the raw MAC never goes on the
    wire. This whole object is **display / routing metadata only** and must
    never be trusted for identity or authorization (see docs/protocol.md):
    a BLE central could put any value here. Future multi-computer routing
    binds frames to the authenticated session, not to this field.
    """
    try:
        host = socket.gethostname()
    except Exception:
        host = "?"
    try:
        sid = hashlib.sha1(str(uuid.getnode()).encode()).hexdigest()[:12]
    except Exception:
        sid = "unknown"
    return {"host": host, "os": platform.system(), "id": sid}


# Computed once — machine identity doesn't change during a process lifetime.
_SOURCE: dict[str, str] = _machine_source()


def make_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload dict in a state message envelope.

    Envelope fields: `type`, `v` (protocol version), `ts` (Unix seconds),
    `source` (machine metadata). v1 firmware ignores `v`/`source` — they're
    reserved for protocol negotiation and the future multi-computer feature.
    """
    return {
        "type": "state",
        "v": PROTOCOL_VERSION,
        "ts": int(time.time()),
        "source": dict(_SOURCE),
        **payload,
    }


def encode_line(msg: dict[str, Any]) -> bytes:
    """Encode a single message as one newline-terminated UTF-8 JSON line.

    Uses compact separators to keep frames small over USB-CDC.
    """
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line: bytes) -> dict[str, Any] | None:
    """Decode a single JSON line from the device. Returns None on parse failure."""
    try:
        return json.loads(line.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
