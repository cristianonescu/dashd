"""BleLink notification reassembly — the framing logic, no radio needed."""
from __future__ import annotations

from dashd.transport.ble_link import BleLink


def _drain(link: BleLink) -> list[dict]:
    return link.read_events()


def test_single_notification_one_message():
    link = BleLink()
    link._feed_notify(b'{"type":"event","name":"boot"}\n')
    assert _drain(link) == [{"type": "event", "name": "boot"}]


def test_message_split_across_notifications():
    """A frame larger than the MTU arrives in several notifications and
    must reassemble into exactly one message."""
    link = BleLink()
    full = b'{"type":"event","name":"hello_ack","fw_version":"0.1.0"}\n'
    # Feed it 8 bytes at a time.
    for i in range(0, len(full), 8):
        link._feed_notify(full[i:i + 8])
    assert _drain(link) == [
        {"type": "event", "name": "hello_ack", "fw_version": "0.1.0"}]


def test_multiple_messages_in_one_notification():
    link = BleLink()
    link._feed_notify(b'{"a":1}\n{"b":2}\n{"c":3}\n')
    assert _drain(link) == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_carriage_returns_ignored():
    link = BleLink()
    link._feed_notify(b'{"x":1}\r\n')
    assert _drain(link) == [{"x": 1}]


def test_garbage_line_dropped_not_crashing():
    link = BleLink()
    link._feed_notify(b'not json\n{"ok":true}\n')
    # The garbage line is silently dropped; the valid one still arrives.
    assert _drain(link) == [{"ok": True}]


def test_overlong_line_resyncs_at_next_newline():
    link = BleLink()
    link._feed_notify(b'x' * 5000)        # exceeds _RX_LINE_MAX, no newline
    link._feed_notify(b'junk\n{"ok":1}\n')  # resync: the overlong remainder + 'junk' is dropped
    assert _drain(link) == [{"ok": 1}]


def test_read_events_empty_when_nothing_fed():
    assert BleLink().read_events() == []


def test_not_connected_before_connect():
    assert BleLink().connected is False
