"""Run every enabled collector in parallel and assemble one state payload.

A failing collector is logged and its slot set to None — never propagates.
Collector `key` may be dotted (e.g. "ai.claude_code", "messages.slack") and
the aggregator builds the nested object accordingly.
"""
from __future__ import annotations

import asyncio
from typing import Any

from dashd.collectors.base import Collector


def _set_path(payload: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    obj = payload
    for p in parts[:-1]:
        nxt = obj.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            obj[p] = nxt
        obj = nxt
    obj[parts[-1]] = value


class Aggregator:
    def __init__(self, collectors: list[Collector]) -> None:
        self.collectors = collectors

    async def gather(self) -> dict[str, Any]:
        results = await asyncio.gather(*(c.safe_collect() for c in self.collectors))
        payload: dict[str, Any] = {}
        for c, r in zip(self.collectors, results):
            _set_path(payload, c.key, r)
        return payload
