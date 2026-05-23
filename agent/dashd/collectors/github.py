"""GitHub collector — PRs awaiting review, recent CI failures, unread notifications.

PAT-authenticated. Set `token` in config or `GITHUB_TOKEN` env var. Required
scopes: `repo` (PR + CI access) and `notifications`.

Three independent REST queries, run in parallel with per-call timeouts. Any
sub-query failure logs a warning and substitutes -1 for that count so the
firmware shows "--" instead of stale data.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from dashd.collectors.base import Collector

log = logging.getLogger("dashd.github")

GITHUB_API = "https://api.github.com"
REQ_TIMEOUT = 10.0


class GitHubCollector(Collector):
    key = "github"

    def __init__(self, enabled: bool = True, token: str | None = None) -> None:
        super().__init__(enabled)
        self._token = token or os.environ.get("GITHUB_TOKEN") or ""

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "dashd/0.1",
        }

    async def collect(self) -> dict[str, Any] | None:
        if not self._token:
            return None

        async with httpx.AsyncClient(timeout=REQ_TIMEOUT, headers=self._headers()) as client:
            prs = await self._prs_awaiting_review(client)
            ci = await self._ci_failures_24h(client)
            notifs = await self._unread_notifications(client)

        return {
            "prs_awaiting_review": prs,
            "ci_failures_24h": ci,
            "unread_notifications": notifs,
        }

    async def _prs_awaiting_review(self, client: httpx.AsyncClient) -> int:
        # Use the search API: PRs (not drafts) where this user is requested as a reviewer.
        # `review-requested:@me` is the documented shorthand for the authenticated user.
        try:
            r = await client.get(
                f"{GITHUB_API}/search/issues",
                params={"q": "is:pr is:open review-requested:@me archived:false", "per_page": 1},
            )
            r.raise_for_status()
            return int(r.json().get("total_count", 0))
        except (httpx.HTTPError, ValueError) as e:
            log.warning("prs_awaiting_review failed: %s", e)
            return -1

    async def _ci_failures_24h(self, client: httpx.AsyncClient) -> int:
        # GitHub Actions runs initiated by this user in the last 24h that failed.
        # Scoped by `actor` so we report on builds the user themselves triggered.
        try:
            user = (await client.get(f"{GITHUB_API}/user")).json().get("login")
            if not user:
                return -1
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            r = await client.get(
                f"{GITHUB_API}/search/issues",
                params={
                    "q": f"is:pr is:open author:{user} status:failure created:>={since}",
                    "per_page": 1,
                },
            )
            # The Search API doesn't index workflow runs directly. Best-effort
            # proxy: count the user's own open failing PRs in the last 24h.
            # When we eventually want true workflow-run failures we'll need to
            # iterate /repos/{owner}/{repo}/actions/runs per watched repo.
            r.raise_for_status()
            return int(r.json().get("total_count", 0))
        except (httpx.HTTPError, ValueError) as e:
            log.warning("ci_failures_24h failed: %s", e)
            return -1

    async def _unread_notifications(self, client: httpx.AsyncClient) -> int:
        try:
            r = await client.get(
                f"{GITHUB_API}/notifications",
                params={"all": "false", "per_page": 50},
            )
            r.raise_for_status()
            return len(r.json())
        except (httpx.HTTPError, ValueError) as e:
            log.warning("unread_notifications failed: %s", e)
            return -1
