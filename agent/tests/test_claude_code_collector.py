"""Tests for the ccusage-style Claude Code collector.

Builds a synthetic projects/<slug>/<session>.jsonl tree in a tmp_path
and exercises:
  - hour-floor block start
  - explicit "Claude AI usage limit reached|<epoch>" override
  - dedup by (message_id, request_id)
  - block_used_pct when a budget is configured
  - burn_tokens_per_min
  - per-project breakdown + per-model split
  - today vs week aggregation
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, time as dtime
from pathlib import Path

import pytest

from dashd.collectors.claude_code import (
    BLOCK_SECONDS,
    ClaudeCodeCollector,
    _floor_hour,
    _local_midnight_ts,
)


def _ts_iso(epoch: float) -> str:
    return datetime.utcfromtimestamp(epoch).isoformat() + "Z"


def _write_assistant(f, ts_epoch: float, *, msg_id: str, model: str,
                    input_tokens: int = 0, output_tokens: int = 0,
                    cache_create: int = 0, cache_read: int = 0,
                    request_id: str = "") -> None:
    line = {
        "type": "assistant",
        "timestamp": _ts_iso(ts_epoch),
        "uuid": msg_id,
        "message": {
            "id": msg_id,
            "request_id": request_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
            },
        },
    }
    f.write(json.dumps(line).encode() + b"\n")


def _write_limit_reached(f, ts_epoch: float, reset_at: int) -> None:
    line = {
        "type": "user",
        "timestamp": _ts_iso(ts_epoch),
        "text": f"Claude AI usage limit reached|{reset_at}",
    }
    f.write(json.dumps(line).encode() + b"\n")


@pytest.fixture
def claude_root(tmp_path: Path) -> Path:
    """Build a `claude_config_dir/projects/<slug>/session.jsonl` skeleton."""
    root = tmp_path / "claude"
    (root / "projects" / "-Users-foo-bar").mkdir(parents=True)
    return root


def _session_path(root: Path, slug: str = "-Users-foo-bar",
                  name: str = "s1.jsonl") -> Path:
    p = root / "projects" / slug / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _make_collector(root: Path, **kw) -> ClaudeCodeCollector:
    # Pin CLAUDE_CONFIG_DIR away so the test's fake root is the only source.
    os.environ["CLAUDE_CONFIG_DIR"] = ""
    return ClaudeCodeCollector(enabled=True, root=root, **kw)


@pytest.mark.asyncio
async def test_block_start_falls_back_to_hour_floor(claude_root: Path) -> None:
    """When no explicit limit-reached event is present, the block start
    is floor()'d to the hour boundary."""
    now = time.time()
    # Two assistant events in the last hour.
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        _write_assistant(f, now - 1800, msg_id="m1", model="claude-opus-4-7",
                         input_tokens=100, output_tokens=50)
        _write_assistant(f, now - 600, msg_id="m2", model="claude-opus-4-7",
                         input_tokens=200, output_tokens=100)

    result = await _make_collector(claude_root).collect()
    assert result is not None
    # block_pct == elapsed/5h, never None when we saw activity.
    assert result["block_elapsed_pct"] is not None
    # block_resets_at should be (floor-hour of first message) + 5h.
    expected_start = _floor_hour(now - 1800)
    assert result["block_resets_at"] == int(expected_start + BLOCK_SECONDS)


@pytest.mark.asyncio
async def test_limit_reached_event_overrides_heuristic(
        claude_root: Path) -> None:
    """When the JSONL has a 'Claude AI usage limit reached|<epoch>' event,
    block_resets_at must use the explicit epoch."""
    now = time.time()
    explicit_reset = int(now + 7200)  # 2h from now
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        _write_assistant(f, now - 600, msg_id="m1", model="claude-opus-4-7",
                         input_tokens=10, output_tokens=5)
        _write_limit_reached(f, now - 300, explicit_reset)

    result = await _make_collector(claude_root).collect()
    assert result["block_resets_at"] == explicit_reset
    # block_resets_in_min should match the explicit time, not the heuristic.
    assert 110 <= (result["block_resets_in_min"] or 0) <= 121


