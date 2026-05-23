"""A failing collector must never sink the rest."""
from __future__ import annotations

import pytest

from dashd.aggregator import Aggregator
from dashd.collectors.base import Collector


class GoodCollector(Collector):
    key = "good"

    async def collect(self):
        return {"v": 1}


class BadCollector(Collector):
    key = "bad"

    async def collect(self):
        raise RuntimeError("boom")


class DisabledCollector(Collector):
    key = "off"

    async def collect(self):
        return {"v": 99}


@pytest.mark.asyncio
async def test_failing_collector_does_not_break_others():
    agg = Aggregator([GoodCollector(enabled=True), BadCollector(enabled=True)])
    result = await agg.gather()
    assert result == {"good": {"v": 1}, "bad": None}


@pytest.mark.asyncio
async def test_disabled_collector_returns_none():
    agg = Aggregator([DisabledCollector(enabled=False)])
    result = await agg.gather()
    assert result == {"off": None}
