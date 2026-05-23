"""Bus pub/sub + drop-oldest backpressure."""
from __future__ import annotations

import asyncio

import pytest

from dashd.bus import Bus


@pytest.mark.asyncio
async def test_fanout_to_multiple_subscribers():
    b = Bus()
    a, c = b.subscribe(), b.subscribe()
    b.publish({"x": 1})
    assert (await a.get()) == {"x": 1}
    assert (await c.get()) == {"x": 1}


@pytest.mark.asyncio
async def test_full_queue_drops_oldest():
    b = Bus(queue_size=2)
    q = b.subscribe()
    b.publish({"n": 1}); b.publish({"n": 2}); b.publish({"n": 3})
    # Queue was full when "3" arrived; the oldest ("1") is dropped.
    assert (await q.get())["n"] == 2
    assert (await q.get())["n"] == 3


def test_unsubscribe_is_idempotent():
    b = Bus()
    q = b.subscribe()
    b.unsubscribe(q)
    b.unsubscribe(q)  # second call must not raise
    assert b.subscriber_count == 0


@pytest.mark.asyncio
async def test_sticky_publish_replays_to_late_subscriber():
    """Reproducer for the "device plugged-in at launch but UI shows
    disconnected" bug: the agent emits `agent_status` before the IPC
    client subscribes. With sticky_key, the new subscriber receives
    the most recent value immediately on subscribe()."""
    b = Bus()
    b.publish(
        {"type": "event", "name": "agent_status", "connected": True},
        sticky_key="agent_status",
    )
    # New subscriber arrives AFTER the publish.
    q = b.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=0.5)
    assert msg["name"] == "agent_status"
    assert msg["connected"] is True


@pytest.mark.asyncio
async def test_sticky_publish_keeps_only_latest_value():
    b = Bus()
    b.publish({"v": 1}, sticky_key="k")
    b.publish({"v": 2}, sticky_key="k")
    q = b.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=0.5)
    assert msg["v"] == 2
    # No second sticky value should be replayed.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_non_sticky_publish_not_replayed():
    b = Bus()
    b.publish({"v": 1})  # no sticky_key
    q = b.subscribe()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_sticky_replay_does_not_break_existing_subscribers():
    """Existing subscribers must NOT receive a duplicate replay when a
    new subscriber joins — sticky replay is per-new-queue only."""
    b = Bus()
    q1 = b.subscribe()
    b.publish({"v": 1}, sticky_key="k")
    assert (await q1.get())["v"] == 1
    # Now a second subscriber arrives — q1 must not see a second copy.
    q2 = b.subscribe()
    assert (await q2.get())["v"] == 1
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q1.get(), timeout=0.05)


def test_clear_sticky():
    b = Bus()
    b.publish({"v": 1}, sticky_key="k")
    b.clear_sticky("k")
    q = b.subscribe()
    assert q.empty()


@pytest.mark.asyncio
async def test_sticky_replay_precedes_live_frames():
    """A subscriber that joins after a sticky publish AND before a
    subsequent live publish must see the sticky first, then the live
    frame. Order matters — the UI applies them in receive order."""
    b = Bus()
    b.publish({"v": "sticky"}, sticky_key="k")
    q = b.subscribe()
    b.publish({"v": "live"})  # non-sticky, post-subscribe
    first = await asyncio.wait_for(q.get(), timeout=0.5)
    second = await asyncio.wait_for(q.get(), timeout=0.5)
    assert first["v"] == "sticky"
    assert second["v"] == "live"


@pytest.mark.asyncio
async def test_multiple_sticky_keys_replayed_in_insertion_order():
    """When more than one sticky key is cached, a late subscriber gets
    all of them. We don't promise a specific order, but dict iteration
    in CPython 3.7+ is insertion-ordered, and the implementation relies
    on that — lock it down so a future refactor doesn't quietly break
    UI assumptions about, say, agent_status arriving before ble_trusted."""
    b = Bus()
    b.publish({"name": "agent_status", "connected": True},
              sticky_key="agent_status")
    b.publish({"name": "ble_trusted", "devices": []},
              sticky_key="ble_trusted")
    q = b.subscribe()
    a = await asyncio.wait_for(q.get(), timeout=0.5)
    bm = await asyncio.wait_for(q.get(), timeout=0.5)
    assert a["name"] == "agent_status"
    assert bm["name"] == "ble_trusted"


@pytest.mark.asyncio
async def test_late_subscriber_after_two_transitions_sees_only_latest():
    """Disconnected → connected → disconnected before the client ever
    subscribes: the cached value is the LAST one, so the late joiner
    sees `connected: false`. This is the desired behaviour — we don't
    want to replay a stale "connected" state after a real disconnect."""
    b = Bus()
    b.publish({"name": "agent_status", "connected": False},
              sticky_key="agent_status")
    b.publish({"name": "agent_status", "connected": True},
              sticky_key="agent_status")
    b.publish({"name": "agent_status", "connected": False},
              sticky_key="agent_status")
    q = b.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=0.5)
    assert msg["connected"] is False
    # No further sticky values should arrive.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_sticky_count_exceeding_queue_capacity_does_not_crash():
    """Defensive: if more sticky keys are cached than the per-subscriber
    queue can hold, subscribe() must not raise — it stops enqueuing once
    the queue is full. The subscriber gets a truncated view; the live
    stream still works."""
    b = Bus(queue_size=2)
    for i in range(5):
        b.publish({"i": i}, sticky_key=f"k{i}")
    q = b.subscribe()  # must not raise
    # We got *some* sticky values (at most queue_size). All present
    # values must be valid — i.e. real published frames.
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    assert 0 < len(drained) <= 2
    for m in drained:
        assert "i" in m
    # Live publishes still work after a saturated sticky replay.
    b.publish({"i": "live"})
    msg = await asyncio.wait_for(q.get(), timeout=0.5)
    assert msg["i"] == "live"
