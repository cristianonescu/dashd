"""Dotted-key nesting for ai.* / messages.* collectors."""
from __future__ import annotations

import pytest

from dashd.aggregator import Aggregator, _set_path
from dashd.collectors.base import Collector


class Nested(Collector):
    key = "ai.claude_code"

    async def collect(self):
        return {"tokens_today": 1}


class Top(Collector):
    key = "system"

    async def collect(self):
        return {"cpu_pct": [1]}


def test_set_path_creates_intermediate_dicts():
    p: dict = {}
    _set_path(p, "messages.slack", {"unread": 4})
    _set_path(p, "messages.teams", {"unread": 2})
    _set_path(p, "git", {"branch": "main"})
    assert p == {"messages": {"slack": {"unread": 4}, "teams": {"unread": 2}}, "git": {"branch": "main"}}


@pytest.mark.asyncio
async def test_aggregator_nests_dotted_keys():
    agg = Aggregator([Nested(enabled=True), Top(enabled=True)])
    result = await agg.gather()
    assert result == {"ai": {"claude_code": {"tokens_today": 1}}, "system": {"cpu_pct": [1]}}
