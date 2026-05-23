"""GitHubCollector with a mocked httpx transport."""
from __future__ import annotations

import json

import httpx
import pytest

from dashd.collectors.github import GitHubCollector


def _client_with(responses: dict[str, dict]):
    """Returns a Mock transport that maps URL paths → (status, json)."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, payload in responses.items():
            if path.startswith(prefix):
                return httpx.Response(payload.get("status", 200), json=payload["body"])
        return httpx.Response(404, json={"message": "not mapped"})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_no_token_returns_none(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    c = GitHubCollector(enabled=True, token=None)
    assert await c.collect() is None


@pytest.mark.asyncio
async def test_happy_path(monkeypatch):
    original = httpx.AsyncClient
    transport = _client_with({
        "/search/issues": {"body": {"total_count": 3}},
        "/user": {"body": {"login": "octocat"}},
        "/notifications": {"body": [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}, {"id": "5"}]},
    })
    monkeypatch.setattr("dashd.collectors.github.httpx.AsyncClient",
                        lambda **kw: original(transport=transport, **kw))
    c = GitHubCollector(enabled=True, token="ghp_test")
    out = await c.collect()
    assert out["prs_awaiting_review"] == 3
    assert out["unread_notifications"] == 5
    # ci_failures uses the same /search/issues endpoint; mock returns 3 for it too.
    assert out["ci_failures_24h"] == 3


@pytest.mark.asyncio
async def test_partial_failure_sets_minus_one(monkeypatch):
    def handler(request):
        if request.url.path.startswith("/notifications"):
            return httpx.Response(500, json={"error": "boom"})
        if request.url.path.startswith("/user"):
            return httpx.Response(200, json={"login": "octocat"})
        if request.url.path.startswith("/search/issues"):
            return httpx.Response(200, json={"total_count": 2})
        return httpx.Response(404)
    original = httpx.AsyncClient
    monkeypatch.setattr("dashd.collectors.github.httpx.AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    c = GitHubCollector(enabled=True, token="ghp_test")
    out = await c.collect()
    assert out["prs_awaiting_review"] == 2
    assert out["unread_notifications"] == -1
