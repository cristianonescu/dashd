"""Placeholder for the Microsoft Teams collector (Phase 3)."""
from __future__ import annotations

from typing import Any

from dashd.collectors.base import Collector


class TeamsCollector(Collector):
    key = "teams"

    async def collect(self) -> dict[str, Any] | None:
        return None
