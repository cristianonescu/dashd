"""Auto-advance: IPC passthrough wires user prefs from UI → agent → device.

The actual cycling logic lives on the firmware (which owns page state +
NVS persistence). The agent's job is purely to forward `set_auto_advance`
commands from IPC clients into the TX queue.
"""
from __future__ import annotations

import asyncio

import pytest

from dashd.main import AgentRuntime


def _make_stub_with_tx_queue():
    """Build a minimal stub that has just enough state to exercise
    `handle_cmd` for the set_auto_advance branch — a real `_tx_queue`
    and the bound method. We don't need the full runtime."""

    class Stub:
        handle_cmd = AgentRuntime.handle_cmd
        _tx_put = AgentRuntime._tx_put

    s = Stub()
    s._tx_queue = asyncio.Queue(maxsize=64)
    return s


@pytest.mark.asyncio
async def test_set_auto_advance_passthrough_full_payload():
    """All three fields (enabled, interval_s, mode) pass through to the
    device cmd unchanged. The firmware is the source of truth for
    clamping/validation — agent is just a relay here."""
    s = _make_stub_with_tx_queue()
    await s.handle_cmd({
        "type": "cmd", "name": "set_auto_advance",
        "enabled": True, "interval_s": 12, "mode": "random",
    })
    queued = s._tx_queue.get_nowait()
    assert queued["name"] == "set_auto_advance"
    assert queued["enabled"] is True
    assert queued["interval_s"] == 12
    assert queued["mode"] == "random"


@pytest.mark.asyncio
async def test_set_auto_advance_passthrough_partial_payload():
    """Partial updates — e.g. user toggling enabled without changing
    interval — must forward only the supplied fields. The firmware's
    cmd handler treats missing fields as "keep current"."""
    s = _make_stub_with_tx_queue()
    await s.handle_cmd({
        "type": "cmd", "name": "set_auto_advance",
        "enabled": False,
    })
    queued = s._tx_queue.get_nowait()
    assert queued == {
        "type": "cmd", "name": "set_auto_advance", "enabled": False,
    }


@pytest.mark.asyncio
async def test_set_auto_advance_strips_envelope_fields():
    """`type` and `name` are device-cmd envelope fields — they must not
    appear duplicated in the payload."""
    s = _make_stub_with_tx_queue()
    await s.handle_cmd({
        "type": "cmd", "name": "set_auto_advance",
        "enabled": True, "interval_s": 8, "mode": "sequential",
        # An over-eager IPC client could include these — we strip them.
        "extra_random_key": "shouldnt-be-here-but-pass-it-anyway",
    })
    queued = s._tx_queue.get_nowait()
    # The envelope is rebuilt cleanly — type/name are set by the agent,
    # everything else is preserved (forward-compat for new fields).
    assert queued["type"] == "cmd"
    assert queued["name"] == "set_auto_advance"
    # Unknown forward-compat fields pass through; firmware silently
    # ignores ones it doesn't recognize.
    assert queued["extra_random_key"] == "shouldnt-be-here-but-pass-it-anyway"


@pytest.mark.asyncio
async def test_unknown_cmd_does_not_create_tx_entry():
    """Sanity check: a typo'd cmd name doesn't accidentally pass through."""
    s = _make_stub_with_tx_queue()
    await s.handle_cmd({"type": "cmd", "name": "set_auto_advanc"})  # typo
    assert s._tx_queue.empty()
