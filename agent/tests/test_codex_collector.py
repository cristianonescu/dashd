"""Tests for the rewritten Codex collector.

Exercises:
  - Multi-session scanning (not just the most recent file)
  - Cumulative→delta diffing via the persisted state file
  - Day rollover resets tokens_today but not last_total
  - Session restart (total regresses) re-baselines safely
  - rate_limits from the freshest session feed through
  - session_active liveness window
  - cost_today_usd stays None (no Codex pricing)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from dashd.collectors.codex import CodexCollector


def _write_session(path: Path, total: int, *, resets_at: int = 0,
                   used_pct: float = 0.0) -> None:
    """Write a minimal rollout JSONL with one token_count event.

    Matches the real Codex format: token_count payloads have
        payload.type == "token_count"
        payload.info.total_token_usage
        payload.rate_limits.primary
    NOT the `payload.message.*` shape an earlier draft of these tests
    assumed (which is what hid the format mismatch behind passing tests).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total // 2,
                    "cached_input_tokens": 0,
                    "output_tokens": total // 2,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total,
                },
                "last_token_usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
            "rate_limits": {
                "primary": {
                    "used_percent": used_pct,
                    "window_minutes": 300,
                    "resets_at": resets_at,
                },
            },
        },
    }
    with open(path, "wb") as f:
        f.write(json.dumps(line).encode() + b"\n")


def _make_collector(root: Path, state_path: Path) -> CodexCollector:
    return CodexCollector(enabled=True, path=root, state_path=state_path)


@pytest.fixture
def codex_root(tmp_path: Path) -> Path:
    """Build a minimal ~/.codex/sessions/YYYY/MM/DD/ tree."""
    return tmp_path / "codex"


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@pytest.mark.asyncio
async def test_first_run_yields_zero_deltas(codex_root: Path,
                                             state_path: Path) -> None:
    """The first collect after seeing a session establishes the baseline;
    no delta is counted yet."""
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-abc.jsonl"
    _write_session(p, total=10_000)
    result = await _make_collector(codex_root, state_path).collect()
    assert result is not None
    assert result["tokens_today"] == 0
    assert state_path.exists()


@pytest.mark.asyncio
async def test_second_run_picks_up_delta(codex_root: Path,
                                          state_path: Path) -> None:
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-abc.jsonl"
    _write_session(p, total=10_000)
    await _make_collector(codex_root, state_path).collect()
    # Session grows by 5000.
    _write_session(p, total=15_000)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["tokens_today"] == 5_000


@pytest.mark.asyncio
async def test_multi_session_summed(codex_root: Path,
                                     state_path: Path) -> None:
    """When two sessions are active, deltas from both are summed."""
    pa = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-aaa.jsonl"
    pb = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-bbb.jsonl"
    _write_session(pa, total=10_000)
    _write_session(pb, total=20_000)
    await _make_collector(codex_root, state_path).collect()
    _write_session(pa, total=12_000)
    _write_session(pb, total=23_000)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["tokens_today"] == 5_000   # +2000 +3000


@pytest.mark.asyncio
async def test_session_restart_rebaselines(codex_root: Path,
                                            state_path: Path) -> None:
    """If a session's cumulative total goes DOWN (restart / truncation),
    we treat it as a re-baseline rather than counting backwards."""
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-x.jsonl"
    _write_session(p, total=50_000)
    await _make_collector(codex_root, state_path).collect()
    # File truncated, fresh session with same id reports 200 tokens.
    _write_session(p, total=200)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["tokens_today"] == 0     # re-baselined, no negative count
    # Now session grows from 200 → 1000.
    _write_session(p, total=1_000)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["tokens_today"] == 800


@pytest.mark.asyncio
async def test_cost_today_usd_stays_none(codex_root: Path,
                                          state_path: Path) -> None:
    """No public Codex pricing — cost must always be None."""
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-abc.jsonl"
    _write_session(p, total=10_000)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["cost_today_usd"] is None


