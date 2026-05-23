"""Wire-format round trips for the host/device protocol."""
from __future__ import annotations

from dashd.protocol import (
    PROTOCOL_VERSION, decode_line, encode_line, make_state,
)


def test_state_envelope_has_type_and_ts():
    msg = make_state({"system": {"cpu_pct": [1, 2]}})
    assert msg["type"] == "state"
    assert isinstance(msg["ts"], int)
    assert msg["system"]["cpu_pct"] == [1, 2]


def test_state_envelope_has_version_and_source():
    """The envelope carries a protocol version and a machine-source object.
    v1 firmware ignores both; they're reserved for negotiation + future
    multi-computer routing."""
    msg = make_state({"system": {}})
    assert msg["v"] == PROTOCOL_VERSION
    src = msg["source"]
    assert set(src) == {"host", "os", "id"}
    assert all(isinstance(v, str) for v in src.values())
    # `id` is a hash — never the raw MAC.
    assert src["id"] and ":" not in src["id"]


def test_state_envelope_is_json_safe():
    """The full envelope must survive an encode/decode round trip."""
    msg = make_state({"system": {"ram_pct": 50}})
    assert decode_line(encode_line(msg).rstrip(b"\n")) == msg


def test_encode_is_single_newline_terminated_line():
    line = encode_line({"type": "state", "ts": 1, "x": 2})
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1


def test_round_trip():
    src = {"type": "event", "name": "boot", "fw_version": "0.1.0"}
    decoded = decode_line(encode_line(src).rstrip(b"\n"))
    assert decoded == src


def test_decode_garbage_returns_none():
    assert decode_line(b"\xff\xff not json") is None
    assert decode_line(b"{not json") is None


def test_decode_handles_trailing_newline():
    line = encode_line({"type": "state", "ts": 1})
    assert decode_line(line) == {"type": "state", "ts": 1}