@pytest.mark.asyncio
async def test_dedup_by_message_id(claude_root: Path) -> None:
    """Re-occurring (msg_id, request_id) pairs must not double-count."""
    now = time.time()
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        _write_assistant(f, now - 300, msg_id="dup", request_id="r1",
                         model="claude-opus-4-7",
                         input_tokens=100, output_tokens=50)
        _write_assistant(f, now - 200, msg_id="dup", request_id="r1",
                         model="claude-opus-4-7",
                         input_tokens=100, output_tokens=50)  # exact dup
        _write_assistant(f, now - 100, msg_id="new", model="claude-opus-4-7",
                         input_tokens=100, output_tokens=50)
    # Use tokens_block, not tokens_today, so the assertion can't get gated
    # on midnight rollover: tokens_block scopes to the last 5h regardless
    # of the local time the test happens to run at.
    result = await _make_collector(claude_root).collect()
    assert result["tokens_block"] == 300, (
        "dedup must collapse the (dup, r1) pair: two unique events should "
        f"yield 300 tokens, got {result['tokens_block']}"
    )


@pytest.mark.asyncio
async def test_block_used_pct_with_budget(claude_root: Path) -> None:
    """When DASHD_CLAUDE_BLOCK_BUDGET is set, block_used_pct reflects
    tokens-this-block / budget * 100."""
    now = time.time()
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        _write_assistant(f, now - 300, msg_id="m1", model="claude-opus-4-7",
                         input_tokens=1000, output_tokens=500)
    collector = _make_collector(
        claude_root, block_token_budget=10_000)
    result = await collector.collect()
    # 1500 / 10000 = 15%.
    assert result["block_used_pct"] == 15
    # No budget → None.
    no_budget = _make_collector(claude_root)
    result_no = await no_budget.collect()
    assert result_no["block_used_pct"] is None


@pytest.mark.asyncio
async def test_per_project_top_3(claude_root: Path) -> None:
    """top_projects should rank by tokens, capped at 3, with the path
    slug unescaped from Claude Code's `-`-separated layout."""
    now = time.time()
    # Three projects with different token counts.
    for slug, n in (
        ("-Users-foo-big", 1000),
        ("-Users-foo-medium", 500),
        ("-Users-foo-small", 100),
    ):
        p = _session_path(claude_root, slug=slug)
        with open(p, "wb") as f:
            _write_assistant(f, now - 60, msg_id=slug + "-m",
                             model="claude-opus-4-7",
                             input_tokens=n, output_tokens=0)

    result = await _make_collector(claude_root).collect()
    names = [t["name"] for t in result["top_projects"]]
    assert names[0] == "Users/foo/big"
    assert "Users/foo/medium" in names
    assert all("-" not in n for n in names)


@pytest.mark.asyncio
async def test_burn_rate_when_block_active(claude_root: Path) -> None:
    """burn_tokens_per_min is tokens-this-block / elapsed-block-minutes."""
    now = time.time()
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        _write_assistant(f, now - 3600, msg_id="m1",
                         model="claude-opus-4-7",
                         input_tokens=6000, output_tokens=0)
    result = await _make_collector(claude_root).collect()
    # Block started at floor-hour of (now-3600). Elapsed minutes ~60.
    # Tokens ~6000 → ~100/min.
    assert result["burn_tokens_per_min"] is not None
    assert 50 <= result["burn_tokens_per_min"] <= 200


@pytest.mark.asyncio
async def test_legacy_block_pct_still_present(claude_root: Path) -> None:
    """The wire-protocol must still carry `block_pct` (= block_elapsed_pct)
    so v0.1.2 firmware reading the old field doesn't break."""
    now = time.time()
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        _write_assistant(f, now - 300, msg_id="m", model="claude-opus-4-7",
                         input_tokens=10, output_tokens=5)
    result = await _make_collector(claude_root).collect()
    assert "block_pct" in result
    assert result["block_pct"] == result["block_elapsed_pct"]


@pytest.mark.asyncio
async def test_no_data_returns_empty_block_fields(claude_root: Path) -> None:
    """An empty projects tree returns a clean reply, not None."""
    result = await _make_collector(claude_root).collect()
    # Root exists but no jsonl files. Fields are present but block info is None.
    assert result is not None
    assert result["tokens_today"] == 0
    assert result["block_elapsed_pct"] is None
    assert result["block_resets_at"] is None