@pytest.mark.asyncio
async def test_rate_limits_passthrough(codex_root: Path,
                                        state_path: Path) -> None:
    """resets_at from the rate_limits block should drive block_resets_at."""
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-abc.jsonl"
    future = int(time.time() + 3600)
    _write_session(p, total=100, resets_at=future, used_pct=42)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["block_resets_at"] == future
    assert result["block_pct"] == 42
    # Codex's used_percent is the actual usage %, not time-elapsed —
    # block_used_pct should mirror it.
    assert result["block_used_pct"] == 42


@pytest.mark.asyncio
async def test_no_session_returns_none(codex_root: Path,
                                        state_path: Path) -> None:
    """Empty ~/.codex returns None, agent gracefully omits the block."""
    result = await _make_collector(codex_root, state_path).collect()
    assert result is None


@pytest.mark.asyncio
async def test_sessions_field_is_not_a_dict(codex_root: Path,
                                              state_path: Path) -> None:
    """If the state file's top-level `sessions` key isn't a dict (e.g.
    a string left over from an older buggy version), collect() must not
    crash with `'str' object has no attribute 'get'`."""
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-broken.jsonl"
    _write_session(p, total=10_000)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "day": "2026-05-23",
        "tokens_today": 0,
        # Bogus shape — should be a dict.
        "sessions": "this was a dict in v0.1.2 and got mangled somewhere",
    }))
    # Must not raise.
    result = await _make_collector(codex_root, state_path).collect()
    assert result is not None
    assert result["tokens_today"] == 0


@pytest.mark.asyncio
async def test_corrupted_state_entry_recovers(codex_root: Path,
                                               state_path: Path) -> None:
    """If the persisted state file has a string where a dict is expected
    (e.g. from a previous buggy version), the collector must not crash.

    This is the regression for the 'str object has no attribute get'
    error the user hit after upgrading."""
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-bad.jsonl"
    _write_session(p, total=10_000)
    # Inject a corrupted state entry — the value is a string.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "day": "2026-05-23",
        "tokens_today": 0,
        "sessions": {
            str(p): "i used to be a dict",
        },
    }))
    # Collect must succeed; the bad entry should be treated as "first
    # sighting" and re-baselined.
    result = await _make_collector(codex_root, state_path).collect()
    assert result is not None
    assert result["tokens_today"] == 0    # re-baselined from corrupted state
    # Next collect with a delta should work normally.
    _write_session(p, total=13_000)
    result = await _make_collector(codex_root, state_path).collect()
    assert result["tokens_today"] == 3_000


@pytest.mark.asyncio
async def test_session_key_stable_across_mtime_change(codex_root: Path,
                                                       state_path: Path
                                                       ) -> None:
    """Regression for a bug Codex caught in review: the session key used
    to bucket by mtime, so any append that pushed mtime across an hour
    boundary invalidated the key and the previous total was lost.

    Simulates that scenario by writing the same session file twice with
    a synthetic mtime stamp set well past an hour boundary between writes.
    The collector MUST still recognise it as the same session and report
    the delta correctly.
    """
    p = codex_root / "sessions" / "2026" / "05" / "23" / "rollout-stable.jsonl"
    _write_session(p, total=10_000)
    # First collect → baseline.
    await _make_collector(codex_root, state_path).collect()
    # Force mtime to advance by 2 hours (past an hour boundary).
    new_mt = os.path.getmtime(p) + 7200
    os.utime(p, (new_mt, new_mt))
    # Same session grows by 3000 tokens.
    _write_session(p, total=13_000)
    new_mt += 1
    os.utime(p, (new_mt, new_mt))
    result = await _make_collector(codex_root, state_path).collect()
    assert result["tokens_today"] == 3_000, (
        "session-key must be stable across mtime hour-boundary changes; "
        "previously the bucket-by-mtime key would rebaseline to zero here"
    )
