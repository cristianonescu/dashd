"""Per-model pricing, fed by LiteLLM's public model index.

LiteLLM (https://github.com/BerriAI/litellm) maintains a single JSON
catalog of every model + their per-token rates, including Anthropic's
above-200k tiered pricing and cache create/read costs. We vendor a
snapshot of that file at `litellm-pricing.json` so dashd works offline
with stale-but-recent data, and optionally refresh it from the upstream
URL once every 24h in the background so users on a long-running agent
get pricing updates without a dashd release.

Cost computation follows ccusage's reading of the same table:
  - tiered split at 200k tokens per category when the model defines
    `*_above_200k_tokens` variants
  - explicit cache_create / cache_read rates when present
  - fallback defaults: cache_create = input × 1.25, cache_read = input × 0.1
    (these match Anthropic's published 5-min ephemeral / read multipliers
    and are what ccusage uses when a model row is missing the cache rows)

This module exposes ONE function the collectors use:
    cost_usd(model, usage, *, overrides=None)
where `usage` is the Anthropic-shape dict from the JSONL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("dashd.pricing")

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
BUNDLED = Path(__file__).resolve().parent / "litellm-pricing.json"
CACHE_PATH = Path.home() / ".config" / "dashd" / "litellm-pricing.cache.json"
REFRESH_INTERVAL_S = 24 * 3600
FETCH_TIMEOUT_S = 10.0

# Below-row defaults that match Anthropic's documented ephemeral multipliers
# and ccusage's behaviour when the LiteLLM table lacks the explicit row.
DEFAULT_CACHE_CREATE_MULT = 1.25
DEFAULT_CACHE_READ_MULT   = 0.1
TIER_THRESHOLD_TOKENS = 200_000


# ── Catalog loading ─────────────────────────────────────────────────────

_catalog: dict[str, dict[str, Any]] | None = None
_catalog_source: str = "unloaded"


def _load_bundled() -> dict[str, Any]:
    try:
        with open(BUNDLED, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not load bundled pricing %s: %s", BUNDLED, e)
        return {}


def _load_cache() -> dict[str, Any] | None:
    try:
        st = CACHE_PATH.stat()
    except OSError:
        return None
    age = time.time() - st.st_mtime
    if age > REFRESH_INTERVAL_S:
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def get_catalog() -> dict[str, Any]:
    """Return the pricing catalog. Lazy-loaded — bundled snapshot is the
    default, but if a fresh fetched cache exists it's preferred."""
    global _catalog, _catalog_source
    if _catalog is not None:
        return _catalog
    cached = _load_cache()
    if cached:
        _catalog = cached
        _catalog_source = "cache"
        log.info("pricing: using fresh fetched cache (%d entries)", len(cached))
        return _catalog
    _catalog = _load_bundled()
    _catalog_source = "bundled"
    log.info("pricing: using bundled snapshot (%d entries)", len(_catalog))
    return _catalog


def catalog_source() -> str:
    """For diagnostics — 'bundled', 'cache', or 'unloaded'."""
    return _catalog_source


# ── Background refresh ──────────────────────────────────────────────────

async def refresh_in_background() -> None:
    """Try to fetch LiteLLM's pricing JSON into ~/.config/dashd/. Fails
    silently — bundled snapshot stays the source of truth on any failure.
    Designed to be fire-and-forget from main.py's startup path."""
    global _catalog, _catalog_source
    try:
        import httpx
    except ImportError:
        log.debug("pricing: httpx not available; skipping refresh")
        return
    if _load_cache() is not None:
        log.debug("pricing: cache is fresh, not refreshing")
        return
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as c:
            r = await c.get(LITELLM_URL)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.info("pricing: fetch failed (%s) — staying on bundled", e)
        return
    if not isinstance(data, dict) or len(data) < 50:
        log.warning("pricing: fetched payload looks malformed; ignoring")
        return
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        log.warning("pricing: could not write cache: %s", e)
        return
    _catalog = data
    _catalog_source = "cache"
    log.info("pricing: refreshed catalog from upstream (%d entries)", len(data))


# ── Model resolution ────────────────────────────────────────────────────

