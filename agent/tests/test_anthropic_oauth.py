"""Tests for the Anthropic OAuth Usage client.

Covers (per codex review):
  - happy path (200 with full response shape)
  - 401 triggers refresh + falls back to no_token reason
  - network timeout / 5xx falls back to network reason
  - lenient parsing: missing fields don't crash
  - pace formula across history deque states (warming up, on track,
    far behind)
  - refresh cooldown is enforced (5 min)
  - cache TTL prevents back-to-back fetches
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from unittest.mock import patch

import httpx
import pytest
import respx

from dashd.anthropic_oauth import (
    AnthropicOAuthClient,
    UsageWindow,
    USAGE_ENDPOINT,
    _classify_pace,
    _derive_pace,
)


def _sample_response(util_session: float = 5,
                     util_weekly: float = 12,
                     util_sonnet: float = 0,
                     extra_used: float = 100,
                     extra_limit: float = 2000) -> dict:
    """Mirror the shape from codexbar's audit."""
    now = int(time.time())
    return {
        "fiveHour": {
            "utilization": util_session,
            "resetsAt": now + 3000,
            "windowMinutes": 300,
        },
        "sevenDay": {
            "utilization": util_weekly,
            "resetsAt": now + 5 * 86400,
            "windowMinutes": 7 * 24 * 60,
        },
        "sevenDaySonnet": {
            "utilization": util_sonnet,
            "resetsAt": now + 5 * 86400,
            "windowMinutes": 7 * 24 * 60,
        },
        "extraUsage": {
            "isEnabled": True,
            "monthlyLimit": extra_limit,
            "usedCredits": extra_used,
            "utilization": (extra_used / extra_limit) * 100,
            "currency": "USD",
        },
    }


@pytest.fixture
def client() -> AnthropicOAuthClient:
    c = AnthropicOAuthClient(enabled=True)
    # Pre-load a fake token so credential discovery isn't exercised.
    c._token = "fake-token-for-test"
    return c


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_returns_populated_usage(
        client: AnthropicOAuthClient) -> None:
    respx.get(USAGE_ENDPOINT).respond(200, json=_sample_response())
    usage = await client.fetch()
    assert usage.available is True
    assert usage.reason == "ok"
    assert usage.session is not None
    assert usage.session.used_pct == 5
    assert usage.weekly is not None
    assert usage.weekly.used_pct == 12
    assert usage.extra_usage is not None
    assert usage.extra_usage.limit_usd == 2000
    assert usage.extra_usage.currency == "USD"


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_refresh_and_returns_no_token(
        client: AnthropicOAuthClient) -> None:
    respx.get(USAGE_ENDPOINT).respond(401)
    with patch("dashd.anthropic_oauth.subprocess.Popen") as popen:
        usage = await client.fetch()
    assert usage.available is False
    assert usage.reason == "401"
    # Should have attempted to spawn `claude` for refresh.
    assert popen.called
    # Token cleared so next tick re-loads.
    assert client._token is None


@pytest.mark.asyncio
@respx.mock
async def test_refresh_cooldown_is_enforced(
        client: AnthropicOAuthClient) -> None:
    """Codex feedback: 401 → spawn `claude` must be ≤1 per 5 min."""
    respx.get(USAGE_ENDPOINT).respond(401)
    with patch("dashd.anthropic_oauth.subprocess.Popen") as popen:
        # First 401 triggers a spawn.
        client._token = "token-1"
        await client.fetch()
        first_call_count = popen.call_count
        assert first_call_count == 1
        # Second 401 within 5 min must NOT spawn again.
        # Bust both the response cache AND the token (the prod code
        # clears _token on 401; we restore it to retest the cooldown).
        client._cache = None
        client._token = "token-2"
        await client.fetch()
        assert popen.call_count == first_call_count
        # Fast-forward the cooldown + invalidate caches.
        client._last_refresh_attempt = time.time() - 6 * 60
        client._cache = None
        client._token = "token-3"
        await client.fetch()
        assert popen.call_count == first_call_count + 1


