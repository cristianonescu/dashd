"""Maps state changes to pet animation cmds.

Stateless-ish: keeps a small "last seen" snapshot so it can react to *changes*
(e.g. a fresh commit, a new CI failure, the moment a meeting drops under 5 min)
rather than firing once per tick. Returns the cmd dicts the runtime should
forward to the device — keeps the IO concerns out of this module.
"""
from __future__ import annotations

import time
from typing import Any


class PetReactor:
    # Rule cooldown so the same trigger doesn't spam the device.
    COOLDOWN_SEC = 8.0
    # Idle (no state change of interest) for this long → "waiting" anim.
    IDLE_AFTER_SEC = 60.0

    def __init__(self) -> None:
        self._last_commits: int | None = None
        self._last_ci_failures: int | None = None
        self._last_block_pct: int | None = None
        self._last_event_t = time.time()
        self._last_emit_t = 0.0
        self._current_state = "idle"

    def react(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Inspect `state`, return a `pet_set_state` cmd dict or None."""
        next_state: str | None = None

        sys = state.get("system") or {}
        git = state.get("git") or {}
        github = state.get("github") or {}
        cal = state.get("calendar") or {}
        ai = (state.get("ai") or {}).get("claude_code") or {}

        # Commit landed → wave (positive feedback).
        commits = git.get("commits_today")
        if commits is not None and self._last_commits is not None and commits > self._last_commits:
            next_state = "wave"
        self._last_commits = commits if commits is not None else self._last_commits

        # New CI failure → "failed".
        ci = github.get("ci_failures_24h")
        if ci is not None and self._last_ci_failures is not None and ci > self._last_ci_failures:
            next_state = "failed"
        self._last_ci_failures = ci if ci is not None else self._last_ci_failures

        # Meeting in <= 5 min → jump (gets your attention).
        next_in_min = cal.get("next_event_in_min")
        if isinstance(next_in_min, (int, float)) and 0 <= next_in_min <= 5:
            next_state = "jump"

        # AI block ≥ 75 % → "review" (the "stop and think" anim).
        block_pct = ai.get("block_pct")
        if isinstance(block_pct, (int, float)) and block_pct >= 75:
            next_state = "review"

        # System pressure → run.
        cpu = sys.get("cpu_pct") or []
        avg_cpu = (sum(cpu) / len(cpu)) if cpu else 0
        ram = sys.get("ram_pct") or 0
        if avg_cpu >= 80 or ram >= 92:
            next_state = "running"

        now = time.time()
        # If nothing interesting changed, decay to idle/waiting.
        if next_state is None:
            since = now - self._last_event_t
            if since >= self.IDLE_AFTER_SEC and self._current_state != "waiting":
                next_state = "waiting"
            elif since < self.IDLE_AFTER_SEC and self._current_state not in ("idle", "waiting"):
                next_state = "idle"
        else:
            self._last_event_t = now

        # Cooldown so we don't ping-pong.
        if next_state and next_state != self._current_state and (now - self._last_emit_t) >= self.COOLDOWN_SEC * 0.5:
            self._current_state = next_state
            self._last_emit_t = now
            return {"type": "cmd", "name": "pet_set_state", "state": next_state}
        return None
