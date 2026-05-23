"""Placeholder for the slack collector (Phase 3)."""
from __future__ import annotations

from typing import Any

from dashd.collectors.base import Collector


class SlackCollector(Collector):
    key = "slack"

    async def collect(self) -> dict[str, Any] | None:
        return None
