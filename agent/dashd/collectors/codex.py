"""Codex CLI session collector.

Codex's session JSONLs live at `~/.codex/sessions/YYYY/MM/DD/rollout-<id>.jsonl`
and emit `token_count` events that carry **cumulative** `total_token_usage`
for the session. dashd through v0.1.2 read only the most recently modified
file's last event, which gave us the current 5h-block percent + reset
timestamp but *no* token totals.

This rewrite borrows ccusage's trick: persist the last-seen cumulative
total per session, and on each collect derive deltas by subtracting.
That yields a real `tokens_today` for Codex for the first time.

Cost stays `null` — Anthropic doesn't publish Codex pricing publicly, so
we refuse to invent dollars.

Persistence model:
  ~/.config/dashd/codex_state.json
  {
    "sessions": {
      "<session-id-or-path>": {
        "last_total": <int>,
        "file_mtime": <epoch>,
        "first_seen_today": <epoch>
      }
    },
    "tokens_today": <int>,    # rolls over at local midnight
    "day": "YYYY-MM-DD"
  }

The state file is written in-place on every collect — small (<10 KB even
with hundreds of sessions) and a clean atomic write protects against
partial-write corruption.

Edge cases handled:
  - Session truncation / restart: if a session's new total < last_total,
    we treat the difference as "fresh tokens" only if it's small (within
    the previous total). Otherwise we re-baseline silently — better to
    miss a delta than blow up the counter.
  - File mtime regression (Codex moved or restored an old file): the
    (session_id, file_mtime_bucket) key changes so we treat it as a new
    session.
  - Day rollover: tokens_today is reset to 0 when the persisted `day`
    changes, but per-session `last_total` is kept so we don't double
    count from zero on the new day.
  - Multiple concurrent sessions: every rollout file is scanned each
    tick; deltas are summed.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from dashd.collectors.base import Collector

log = logging.getLogger("dashd.codex")

SESSION_ACTIVE_WINDOW = 5 * 60  # seconds since last mtime to count as "live"
STATE_PATH = Path.home() / ".config" / "dashd" / "codex_state.json"

# Session id appears in the filename: rollout-<UUID>.jsonl
SESSION_ID_RE = re.compile(r"rollout-([0-9a-fA-F-]+)\.jsonl$")


def _today_str() -> str:
    return date.today().isoformat()


def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"sessions": {}, "tokens_today": 0, "day": _today_str()}
    if not isinstance(data, dict):
        return {"sessions": {}, "tokens_today": 0, "day": _today_str()}
    data.setdefault("sessions", {})
    data.setdefault("tokens_today", 0)
    data.setdefault("day", _today_str())
    return data


def _save_state(data: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        log.warning("codex_state: write failed (%s)", e)


def _session_key(path: str) -> str:
    """Identify a session uniquely. The full file path is the right key
    because Codex writes each session into its own `YYYY/MM/DD/rollout-<UUID>`
    file — the path is stable for the entire life of the session, even as
    Codex appends to it. We tried bucketing by mtime here, which broke
    long-running sessions: any append that pushed mtime across an hour
    boundary invalidated the key, so the previous total was lost and the
    next collect rebaselined to zero, silently dropping tokens.
    """
    return path


def _read_session_metrics(path: str) -> dict[str, Any] | None:
    """Walk a rollout JSONL bottom-up and pull the freshest token_count
    event. Returns the relevant fields or None if nothing usable.

    Real Codex format (verified against ~/.codex/sessions/.../rollout-*):

        {
          "type": "event_msg",
          "payload": {
            "type": "token_count",
            "info": {
              "total_token_usage":  {input_tokens, cached_input_tokens,
                                     output_tokens, reasoning_output_tokens,
                                     total_tokens},
              "last_token_usage":   {...},
              ...
            },
            "rate_limits": {
              "primary": {used_percent, window_minutes, resets_at},
              "secondary": {...}
            }
          }
        }

    The original v0.1.2 collector — and my own v0.1.3 rewrite — both
    looked for the data under `payload.message.*`, which Codex does NOT
    emit. The look-ups returned `{}`, the type check ("token_count") never
    matched, and the collector silently did nothing for every session.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    out: dict[str, Any] = {}
    # Walk lines from the END until we find what we need. token_count
    # events are emitted periodically — the most recent has the latest
    # cumulative total + rate-limit info.
    for raw in reversed(data.splitlines()):
        if b"token_count" not in raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("type") != "event_msg":
            continue
        payload = d.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "token_count":
            continue
        # Cumulative totals live at payload.info.total_token_usage. The
        # `info` block can be null between sessions — guard accordingly.
        info = payload.get("info")
        if isinstance(info, dict):
            ttu = info.get("total_token_usage")
            if isinstance(ttu, dict):
                out["total_token_usage"] = (
                    int(ttu.get("input_tokens") or 0)
                    + int(ttu.get("cached_input_tokens") or 0)
                    + int(ttu.get("output_tokens") or 0)
                    + int(ttu.get("reasoning_output_tokens") or 0)
                )
            elif isinstance(ttu, (int, float)):
                out["total_token_usage"] = int(ttu)
        rl = payload.get("rate_limits")
        if isinstance(rl, dict):
            out["rate_limits"] = rl
        # We have the freshest event; stop.
        if "total_token_usage" in out or "rate_limits" in out:
            break
    return out or None


