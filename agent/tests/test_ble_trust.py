"""BLE pairing trust store — persistence + the remember/forget API."""
from __future__ import annotations

import json

from dashd.ble_trust import TrustStore


def test_empty_by_default(tmp_path):
    ts = TrustStore(tmp_path / "trust.json")
    assert ts.addresses() == []
    assert ts.token_for("AA:BB") is None
    assert ts.is_trusted("AA:BB") is False


def test_remember_and_persist(tmp_path):
    p = tmp_path / "trust.json"
    ts = TrustStore(p)
    ts.remember("AA:BB:CC", "tok-123")
    assert ts.token_for("AA:BB:CC") == "tok-123"
    assert ts.is_trusted("AA:BB:CC")
    # A fresh instance reads it back from disk.
    assert TrustStore(p).token_for("AA:BB:CC") == "tok-123"


def test_file_is_0600(tmp_path):
    p = tmp_path / "trust.json"
    TrustStore(p).remember("D:E:F", "secret")
    assert (p.stat().st_mode & 0o777) == 0o600


def test_forget(tmp_path):
    p = tmp_path / "trust.json"
    ts = TrustStore(p)
    ts.remember("X", "t1")
    assert ts.forget("X") is True
    assert ts.forget("X") is False          # already gone
    assert ts.is_trusted("X") is False
    assert TrustStore(p).addresses() == []  # persisted


def test_forget_all(tmp_path):
    p = tmp_path / "trust.json"
    ts = TrustStore(p)
    ts.remember("A", "1"); ts.remember("B", "2")
    ts.forget_all()
    assert ts.addresses() == []
    assert TrustStore(p).addresses() == []


def test_corrupt_file_treated_as_empty(tmp_path):
    p = tmp_path / "trust.json"
    p.write_text("{not json")
    ts = TrustStore(p)
    assert ts.addresses() == []
    # Still usable — a remember() overwrites the garbage.
    ts.remember("A", "1")
    assert json.loads(p.read_text()) == {"A": "1"}


def test_non_string_entries_ignored(tmp_path):
    p = tmp_path / "trust.json"
    p.write_text(json.dumps({"good": "tok", "bad": 123, "alsobad": None}))
    ts = TrustStore(p)
    assert ts.addresses() == ["good"]
