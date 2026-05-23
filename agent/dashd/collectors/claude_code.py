"""Claude Code JSONL collector.

Scans Claude Code's session JSONLs and reports:
  - tokens / cost today (since local midnight) — `tokens_today`, `cost_today_usd`
  - tokens / cost this block (since current rate-limit window opened) —
    `tokens_block`, `cost_block_usd`
  - active 5h rate-limit block: `block_elapsed_pct`, `block_resets_in_min`,
    `block_resets_at` (epoch); plus the legacy `block_pct` for the firmware's
    old wire-protocol contract.
  - `block_used_pct` when a per-block token budget is configured
    (`[collectors.claude_code] block_token_budget = 1500000` or
    `DASHD_CLAUDE_BLOCK_BUDGET` env var).
  - per-model breakdown (`models: {opus, sonnet, haiku, ...}`)
  - top projects today (`top_projects: [{name, tokens}, ...]`)
  - 7-day aggregates: `tokens_this_week`, `cost_this_week_usd`
  - burn rate: `burn_tokens_per_min`, `burn_projected_cap_min`
    (the projection only when a budget is configured)

Key tricks borrowed from ccusage:
  - Path discovery: $CLAUDE_CONFIG_DIR (comma-separated, takes precedence),
    $XDG_CONFIG_HOME/claude (default ~/.config/claude), ~/.claude.
  - Block start floored to the hour when no explicit reset is available
    (matches what Anthropic appears to use, even though it's undocumented).
  - Explicit reset epoch parsed from "Claude AI usage limit reached|<epoch>"
    error events — when present, this is the authoritative reset time
    instead of `block_start + 5h`.
  - Deduplication by (message_id, request_id) so re-reading files
    (file truncation, glob hiccups) doesn't double-count tokens.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Iterable

from dashd.collectors.base import Collector
from dashd.pricing import cost_usd

log = logging.getLogger("dashd.claude_code")

BLOCK_SECONDS = 5 * 3600
WEEK_SECONDS = 7 * 86400

# Regex for the limit-reached marker. Format observed in real JSONL:
#   "Claude AI usage limit reached|1747987200"
# where the trailing number is an epoch second giving the reset time.
LIMIT_RESET_RE = re.compile(r"Claude AI usage limit reached\|(\d+)")


# ── Path discovery (matches ccusage's lookup chain) ────────────────────

def _candidate_roots() -> list[Path]:
    """Every directory that might contain a Claude Code `projects/` tree.

    Order (highest precedence first):
      1. `$CLAUDE_CONFIG_DIR` — honored comma-separated for users who
         keep multiple Claude installs side by side.
      2. `$XDG_CONFIG_HOME/claude` (default `~/.config/claude`).
      3. `~/.claude` — the legacy default and what dashd used through v0.1.2.

    Duplicate paths (after resolve()) are removed while preserving order.
    """
    seen: set[Path] = set()
    out: list[Path] = []

    def _add(p: Path) -> None:
        try:
            r = p.resolve()
        except OSError:
            return
        if r in seen:
            return
        seen.add(r)
        out.append(p)

    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if part:
                _add(Path(part).expanduser())
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    _add(Path(xdg) / "claude")
    _add(Path.home() / ".claude")
    return out


# ── Helpers ────────────────────────────────────────────────────────────

def _local_midnight_ts() -> float:
    now = datetime.now().astimezone()
    return datetime.combine(now.date(), dtime.min, tzinfo=now.tzinfo).timestamp()


def _floor_hour(ts: float) -> float:
    return float(int(ts // 3600) * 3600)


def _parse_ts(s: str) -> float | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _short_model(model: str) -> str:
    m = model.lower()
    for tag in ("opus", "sonnet", "haiku"):
        if tag in m:
            return tag
    return "other"


def _project_label(jsonl_path: str, projects_root: Path) -> str:
    """Derive a human-readable project label from the JSONL path.

    Claude Code lays out files like
        ~/.claude/projects/-Users-foo-bar-baz/SESSION-ID.jsonl
    The leading directory under `projects/` is a slug derived from the
    absolute cwd; we extract its first segment and turn `-` separators
    back into `/` so it reads like a normal path tail.
    """
    try:
        rel = Path(jsonl_path).relative_to(projects_root)
    except ValueError:
        return Path(jsonl_path).parent.name
    parts = rel.parts
    if not parts:
        return "?"
    slug = parts[0]
    if slug.startswith("-"):
        slug = slug[1:]
    return slug.replace("-", "/")


def _find_current_block_start(timestamps: list[float], now: float) -> float | None:
    """Identify the start of the currently-active 5h block via ccusage's
    segmentation algorithm.

    Walks the timestamps chronologically and opens a new block whenever
    an entry's gap from the current block's start, OR from the previous
    entry, exceeds 5h. Block starts are floored to the hour boundary
    (matches what Anthropic appears to use though they don't document it).

    Returns None when the most recent block has expired — i.e. the user
    has been idle for >5h. dashd will then show "no active block"
    instead of stale block info.

    THIS REPLACES the previous "earliest message in last 5h, hour-floor"
    heuristic which was wrong for users continuously active >5h. Their
    earliest in-window message was always ~5h ago, so block_elapsed_pct
    pegged at 100% forever even though the actual Anthropic block was
    far from over.
    """
    if not timestamps:
        return None
    ts_sorted = sorted(timestamps)
    block_start = _floor_hour(ts_sorted[0])
    last_ts = ts_sorted[0]
    for ts in ts_sorted[1:]:
        # New block if we crossed 5h since this block's start OR since
        # the previous message. Either condition closes the current block
        # and opens a new one floored to the hour of `ts`.
        if (ts - block_start > BLOCK_SECONDS
                or ts - last_ts > BLOCK_SECONDS):
            block_start = _floor_hour(ts)
        last_ts = ts
    # If the user has been idle for >5h, the most recent block has
    # closed and no new one has started. Report no active block.
    if now - last_ts > BLOCK_SECONDS:
        return None
    return block_start


def _config_block_budget(extra: dict | None) -> int | None:
    """Per-block token budget from env or config.toml. Env wins."""
    env = os.environ.get("DASHD_CLAUDE_BLOCK_BUDGET", "").strip()
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    if isinstance(extra, dict):
        for k in ("block_token_budget", "blockTokenBudget"):
            v = extra.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
    return None


# ── Per-collect accumulator ────────────────────────────────────────────

@dataclass
class _Tally:
    """Side-channel for `_scan_file` so we don't pollute module state.

    A fresh `_Tally` is constructed at the start of every `collect()` call
    and discarded at the end — no concurrency hazards, no leakage between
    ticks.
    """
    midnight: float
    week_start: float
    block_window_start: float
    now: float

    tokens_today: int = 0
    cost_today: float = 0.0
    tokens_week: int = 0
    cost_week: float = 0.0
    per_model: dict[str, int] = field(default_factory=dict)
    per_project: dict[str, int] = field(default_factory=dict)
    # Bucketed by floor-to-hour so we can re-tally block-only stats once we
    # know which hour-bucket the block actually starts in.
    tokens_by_hour: dict[float, int] = field(default_factory=dict)
    cost_by_hour: dict[float, float] = field(default_factory=dict)
    # All assistant timestamps within the last ~24 hours. We segment
    # these into 5h blocks (ccusage-style) to find the CURRENT block —
    # not the earliest message in the last 5h, which was wrong when the
    # user is continuously active across multiple blocks.
    recent_ts: list[float] = field(default_factory=list)
    # Highest "Claude AI usage limit reached|<epoch>" timestamp we saw.
    limit_epoch: float | None = None
    # Dedup keys.
    seen: set[tuple[str, str]] = field(default_factory=set)

    def note_limit_reset(self, epoch: float) -> None:
        if self.limit_epoch is None or epoch > self.limit_epoch:
            self.limit_epoch = epoch


# ── Collector ──────────────────────────────────────────────────────────

class ClaudeCodeCollector(Collector):
    key = "ai.claude_code"

    def __init__(self, enabled: bool = True, root: Path | None = None,
                 rate_overrides: dict | None = None,
                 block_token_budget: int | None = None) -> None:
        super().__init__(enabled)
        # Backwards-compat: callers used to pass an explicit `root`. Keep
        # honoring it as the FIRST entry of the discovery chain so existing
        # tests work, but always also consult the default chain.
        self._extra_root: Path | None = root
        self.rate_overrides = rate_overrides or {}
        self._block_token_budget = block_token_budget

    def candidate_roots(self) -> list[Path]:
        # If the caller passed an explicit `root`, treat it as the ONLY
        # source — needed for tests + the legacy single-path config. We
        # don't want a test's fixture root to be polluted by the host's
        # real ~/.claude data.
        if self._extra_root is not None:
            return [self._extra_root]
        return _candidate_roots()

    async def collect(self) -> dict[str, Any] | None:
        budget = (
            self._block_token_budget
            or _config_block_budget(self.rate_overrides))
        now = time.time()
        midnight = _local_midnight_ts()
        week_start = now - WEEK_SECONDS
        block_window_start = now - BLOCK_SECONDS

        tally = _Tally(
            midnight=midnight, week_start=week_start,
            block_window_start=block_window_start, now=now)

        any_root = False
        for root in self.candidate_roots():
            projects = root / "projects"
            if not projects.is_dir():
                continue
            any_root = True
            scan_cutoff = min(midnight, block_window_start, week_start)
            for jsonl in self._iter_files(projects, scan_cutoff):
                self._scan_file(jsonl, projects, tally=tally)
        if not any_root:
            return None

        # ── Block start: explicit reset (preferred) → segmentation
        # algorithm (ccusage-style) for the active block.
        block_start: float | None = None
        block_resets_at: float | None = None
        if tally.limit_epoch is not None and tally.limit_epoch > now:
            # Authoritative: Anthropic's "Claude AI usage limit reached"
            # event in the JSONL pins the reset time exactly.
            block_resets_at = tally.limit_epoch
            block_start = block_resets_at - BLOCK_SECONDS
        else:
            block_start = _find_current_block_start(tally.recent_ts, now)
            if block_start is not None:
                block_resets_at = block_start + BLOCK_SECONDS

        block_elapsed_pct: int | None = None
        block_resets_in_min: int | None = None
        if block_start is not None and block_resets_at is not None:
            elapsed = max(0.0, now - block_start)
            block_elapsed_pct = max(0, min(100,
                                            int(elapsed * 100 / BLOCK_SECONDS)))
            block_resets_in_min = max(0, int((block_resets_at - now) / 60))

        # Aggregate block-only stats from the per-hour buckets.
        tokens_block = 0
        cost_block = 0.0
        if block_start is not None:
            for hour, n in tally.tokens_by_hour.items():
                if hour >= block_start:
                    tokens_block += n
            for hour, c in tally.cost_by_hour.items():
                if hour >= block_start:
                    cost_block += c

        # block_used_pct: only meaningful if the user set a budget.
        block_used_pct: int | None = None
        if budget and budget > 0:
            block_used_pct = max(0, min(100, int(tokens_block * 100 / budget)))

        # Burn rate over the elapsed portion of this block.
        burn_tokens_per_min: int | None = None
        burn_projected_cap_min: int | None = None
        if block_start is not None:
            elapsed_min = max(1.0, (now - block_start) / 60.0)
            burn_tokens_per_min = int(tokens_block / elapsed_min)
            if budget and budget > 0 and burn_tokens_per_min > 0:
                remaining = max(0, budget - tokens_block)
                burn_projected_cap_min = int(remaining / burn_tokens_per_min)

        top_projects = sorted(tally.per_project.items(),
                              key=lambda kv: kv[1], reverse=True)[:3]

        return {
            # Today
            "tokens_today": tally.tokens_today,
            "cost_today_usd": round(tally.cost_today, 2),
            # This block (since current window opened)
            "tokens_block": tokens_block,
            "cost_block_usd": round(cost_block, 2),
            # Legacy field — duplicates block_elapsed_pct so the firmware
            # that reads `block_pct` keeps working without a wire change.
            "block_pct": block_elapsed_pct,
            "block_elapsed_pct": block_elapsed_pct,
            "block_used_pct": block_used_pct,
            "block_resets_in_min": block_resets_in_min,
            "block_resets_at": (
                int(block_resets_at) if block_resets_at is not None else None),
            # Last 7 days
            "tokens_this_week": tally.tokens_week,
            "cost_this_week_usd": round(tally.cost_week, 2),
            # Per-model + per-project
            "models": {k: v for k, v in tally.per_model.items() if v > 0},
            "top_projects": [
                {"name": name, "tokens": n} for name, n in top_projects
            ],
            # Burn rate
            "burn_tokens_per_min": burn_tokens_per_min,
            "burn_projected_cap_min": burn_projected_cap_min,
        }

    # ── File scanning ─────────────────────────────────────────────────

    def _iter_files(self, projects: Path, scan_cutoff: float) -> Iterable[str]:
        pattern = str(projects / "**" / "*.jsonl")
        for p in glob.iglob(pattern, recursive=True):
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt < scan_cutoff:
                continue
            yield p

    def _scan_file(self, path: str, projects: Path, *, tally: _Tally) -> None:
        proj = _project_label(path, projects)
        try:
            with open(path, "rb") as f:
                for raw in f:
                    # Fast-rejection saves a json.loads on every line.
                    has_usage = b'"usage"' in raw
                    has_limit = b'limit reached' in raw
                    if not (has_usage or has_limit):
                        continue
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if has_limit:
                        self._capture_limit_reset(d, tally)
                    if not has_usage:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message") or {}
                    usage = msg.get("usage") or {}
                    if not usage:
                        continue
                    ts = _parse_ts(d.get("timestamp", ""))
                    # Dedup chain. The previous code only deduped when
                    # `msg.id` was populated — which silently skipped
                    # dedup for any JSONL line where the assistant
                    # message was a tool-result or had no Anthropic-side
                    # message ID. Those lines then got their tokens
                    # counted every time the file was scanned, inflating
                    # tokens_today by orders of magnitude (505M on a
                    # user where the truth was ~15K — see codexbar
                    # comparison).
                    #
                    # Try identifiers in order of stability:
                    #   1. msg.id          — Anthropic API message id
                    #   2. d.uuid          — Claude Code envelope id
                    #                        (differs from msg.id on retries)
                    #   3. d.requestId     — Claude Code's outbound id
                    #   4. (timestamp, input_tokens) — probabilistic
                    #                        fallback; unique within a
                    #                        session at second resolution.
                    msg_id = (
                        msg.get("id")
                        or d.get("uuid")
                        or d.get("requestId")
                        or (f"ts:{d.get('timestamp')}|"
                            f"in:{usage.get('input_tokens', 0)}|"
                            f"out:{usage.get('output_tokens', 0)}")
                    )
                    req_id = msg.get("request_id") or d.get("requestId") or ""
                    key = (str(msg_id), str(req_id))
                    if key in tally.seen:
                        continue
                    tally.seen.add(key)


                    if ts is None:
                        continue
                    model = msg.get("model") or "unknown"
                    short = _short_model(model)
                    tokens = (
                        (usage.get("input_tokens") or 0)
                        + (usage.get("output_tokens") or 0)
                        + (usage.get("cache_creation_input_tokens") or 0)
                        + (usage.get("cache_read_input_tokens") or 0)
                    )
                    call_cost = cost_usd(model, usage,
                                          overrides=self.rate_overrides)

                    # Collect timestamps within the last 24h. We need a
                    # window wider than 5h so the block-segmentation
                    # algorithm can correctly identify the CURRENT block
                    # boundary even when the user has been continuously
                    # active across multiple consecutive blocks. The
                    # previous "last 5h" window meant `min()` over those
                    # timestamps always resolved to ~5h ago, which
                    # incorrectly pegged block_elapsed_pct at 100% for
                    # anyone working >5h continuously.
                    if ts >= tally.now - 86400:
                        tally.recent_ts.append(ts)
                        hour = _floor_hour(ts)
                        tally.tokens_by_hour[hour] = (
                            tally.tokens_by_hour.get(hour, 0) + tokens)
                        tally.cost_by_hour[hour] = (
                            tally.cost_by_hour.get(hour, 0.0) + call_cost)
                    if ts >= tally.midnight:
                        tally.tokens_today += tokens
                        tally.cost_today += call_cost
                        tally.per_model[short] = (
                            tally.per_model.get(short, 0) + tokens)
                        tally.per_project[proj] = (
                            tally.per_project.get(proj, 0) + tokens)
                    if ts >= tally.week_start:
                        tally.tokens_week += tokens
                        tally.cost_week += call_cost
        except OSError:
            pass

    @staticmethod
    def _capture_limit_reset(d: dict, tally: _Tally) -> None:
        """If `d` is a "Claude AI usage limit reached" event, pull its
        trailing epoch out and feed it to the tally."""
        # The marker appears as plain text in a handful of likely fields.
        # We don't walk the whole dict — cheaper to check known paths.
        candidates: list[Any] = []
        for k in ("message", "error", "text", "content"):
            candidates.append(d.get(k))
        msg = d.get("message")
        if isinstance(msg, dict):
            candidates.extend([msg.get("content"), msg.get("text")])
        for c in candidates:
            if isinstance(c, str):
                m = LIMIT_RESET_RE.search(c)
                if m:
                    try:
                        tally.note_limit_reset(float(m.group(1)))
                    except (ValueError, TypeError):
                        pass
                    return