def _normalize(model: str) -> str:
    """Strip provider prefix / vendor suffix so 'anthropic/claude-opus-4-7'
    or 'claude-opus-4-7-20260416' both find the right LiteLLM key."""
    m = model.lower()
    if "/" in m:
        m = m.split("/", 1)[1]
    return m


def resolve(model: str, overrides: dict | None = None) -> dict[str, Any] | None:
    """Find the pricing row for a model id. Tries exact match first, then
    longest-prefix match against the catalog keys. Returns None if unknown.

    `overrides` is the per-user config.toml table, applied on top of the
    catalog. Keys in the overrides should be raw model ids OR family
    prefixes; longest-prefix wins like the catalog.
    """
    norm = _normalize(model)
    catalog = get_catalog()
    table: dict[str, Any] = {**catalog, **(overrides or {})}
    if norm in table:
        return table[norm]
    # Longest-prefix match — e.g. "claude-opus-4-7-20260416" → "claude-opus-4-7".
    candidates = sorted(
        (k for k in table if norm.startswith(k.lower())),
        key=len, reverse=True)
    return table[candidates[0]] if candidates else None


# ── Cost calculation ────────────────────────────────────────────────────

def _tier_split(tokens: int) -> tuple[int, int]:
    """Split a per-call token count into (below_200k, above_200k)."""
    if tokens <= TIER_THRESHOLD_TOKENS:
        return tokens, 0
    return TIER_THRESHOLD_TOKENS, tokens - TIER_THRESHOLD_TOKENS


def _safe_rate(value: Any, fallback: float = 0.0) -> float:
    """Coerce a catalog rate into a non-negative finite float.

    Defends the cost path against a poisoned LiteLLM snapshot — strings,
    None, NaN, inf, or negative numbers all collapse to `fallback`
    instead of propagating into the math (where they'd silently turn a
    user's session into "-$1.4e308" or trigger an exception). With a
    bundled fallback always present, the worst case is "cost = 0 for an
    unknown rate row" which is what we want.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return fallback
    import math
    if math.isnan(f) or math.isinf(f) or f < 0:
        return fallback
    return f


def cost_usd(model: str, usage: dict, *, overrides: dict | None = None) -> float:
    """Compute USD cost for one Anthropic-shape usage dict.

    Handles:
      - tiered pricing above 200k input/output tokens per call
      - explicit cache_create / cache_read rates from the LiteLLM table
      - default cache rates (input × 1.25 / × 0.1) when the row is missing them
      - 5m vs 1h cache_creation breakdown when Anthropic provides it
        (LiteLLM doesn't distinguish — both bill at cache_creation_input_token_cost)
      - poisoned rate rows: any non-finite or negative value collapses to 0
        rather than corrupting the running cost total.

    Returns 0.0 on unknown model (logs at debug level).
    """
    r = resolve(model, overrides)
    if not r:
        log.debug("pricing: no rate for %s — cost=0", model)
        return 0.0

    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_create_total = int(usage.get("cache_creation_input_tokens") or 0)

    in_below, in_above = _tier_split(inp)
    out_below, out_above = _tier_split(out)

    in_rate = _safe_rate(r.get("input_cost_per_token"))
    out_rate = _safe_rate(r.get("output_cost_per_token"))
    in_rate_hi = _safe_rate(
        r.get("input_cost_per_token_above_200k_tokens"), fallback=in_rate)
    out_rate_hi = _safe_rate(
        r.get("output_cost_per_token_above_200k_tokens"), fallback=out_rate)

    cc_explicit = r.get("cache_creation_input_token_cost")
    cc_rate = _safe_rate(cc_explicit) if cc_explicit is not None else (
        in_rate * DEFAULT_CACHE_CREATE_MULT)
    cr_explicit = r.get("cache_read_input_token_cost")
    cr_rate = _safe_rate(cr_explicit) if cr_explicit is not None else (
        in_rate * DEFAULT_CACHE_READ_MULT)

    total = (
        in_below * in_rate + in_above * in_rate_hi
        + out_below * out_rate + out_above * out_rate_hi
        + cache_create_total * cc_rate
        + cache_read * cr_rate
    )
    return total


def starts_background_refresh(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Schedule the background refresh on the running loop. Idempotent —
    safe to call once at agent startup. Errors swallowed inside the task."""
    loop = loop or asyncio.get_event_loop()
    loop.create_task(refresh_in_background())