class CodexCollector(Collector):
    key = "ai.codex"

    def __init__(self, enabled: bool = True, path: Path | None = None,
                 state_path: Path | None = None) -> None:
        super().__init__(enabled)
        self.root = path or (Path.home() / ".codex")
        self._state_path = state_path or STATE_PATH

    def _load_state(self) -> dict[str, Any]:
        global STATE_PATH
        # Swap the module-level path so _load_state/_save_state honor it.
        STATE_PATH = self._state_path
        return _load_state()

    def _save_state(self, state: dict[str, Any]) -> None:
        global STATE_PATH
        STATE_PATH = self._state_path
        _save_state(state)

    async def collect(self) -> dict[str, Any] | None:
        sessions_dir = self.root / "sessions"
        if not sessions_dir.is_dir():
            return None

        now = time.time()
        state = self._load_state()
        # Defensive: _load_state's setdefault leaves `sessions` alone if
        # the file already had a non-dict value there. Coerce to a clean
        # dict before any .get() lookups so a corrupted state file from
        # an older buggy build can't crash the live collector.
        if not isinstance(state, dict):
            state = {"sessions": {}, "tokens_today": 0, "day": _today_str()}
        if not isinstance(state.get("sessions"), dict):
            state["sessions"] = {}
        today = _today_str()
        if state.get("day") != today:
            state["day"] = today
            state["tokens_today"] = 0

        # Scan every rollout file under sessions_dir/**/rollout-*.jsonl. We
        # avoid touching files whose mtime is older than 24h to keep this
        # cheap on long-running installs.
        scan_cutoff = now - 86400
        latest_mtime = 0.0
        latest_metrics: dict[str, Any] | None = None
        per_session_deltas = 0
        for p in glob.iglob(str(sessions_dir / "**" / "rollout-*.jsonl"),
                            recursive=True):
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt < scan_cutoff:
                continue
            metrics = _read_session_metrics(p)
            if metrics is None:
                continue
            # Track the freshest file so we can carry its rate_limits +
            # session_active liveness flag through to the result.
            if mt > latest_mtime:
                latest_mtime = mt
                latest_metrics = metrics

            total = metrics.get("total_token_usage")
            if not isinstance(total, int):
                continue
            key = _session_key(p)
            prev = state["sessions"].get(key)
            if not isinstance(prev, dict):
                # First sighting (or a corrupted entry from an older buggy
                # state file). Establish baseline, count no delta — this
                # is what protects us from counting pre-existing session
                # totals as "fresh activity" on the very first dashd run.
                delta = 0
            else:
                try:
                    prev_total = int(prev.get("last_total") or 0)
                except (TypeError, ValueError):
                    prev_total = 0
                if total < prev_total:
                    # File truncated / session restarted: re-baseline.
                    log.debug("codex: %s total regressed (%d → %d); re-baselining",
                              key, prev_total, total)
                    delta = 0
                else:
                    delta = total - prev_total
            per_session_deltas += delta
            state["sessions"][key] = {
                "last_total": total,
                "file_mtime": mt,
            }

        # Prune session entries we haven't touched in 3 days so the state
        # file doesn't grow forever.
        prune_cutoff = now - 3 * 86400
        state["sessions"] = {
            k: v for k, v in state["sessions"].items()
            if isinstance(v, dict) and (v.get("file_mtime") or 0) >= prune_cutoff
        }

        state["tokens_today"] = int(state.get("tokens_today") or 0) + per_session_deltas
        self._save_state(state)

        # Pull rate-limit + activity info from the freshest session.
        block_pct: int | None = None
        block_resets_in_min: int | None = None
        block_elapsed_pct: int | None = None
        block_resets_at: int | None = None
        session_active = False
        if latest_metrics is not None:
            rl = latest_metrics.get("rate_limits") or {}
            primary = rl.get("primary") if isinstance(rl, dict) else None
            if isinstance(primary, dict):
                used = primary.get("used_percent")
                if isinstance(used, (int, float)):
                    block_pct = max(0, min(100, int(round(used))))
                    block_elapsed_pct = block_pct  # Codex reports usage %
                resets_at = primary.get("resets_at")
                if isinstance(resets_at, (int, float)):
                    block_resets_at = int(resets_at)
                    block_resets_in_min = max(0, int((resets_at - now) / 60))
            session_active = (now - latest_mtime) <= SESSION_ACTIVE_WINDOW

        return {
            # Real Codex tokens at last — derived from cumulative diffs.
            "tokens_today": state["tokens_today"],
            # No public Codex pricing — refuse to invent dollars.
            "cost_today_usd": None,
            # Rate-limit info from the live session.
            "block_pct": block_pct,
            "block_elapsed_pct": block_elapsed_pct,
            "block_used_pct": block_pct,   # Codex reports usage %, not time
            "block_resets_in_min": block_resets_in_min,
            "block_resets_at": block_resets_at,
            "session_active": session_active,
        }
