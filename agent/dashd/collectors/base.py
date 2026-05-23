"""Async collector base class.

Each collector exposes an async `collect()` that returns a JSON-serializable dict
(or None when unavailable). Failures inside `collect()` must not propagate —
the aggregator treats any exception as `None` so one bad collector never breaks
the rest.
"""
from __future__ import annotations

import abc
import logging
from typing import Any

log = logging.getLogger("dashd.collector")


class Collector(abc.ABC):
    #: Top-level key under which this collector's payload appears in the state msg.
    key: str = ""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    @abc.abstractmethod
    async def collect(self) -> dict[str, Any] | None:  # pragma: no cover - interface
        """Return a JSON-serializable dict, or None if unavailable."""
        raise NotImplementedError

    async def safe_collect(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            return await self.collect()
        except Exception as e:
            # Log with traceback at debug level (visible under `dashd -v`)
            # so recurring "collector X failed: …" errors can be diagnosed
            # without modifying the collector. Keeps the default-verbosity
            # log line short so the live UI stays readable.
            log.warning("%s collector failed: %s",
                        self.key or type(self).__name__, e)
            log.debug("%s traceback:", self.key or type(self).__name__,
                      exc_info=True)
            return None