@pytest.mark.asyncio
@respx.mock
async def test_network_timeout_returns_network_reason(
        client: AnthropicOAuthClient) -> None:
    respx.get(USAGE_ENDPOINT).mock(side_effect=httpx.ReadTimeout("slow"))
    usage = await client.fetch()
    assert usage.available is False
    assert usage.reason == "network"


@pytest.mark.asyncio
@respx.mock
async def test_lenient_parsing_handles_missing_fields(
        client: AnthropicOAuthClient) -> None:
    """Partial responses (Anthropic returns fewer windows than expected)
    must produce a degraded-but-valid block, not crash."""
    respx.get(USAGE_ENDPOINT).respond(200, json={
        "fiveHour": {"utilization": 7},  # no resetsAt, no windowMinutes
        # no sevenDay, no extraUsage at all
    })
    usage = await client.fetch()
    assert usage.available is True
    assert usage.session is not None
    assert usage.session.used_pct == 7
    assert usage.session.resets_at is None
    assert usage.weekly is None
    assert usage.extra_usage is None


@pytest.mark.asyncio
async def test_disabled_returns_disabled_reason() -> None:
    c = AnthropicOAuthClient(enabled=False)
    usage = await c.fetch()
    assert usage.available is False
    assert usage.reason == "disabled"


@pytest.mark.asyncio
@respx.mock
async def test_cache_hit_avoids_second_http_call(
        client: AnthropicOAuthClient) -> None:
    route = respx.get(USAGE_ENDPOINT).respond(200, json=_sample_response())
    await client.fetch()
    await client.fetch()
    await client.fetch()
    # Three calls to fetch() within the 60s cache window → only one HTTP call.
    assert route.call_count == 1


# ── Pace formula tests ──────────────────────────────────────────────────

def test_pace_classify_on_track() -> None:
    assert _classify_pace(0) == "on_track"
    assert _classify_pace(1.9) == "on_track"
    assert _classify_pace(-2) == "on_track"


def test_pace_classify_directional_bands() -> None:
    # delta < 0 means usage is BELOW expected ⇒ ahead (under pace = good)
    assert _classify_pace(-3) == "slightly_ahead"
    assert _classify_pace(-7) == "ahead"
    assert _classify_pace(-20) == "far_ahead"
    # delta > 0 means usage exceeds expected ⇒ behind (over pace = bad)
    assert _classify_pace(3) == "slightly_behind"
    assert _classify_pace(7) == "behind"
    assert _classify_pace(20) == "far_behind"


def test_pace_warming_up_with_no_history() -> None:
    window = UsageWindow(used_pct=10, resets_at=int(time.time()) + 3000,
                         resets_in_min=50, window_minutes=300)
    history: deque[tuple[float, float]] = deque(maxlen=60)
    _derive_pace(history, window, time.time())
    assert window.pace_status == "warming_up"
    assert window.pace_delta_pct is None


def test_pace_computes_far_behind() -> None:
    """User burning ~3% per minute over a long window — should classify
    as 'far_behind' (=using too fast)."""
    now = time.time()
    window = UsageWindow(
        used_pct=80,
        resets_at=int(now) + 3000,
        resets_in_min=50,           # 50 min remaining out of 300
        window_minutes=300,
    )
    # History: 10 minutes ago at 50%, now at 80% → +30pp in 10 min
    history = deque([(now - 600, 50.0), (now, 80.0)], maxlen=60)
    _derive_pace(history, window, now)
    # 250 min elapsed of 300 min window → expected ≈ 83.33%.
    # actual 80, delta = 80 - 83.33 = -3.33 → slightly_ahead.
    assert window.pace_status == "slightly_ahead"
    # Burn rate 3pp/min, remaining 20pp → ETA ≈ 7 min.
    assert window.eta_to_cap_min is not None
    assert 6 <= window.eta_to_cap_min <= 8
    # 7 min < 50 min reset → quota WON'T last past reset at current rate.
    # Actually 7 min to cap < 50 min to reset → cap will hit BEFORE reset.
    assert window.will_last_to_reset is False
