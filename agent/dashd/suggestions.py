"""Real-time, rule-based suggestions for the user.

Runs every aggregator tick after the collectors fill in their slots. Each
rule looks at the latest state payload and may return one suggestion
(`severity`, `text`). The engine sorts by severity (crit > warn > info)
and returns the top N for the firmware to render on the Tips page.

Severity ladder:
    "crit" — needs the user's attention now
    "warn" — something to watch
    "info" — informational nudge

Keep the text under ~36 chars when possible so it fits on one display row
at the default text scale.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

Suggestion = dict   # {"severity": "crit"|"warn"|"info", "text": str}

_SEVERITY_ORDER = {"crit": 0, "warn": 1, "info": 2}


def _truncate(s: str, n: int = 14) -> str:
    if not s:
        return "?"
    return s[: n - 1] + "…" if len(s) > n else s


def _ram_pressure(state: dict[str, Any]) -> Suggestion | None:
    sys = state.get("system") or {}
    # Prefer the cache-excluded "real" pressure metric when the collector
    # supplied it (macOS does; Linux/Windows fall back to ram_pct).
    pct = sys.get("ram_pressure_pct")
    if pct is None:
        pct = sys.get("ram_pct")
    if pct is None:
        return None
    if pct >= 92:
        top = (sys.get("top_ram") or [None])[0]
        worst = _truncate(top["name"]) if top else "biggest app"
        return {"severity": "crit",
                "text": f"RAM {pct}% — close {worst}"}
    if pct >= 80:
        return {"severity": "warn",
                "text": f"RAM tight ({pct}%) — consider closing tabs"}
    return None


def _cpu_pressure(state: dict[str, Any]) -> Suggestion | None:
    sys = state.get("system") or {}
    cpus = sys.get("cpu_pct") or []
    if not cpus:
        return None
    avg = sum(cpus) / len(cpus)
    if avg >= 80:
        top = (sys.get("top_cpu") or [None])[0]
        worst = _truncate(top["name"]) if top else "top process"
        return {"severity": "crit", "text": f"CPU {int(avg)}% — {worst} pegged"}
    if avg >= 60:
        return {"severity": "warn",
                "text": f"CPU {int(avg)}% — pause heavy work"}
    return None


def _disk_pressure(state: dict[str, Any]) -> Suggestion | None:
    sys = state.get("system") or {}
    pct = sys.get("disk_pct")
    if pct is None:
        return None
    if pct >= 95:
        return {"severity": "crit", "text": f"Disk {pct}% full"}
    if pct >= 85:
        return {"severity": "warn", "text": f"Disk {pct}% — clean caches"}
    return None


def _battery(state: dict[str, Any]) -> Suggestion | None:
    sys = state.get("system") or {}
    pct = sys.get("battery_pct")
    charging = sys.get("battery_charging")
    if pct is None:
        return None
    if pct <= 10 and charging is not True:
        return {"severity": "crit", "text": f"Battery {pct}% — plug in"}
    if pct <= 20 and charging is not True:
        return {"severity": "warn", "text": f"Battery {pct}% — find a charger"}
    return None


def _claude_block(state: dict[str, Any]) -> Suggestion | None:
    cc = ((state.get("ai") or {}).get("claude_code")) or {}
    pct = cc.get("block_pct")
    if pct is None:
        return None
    if pct >= 90:
        return {"severity": "crit",
                "text": f"Claude block {pct}% — slow down"}
    if pct >= 75:
        mins = cc.get("block_resets_in_min")
        suffix = f" ({mins}m to reset)" if mins is not None else ""
        return {"severity": "warn",
                "text": f"Claude block {pct}%{suffix}"}
    return None


def _codex_block(state: dict[str, Any]) -> Suggestion | None:
    cx = ((state.get("ai") or {}).get("codex")) or {}
    pct = cx.get("block_pct")
    if pct is None:
        return None
    if pct >= 90:
        return {"severity": "crit", "text": f"Codex block {pct}% — slow down"}
    if pct >= 75:
        return {"severity": "warn", "text": f"Codex block {pct}%"}
    return None


def _temp(state: dict[str, Any]) -> Suggestion | None:
    sys = state.get("system") or {}
    t = sys.get("temp_cpu_c")
    if t is None:
        return None
    if t >= 90:
        return {"severity": "crit", "text": f"CPU {t:.0f}°C — thermal throttle"}
    if t >= 80:
        return {"severity": "warn", "text": f"CPU {t:.0f}°C — running hot"}
    return None


def _next_meeting(state: dict[str, Any]) -> Suggestion | None:
    cal = state.get("calendar") or {}
    in_min = cal.get("next_event_in_min")
    title = cal.get("next_event_title")
    if in_min is None:
        return None
    if in_min <= 2:
        return {"severity": "crit", "text": f"Meeting NOW: {_truncate(title or '', 22)}"}
    if in_min <= 5:
        return {"severity": "warn", "text": f"In {in_min}m: {_truncate(title or '', 22)}"}
    return None


def _commit_freshness(state: dict[str, Any]) -> Suggestion | None:
    git = state.get("git") or {}
    m = git.get("minutes_since_last_commit")
    if m is None:
        return None
    if m >= 180:
        return {"severity": "warn", "text": f"No commit in {m // 60}h — checkpoint?"}
    return None


def _prs_awaiting(state: dict[str, Any]) -> Suggestion | None:
    gh = state.get("github") or {}
    n = gh.get("prs_awaiting_review")
    if n is None or n <= 0:
        return None
    if n >= 5:
        return {"severity": "warn", "text": f"{n} PRs awaiting your review"}
    if n >= 1:
        return {"severity": "info", "text": f"{n} PR{'s' if n != 1 else ''} need review"}
    return None


def _ci_failures(state: dict[str, Any]) -> Suggestion | None:
    gh = state.get("github") or {}
    n = gh.get("ci_failures_24h")
    if n is None or n <= 0:
        return None
    if n >= 3:
        return {"severity": "crit", "text": f"{n} CI failures (24h)"}
    return {"severity": "warn", "text": f"{n} CI failure{'s' if n != 1 else ''} (24h)"}


def _memory_leak(state: dict[str, Any]) -> Suggestion | None:
    leak = (state.get("system") or {}).get("memory_leak")
    if not leak:
        return None
    name = _truncate(str(leak.get("name") or "?"), 18)
    delta = int(leak.get("delta_mb") or 0)
    window = leak.get("window_min") or 5
    sev = "warn" if delta < 500 else "crit"
    return {"severity": sev,
            "text": f"{name} +{delta} MB / {window:g}m"}


# Order matters only for deterministic ties — actual ranking is by severity.
RULES: list[Callable[[dict[str, Any]], Suggestion | None]] = [
    _battery, _ram_pressure, _memory_leak, _cpu_pressure, _temp,
    _claude_block, _codex_block, _next_meeting,
    _disk_pressure, _ci_failures, _prs_awaiting, _commit_freshness,
]


class SuggestionsEngine:
    """Stateless aggregator over RULES. Lives on the agent process and is
    invoked once per aggregator tick with the just-built payload."""

    def __init__(self, rules: Iterable[Callable] | None = None, top_n: int = 5) -> None:
        self.rules = list(rules) if rules is not None else RULES
        self.top_n = top_n

    def suggest(self, state: dict[str, Any]) -> list[Suggestion]:
        out: list[Suggestion] = []
        for rule in self.rules:
            try:
                s = rule(state)
            except Exception:
                continue
            if s and s.get("text"):
                out.append({"severity": s.get("severity", "info"), "text": s["text"]})
        out.sort(key=lambda s: _SEVERITY_ORDER.get(s["severity"], 99))
        return out[: self.top_n]
