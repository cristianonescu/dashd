"""Rule-driven suggestion engine: each rule fires on the right state."""
from __future__ import annotations

from dashd.suggestions import SuggestionsEngine


def _sev(out, text_contains):
    for s in out:
        if text_contains.lower() in s["text"].lower():
            return s["severity"]
    return None


def test_empty_state_yields_no_suggestions():
    out = SuggestionsEngine().suggest({})
    assert out == []


def test_ram_crit_calls_out_worst_process():
    out = SuggestionsEngine().suggest({
        "system": {"ram_pct": 95, "top_ram": [{"name": "Chrome Helper"}]},
    })
    assert _sev(out, "Chrome") == "crit"


def test_ram_warn_at_85():
    out = SuggestionsEngine().suggest({"system": {"ram_pct": 85}})
    assert _sev(out, "tight") == "warn"


def test_cpu_crit_calls_out_worst_process():
    out = SuggestionsEngine().suggest({
        "system": {"cpu_pct": [90, 95, 88, 92], "top_cpu": [{"name": "ffmpeg"}]},
    })
    assert _sev(out, "ffmpeg") == "crit"


def test_battery_crit_when_unplugged():
    out = SuggestionsEngine().suggest({
        "system": {"battery_pct": 8, "battery_charging": False},
    })
    assert _sev(out, "Battery") == "crit"


def test_battery_silent_when_charging():
    out = SuggestionsEngine().suggest({
        "system": {"battery_pct": 8, "battery_charging": True},
    })
    assert all("Battery" not in s["text"] for s in out)


def test_claude_block_warns_at_75():
    out = SuggestionsEngine().suggest({
        "ai": {"claude_code": {"block_pct": 80, "block_resets_in_min": 90}},
    })
    s = _sev(out, "Claude")
    assert s == "warn"


def test_next_meeting_crit_when_imminent():
    out = SuggestionsEngine().suggest({
        "calendar": {"next_event_in_min": 1, "next_event_title": "Standup"},
    })
    assert _sev(out, "Standup") == "crit"


def test_severity_ordering_crit_first():
    out = SuggestionsEngine().suggest({
        "system": {"ram_pct": 95, "top_ram": [{"name": "Slack"}],
                   "cpu_pct": [40, 40, 40]},
        "github": {"prs_awaiting_review": 2},
    })
    # crit first, then any warn / info follows
    assert out[0]["severity"] == "crit"
    severities = [s["severity"] for s in out]
    # Sorted ladder: crit ≤ warn ≤ info
    rank = {"crit": 0, "warn": 1, "info": 2}
    ranks = [rank[s] for s in severities]
    assert ranks == sorted(ranks)


def test_top_n_clamps_output():
    eng = SuggestionsEngine(top_n=2)
    out = eng.suggest({
        "system": {"ram_pct": 95, "cpu_pct": [95]*8, "disk_pct": 96, "temp_cpu_c": 92,
                   "top_ram": [{"name": "x"}], "top_cpu": [{"name": "y"}]},
    })
    assert len(out) == 2


def test_failing_rule_does_not_break_engine():
    def bad(_): raise RuntimeError("boom")
    eng = SuggestionsEngine(rules=[bad], top_n=5)
    assert eng.suggest({"system": {"ram_pct": 50}}) == []
