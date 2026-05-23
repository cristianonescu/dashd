"""Cost math sanity checks against the LiteLLM-backed pricing module.

Replaces the old test_rates.py that tested the hand-maintained
dashd/rates.py. The new pricing.py uses real rates from a vendored
LiteLLM snapshot.
"""
from __future__ import annotations

from dashd.pricing import cost_usd, resolve


def test_resolves_by_longest_prefix() -> None:
    assert resolve("claude-opus-4-7") is not None
    assert resolve("claude-sonnet-4-5") is not None
    assert resolve("totally-unknown-model") is None


def test_basic_cost_opus_4_7() -> None:
    # 1M input + 1M output on opus-4-7 at $5/M + $25/M = $30.
    c = cost_usd("claude-opus-4-7",
                 {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert round(c, 2) == 30.00


def test_cache_create_uses_explicit_rate() -> None:
    # opus-4-7 cache_creation_input_token_cost is 6.25e-6 ($6.25/M).
    c = cost_usd("claude-opus-4-7",
                 {"cache_creation_input_tokens": 1_000_000})
    assert round(c, 2) == 6.25


def test_cache_read_uses_explicit_rate() -> None:
    # opus-4-7 cache_read_input_token_cost is 5e-7 ($0.50/M).
    c = cost_usd("claude-opus-4-7",
                 {"cache_read_input_tokens": 1_000_000})
    assert round(c, 2) == 0.50


def test_tiered_pricing_above_200k() -> None:
    # sonnet-4-6 has tiered pricing: $3/M up to 200k input, $6/M above.
    # 300k input → 200_000 * 3e-6 + 100_000 * 6e-6 = $1.20.
    c = cost_usd("claude-sonnet-4-5",
                 {"input_tokens": 300_000, "output_tokens": 0})
    assert round(c, 2) == 1.20


def test_unknown_model_costs_zero() -> None:
    assert cost_usd("gpt-9000", {"input_tokens": 1_000_000}) == 0.0


def test_user_override_wins() -> None:
    override = {
        "claude-opus-4-7": {
            "input_cost_per_token": 1.0e-6,
            "output_cost_per_token": 1.0e-6,
        }
    }
    c = cost_usd("claude-opus-4-7",
                 {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
                 overrides=override)
    assert round(c, 2) == 2.00


def test_poisoned_rates_collapse_to_zero() -> None:
    """Defends against a malformed LiteLLM payload: any non-finite or
    negative rate is treated as 0 instead of being multiplied into the
    running cost (where it'd silently corrupt totals)."""
    poison = {
        "claude-opus-4-7": {
            "input_cost_per_token": float("nan"),
            "output_cost_per_token": -5.0,
            "cache_read_input_token_cost": float("inf"),
        }
    }
    c = cost_usd("claude-opus-4-7", {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }, overrides=poison)
    assert c == 0.0


def test_string_rate_does_not_crash() -> None:
    """A pricing-row entry that's a string (e.g. someone misediting
    config.toml) must not crash the cost path."""
    override = {
        "claude-opus-4-7": {"input_cost_per_token": "free"},
    }
    c = cost_usd("claude-opus-4-7",
                 {"input_tokens": 100}, overrides=override)
    assert c == 0.0
