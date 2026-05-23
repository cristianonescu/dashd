"""Activity tracker — drives the agent's adaptive push tick rate."""
from __future__ import annotations

from dashd.activity import ActivityTracker


def test_idle_by_default():
    a = ActivityTracker()
    assert not a.has_active_consumer


def test_active_when_device_connects():
    a = ActivityTracker()
    a.set_device_connected(True)
    assert a.has_active_consumer
    a.set_device_connected(False)
    assert not a.has_active_consumer


def test_active_when_any_client_active():
    a = ActivityTracker()
    a.client_set_active(1, True)
    assert a.has_active_consumer
    a.client_set_active(1, False)
    assert not a.has_active_consumer


def test_multi_client_logic():
    """Hidden window must not silence a visible peer."""
    a = ActivityTracker()
    a.client_set_active(1, True)   # client 1 visible
    a.client_set_active(2, True)   # client 2 visible
    a.client_set_active(2, False)  # client 2 hidden — client 1 still active
    assert a.has_active_consumer
    a.client_set_active(1, False)  # client 1 hidden — now idle
    assert not a.has_active_consumer


def test_client_gone_prunes_stale_active_flag():
    a = ActivityTracker()
    a.client_set_active(7, True)
    assert a.has_active_consumer
    a.client_gone(7)
    assert not a.has_active_consumer


def test_listener_fires_only_on_transition():
    """on_change must fire exactly when has_active_consumer flips, not
    every time a client toggles. Otherwise the push loop would wake up
    spuriously on every set_active false from a hidden second window."""
    a = ActivityTracker()
    calls: list[bool] = []
    a.on_change(lambda: calls.append(a.has_active_consumer))

    a.client_set_active(1, True)        # flip → True (1 call)
    a.client_set_active(2, True)        # still True, no transition
    a.client_set_active(2, False)       # still True, no transition
    a.client_set_active(1, False)       # flip → False (2nd call)
    a.set_device_connected(True)        # flip → True (3rd call)
    a.set_device_connected(True)        # already True, no transition
    a.set_device_connected(False)       # flip → False (4th call)

    assert calls == [True, False, True, False]


def test_listener_unsubscribe():
    a = ActivityTracker()
    calls: list[bool] = []
    off = a.on_change(lambda: calls.append(True))
    off()
    a.client_set_active(1, True)
    assert calls == []


def test_device_or_client_either_keeps_active():
    """Device alone keeps us active even when no client is active, and
    vice versa — the tracker is a logical OR over the two sources."""
    a = ActivityTracker()
    a.set_device_connected(True)
    a.client_set_active(1, True)
    a.set_device_connected(False)
    assert a.has_active_consumer  # client still active
    a.client_set_active(1, False)
    assert not a.has_active_consumer
    a.set_device_connected(True)
    assert a.has_active_consumer  # device alone is enough