@pytest.mark.asyncio
async def test_continuous_activity_segments_into_blocks(claude_root: Path) -> None:
    """Regression: a user continuously active for ~9 hours should be in
    their SECOND (current) block, not in a single 9h block that pegs
    elapsed_pct at 100% forever.

    Reproduces the bug a user reported in the wild: dashd showed
    "5h block · elapsed: 100% / 0m to reset" while Claude.ai showed
    the current session at 10% / 40m to reset. Root cause was the
    old heuristic taking `min(timestamps)` over a fixed last-5h
    window — that always resolved to ~5h ago for any user who'd
    been active that long.
    """
    now = time.time()
    p = _session_path(claude_root)
    # Simulate 9 hours of continuous activity, one message every 10 min.
    with open(p, "wb") as f:
        for i in range(54):  # 54 * 10 min = 540 min = 9h
            ts = now - (9 * 3600) + (i * 600)
            _write_assistant(f, ts, msg_id=f"m{i}", model="claude-opus-4-7",
                             input_tokens=10, output_tokens=5)
    result = await _make_collector(claude_root).collect()
    # The current block should start ~4-5h ago (after a previous block
    # closed at 5h since start), NOT 9h ago. So elapsed should be
    # somewhere in [0, 100], not pinned at 100.
    elapsed = result["block_elapsed_pct"]
    assert elapsed is not None
    assert 0 <= elapsed < 100, (
        f"block_elapsed_pct={elapsed}; should NOT be pinned at 100 "
        f"for a user with ongoing activity that spans block boundaries"
    )
    # And we should have some reset time in the future.
    assert result["block_resets_in_min"] is not None
    assert result["block_resets_in_min"] > 0


@pytest.mark.asyncio
async def test_dedup_works_when_msg_id_is_missing(claude_root: Path) -> None:
    """Regression: previous code only deduped when `msg.id` was set.
    JSONL lines with empty `msg.id` got their tokens counted multiple
    times — inflating tokens_today by orders of magnitude.

    The fix tries id → uuid → requestId → (timestamp, input_tokens)
    as fallbacks. Verify each layer."""
    now = time.time()
    p = _session_path(claude_root)

    # Write the same assistant message THREE times: once with msg.id,
    # once with only uuid, once with neither (only timestamp).
    line_a = {
        "type": "assistant",
        "timestamp": _ts_iso(now - 60),
        "uuid": "envelope-aaa",
        "message": {"id": "anth-msg-x", "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 100, "output_tokens": 50}},
    }
    line_b_uuid_only = {
        "type": "assistant",
        "timestamp": _ts_iso(now - 50),
        "uuid": "envelope-bbb",
        "message": {"model": "claude-opus-4-7",  # no id
                    "usage": {"input_tokens": 100, "output_tokens": 50}},
    }
    line_c_ts_only = {
        "type": "assistant",
        "timestamp": _ts_iso(now - 40),  # different ts → distinct
        "message": {"model": "claude-opus-4-7",  # no id, no uuid
                    "usage": {"input_tokens": 100, "output_tokens": 50}},
    }
    with open(p, "wb") as f:
        for line in [line_a, line_a,        # exact dup, same msg.id
                     line_b_uuid_only, line_b_uuid_only,  # dup, same uuid
                     line_c_ts_only,        # unique by ts+tokens
                     line_c_ts_only,        # exact dup of c
                     ]:
            f.write(json.dumps(line).encode() + b"\n")

    result = await _make_collector(claude_root).collect()
    # Three logically-distinct messages × 150 tokens = 450.
    assert result["tokens_block"] == 450, (
        f"expected 3 unique messages = 450 tokens; got {result['tokens_block']}"
    )


@pytest.mark.asyncio
async def test_idle_for_more_than_5h_means_no_active_block(
        claude_root: Path) -> None:
    """If the user hasn't been active in >5h, there's no active block."""
    now = time.time()
    p = _session_path(claude_root)
    with open(p, "wb") as f:
        # One message 7 hours ago, nothing since.
        _write_assistant(f, now - (7 * 3600), msg_id="ancient",
                         model="claude-opus-4-7",
                         input_tokens=10, output_tokens=5)
    result = await _make_collector(claude_root).collect()
    assert result["block_elapsed_pct"] is None
    assert result["block_resets_at"] is None
    assert result["block_resets_in_min"] is None
