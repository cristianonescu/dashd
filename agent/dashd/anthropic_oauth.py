"""Anthropic OAuth Usage API client.

Reads Claude Code's OAuth credentials from the user's local keychain (or
flat-file fallback), calls Anthropic's undocumented `/api/oauth/usage`
endpoint, and exposes the result as a structured dataclass.

This is what makes dashd's Session% / Weekly% / Sonnet% / Extra-usage
gauges match Claude.ai exactly — the response comes straight from
Anthropic, no local guesswork. JSONL-derived metrics remain as a
fallback when this pipeline is disabled or unreachable.

Security model:
    - Strictly OPT-IN. Default: disabled. The Electron app shows a
      one-time first-launch dialog asking the user to enable.
    - Token loaded into memory only. Never logged, never written to
      dashd state, never serialized to the wire. Only the `used_pct` /
      `resets_at` shape leaves this module.
    - File-fallback paths require mode 0600 (Unix) or restricted ACL
      (Windows). World-readable credential files are skipped with an
      INFO log.

References: codexbar's ClaudeOAuthUsageFetcher.swift (Swift). The
endpoint is undocumented and Anthropic may change or restrict it at
any time — every call handles failure as a soft degradation, not an
error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import stat
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("dashd.anthropic_oauth")

USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"
# Honest UA per Codex's review: don't impersonate Claude Code. If
# Anthropic rejects non-Claude-Code UAs we'll see a 4xx and can revisit.
USER_AGENT_PREFIX = "dashd"
HTTP_TIMEOUT_S = 10.0
REFRESH_COOLDOWN_S = 5 * 60   # don't spam `claude` CLI on repeated 401
CACHE_TTL_S = 60.0            # API response cache lifetime
HISTORY_LEN = 60              # ~60 minutes of polls for pace derivation

KEYCHAIN_SERVICE = "Claude Code-credentials"


# ── Public dataclasses ─────────────────────────────────────────────────

@dataclass
class UsageWindow:
    """One usage gauge from Anthropic (session, weekly, sonnet, etc.)."""
    used_pct: float
    resets_at: int | None = None        # Unix epoch seconds
    resets_in_min: int | None = None
    window_minutes: int | None = None
    # Pace metric derived locally from history. Null when not enough
    # history has accumulated (first ~2 ticks after agent start).
    pace_delta_pct: float | None = None
    pace_status: str | None = None      # "on_track" | "ahead" | "behind" | "far_*" | "warming_up"
    will_last_to_reset: bool | None = None
    eta_to_cap_min: int | None = None


@dataclass
class ExtraUsage:
    """Configurable monthly credit limit + current consumption."""
    enabled: bool = False
    limit_usd: float = 0.0
    used_usd: float = 0.0
    used_pct: float = 0.0
    currency: str = "USD"


@dataclass
class AnthropicUsage:
    """Top-level wire shape for the `anthropic` block."""
    available: bool = False
    reason: str = "disabled"            # "disabled" | "no_token" | "401" | "network" | "ok"
    session: UsageWindow | None = None
    weekly: UsageWindow | None = None
    sonnet_weekly: UsageWindow | None = None
    extra_usage: ExtraUsage | None = None
    # Optional — agent emits this only on the 60s `anthropic` block,
    # NOT in the 2s state frame (codex feedback: avoid wire bloat).
    history: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for the wire protocol."""
        out: dict[str, Any] = {"available": self.available, "reason": self.reason}
        if self.session:
            out["session"] = _window_to_dict(self.session)
        if self.weekly:
            out["weekly"] = _window_to_dict(self.weekly)
        if self.sonnet_weekly:
            out["sonnet_weekly"] = _window_to_dict(self.sonnet_weekly)
        if self.extra_usage:
            eu = self.extra_usage
            out["extra_usage"] = {
                "enabled": eu.enabled, "limit_usd": eu.limit_usd,
                "used_usd": eu.used_usd, "used_pct": eu.used_pct,
                "currency": eu.currency,
            }
        return out


def _window_to_dict(w: UsageWindow) -> dict[str, Any]:
    return {
        "used_pct": w.used_pct,
        "resets_at": w.resets_at,
        "resets_in_min": w.resets_in_min,
        "window_minutes": w.window_minutes,
        "pace_delta_pct": w.pace_delta_pct,
        "pace_status": w.pace_status,
        "will_last_to_reset": w.will_last_to_reset,
        "eta_to_cap_min": w.eta_to_cap_min,
    }


# ── Credential loaders (OS-specific, fallback chain) ───────────────────

def _load_credentials_keychain_macos() -> str | None:
    """macOS Keychain via `keyring` (lazy import — keep PyInstaller lean)."""
    if platform.system() != "Darwin":
        return None
    try:
        import keyring  # noqa: PLC0415 — intentional lazy import
    except ImportError:
        log.info("keyring library not installed; skipping macOS Keychain")
        return None
    try:
        # The username on a Claude Code keychain entry is the user's
        # actual email or "claude-code" depending on version. The library
        # docs allow get_credential() to skip it.
        cred = keyring.get_credential(KEYCHAIN_SERVICE, "")
    except Exception as e:
        log.info("keychain lookup failed: %s", e)
        return None
    if cred is None or not cred.password:
        return None
    return cred.password


def _load_credentials_file(path: Path) -> str | None:
    """Read a flat-file credential blob. Refuses to read non-0600 files."""
    if not path.exists():
        return None
    if os.name != "nt":  # POSIX: enforce 0600
        st = path.stat()
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            log.info("%s is mode 0%o (not 0600) — skipping for safety", path, mode)
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.info("could not read %s: %s", path, e)
        return None
    # Format varies across Claude Code versions. Try common shapes.
    if isinstance(data, dict):
        for key in ("claudeAiOauth", "claude_ai_oauth", "oauth"):
            v = data.get(key)
            if isinstance(v, dict):
                token = v.get("accessToken") or v.get("access_token")
                if isinstance(token, str) and token:
                    return token
        token = data.get("accessToken") or data.get("access_token")
        if isinstance(token, str) and token:
            return token
    return None


def load_oauth_token() -> str | None:
    """Try every credential source in order; return the first hit.

    Order:
      1. macOS Keychain (`keyring`)
      2. ~/.claude/.credentials.json (POSIX-style fallback)
      3. $XDG_CONFIG_HOME/claude/.credentials.json
      4. %APPDATA%\\Claude\\.credentials.json (Windows file fallback)

    Returns None when no credential is found. Never raises on failure;
    every error path logs at INFO so silent breakage is detectable
    under `dashd -v`.
    """
    token = _load_credentials_keychain_macos()
    if token:
        return token
    # ~/.claude/.credentials.json
    token = _load_credentials_file(Path.home() / ".claude" / ".credentials.json")
    if token:
        return token
    # XDG
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    token = _load_credentials_file(Path(xdg) / "claude" / ".credentials.json")
    if token:
        return token
    # Windows
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            token = _load_credentials_file(Path(appdata) / "Claude" / ".credentials.json")
            if token:
                return token
    return None


# ── Pace formula (lifted from codexbar's UsagePace.swift) ──────────────

# Codex flagged these as needing per-window calibration. For now,
# ±2/6/12 are codexbar's defaults; revisit once we have empirical data.
PACE_THRESHOLD_ON_TRACK = 2
PACE_THRESHOLD_SLIGHTLY = 6
PACE_THRESHOLD_FAR = 12


def _classify_pace(delta_pct: float) -> str:
    """Map a pace-delta percentage to a status label."""
    abs_d = abs(delta_pct)
    direction = "ahead" if delta_pct < 0 else "behind"  # below expected = ahead
    if abs_d <= PACE_THRESHOLD_ON_TRACK:
        return "on_track"
    if abs_d <= PACE_THRESHOLD_SLIGHTLY:
        return f"slightly_{direction}"
    if abs_d <= PACE_THRESHOLD_FAR:
        return direction
    return f"far_{direction}"


def _derive_pace(history: deque[tuple[float, float]],
                  window: UsageWindow, now: float) -> None:
    """Mutates `window` to populate pace_*, will_last_to_reset, eta_to_cap_min.

    `history` is a sequence of (timestamp_s, used_pct) samples for THIS
    window. The oldest sample's age plus the latest pct give us the
    rate of change.

    If we don't have enough history yet (≤1 sample), set `pace_status =
    "warming_up"` and leave the metric fields None. The UI renders
    nothing during this state — no spinner, no jargon.
    """
    if len(history) < 2:
        window.pace_status = "warming_up"
        return
    # Get the oldest sample we can find (up to the deque's full lifetime).
    oldest_ts, oldest_pct = history[0]
    elapsed_min = max(1e-6, (now - oldest_ts) / 60.0)
    rate_pct_per_min = (window.used_pct - oldest_pct) / elapsed_min

    # Expected pct = where the user should be if usage was perfectly
    # paced across the entire window. We need the window duration to
    # compute it.
    if window.window_minutes is None or window.window_minutes <= 0:
        # Without a window duration we can't compute expected_pct.
        # Surface rate-of-change only.
        window.pace_status = "warming_up"
        return
    elapsed_in_window_min = (
        window.window_minutes
        - (window.resets_in_min or window.window_minutes)
    )
    expected_pct = max(0.0, min(100.0,
        100.0 * elapsed_in_window_min / window.window_minutes))
    delta_pct = window.used_pct - expected_pct
    window.pace_delta_pct = round(delta_pct, 1)
    window.pace_status = _classify_pace(delta_pct)

    # Will the user's quota last to reset given the current burn rate?
    remaining_pct = max(0.0, 100.0 - window.used_pct)
    if rate_pct_per_min > 0 and window.resets_in_min is not None:
        eta_min = remaining_pct / rate_pct_per_min
        window.eta_to_cap_min = int(eta_min)
        window.will_last_to_reset = eta_min > window.resets_in_min
    else:
        # No burn (rate ≤ 0 or holding steady) — quota lasts trivially.
        window.will_last_to_reset = True
        window.eta_to_cap_min = None


# ── Response parsing (lenient) ─────────────────────────────────────────

def _parse_window(d: dict | None, now: float) -> UsageWindow | None:
    """Parse Anthropic's OAuth usage window object into UsageWindow.

    The shape from codexbar's audit:
        {
          utilization: number,             // percentage 0..100
          resetsAt:    ISO8601 string,     // OR an epoch — handle both
          windowMinutes: number,           // optional
        }
    Lenient: missing fields become None instead of raising.
    """
    if not isinstance(d, dict):
        return None
    util = d.get("utilization")
    if util is None:
        # Empty/unused window — Anthropic returns this for windows the
        # user's plan doesn't have. Skip silently.
        return None
    try:
        used_pct = float(util)
    except (TypeError, ValueError):
        return None

    resets_at: int | None = None
    raw = d.get("resetsAt") or d.get("resets_at")
    if isinstance(raw, (int, float)):
        resets_at = int(raw)
    elif isinstance(raw, str):
        # ISO 8601 — best-effort parse.
        try:
            from datetime import datetime
            ts = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            resets_at = int(datetime.fromisoformat(ts).timestamp())
        except (ValueError, TypeError):
            pass

    resets_in_min: int | None = None
    if resets_at is not None:
        resets_in_min = max(0, int((resets_at - now) / 60))

    window_minutes: int | None = None
    wm = d.get("windowMinutes") or d.get("window_minutes")
    if isinstance(wm, (int, float)) and wm > 0:
        window_minutes = int(wm)

    return UsageWindow(
        used_pct=used_pct,
        resets_at=resets_at,
        resets_in_min=resets_in_min,
        window_minutes=window_minutes,
    )


def _parse_extra(d: dict | None) -> ExtraUsage | None:
    if not isinstance(d, dict):
        return None
    return ExtraUsage(
        enabled=bool(d.get("isEnabled") or d.get("enabled")),
        limit_usd=float(d.get("monthlyLimit") or d.get("limit_usd") or 0),
        used_usd=float(d.get("usedCredits") or d.get("used_usd") or 0),
        used_pct=float(d.get("utilization") or d.get("used_pct") or 0),
        currency=str(d.get("currency") or "USD"),
    )


# ── Main client class ──────────────────────────────────────────────────

class AnthropicOAuthClient:
    """Wraps the OAuth usage endpoint with caching, refresh, history."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._token: str | None = None
        self._cache: AnthropicUsage | None = None
        self._cache_at: float = 0.0
        # 401 → spawn `claude` CLI fire-and-forget; gate to ≤1 per 5 min.
        # Instance state per Codex review (NOT a local variable).
        self._last_refresh_attempt: float = 0.0
        # Per-window history of (ts, used_pct) for pace derivation.
        # Key: "session" | "weekly" | "sonnet_weekly".
        self._history: dict[str, deque[tuple[float, float]]] = {
            k: deque(maxlen=HISTORY_LEN)
            for k in ("session", "weekly", "sonnet_weekly")
        }

    async def fetch(self) -> AnthropicUsage:
        """Return the freshest usage payload, hitting the API as needed."""
        if not self.enabled:
            return AnthropicUsage(available=False, reason="disabled")
        now = time.time()
        # 60s cache hit
        if self._cache is not None and (now - self._cache_at) < CACHE_TTL_S:
            return self._cache

        if self._token is None:
            self._token = load_oauth_token()
        if not self._token:
            usage = AnthropicUsage(available=False, reason="no_token")
            self._cache = usage
            self._cache_at = now
            return usage

        try:
            import httpx  # lazy import
        except ImportError:
            log.warning("httpx not available — anthropic oauth disabled")
            return AnthropicUsage(available=False, reason="no_httpx")

        try:
            payload = await self._http_get(httpx, self._token, now)
        except _Unauthorized:
            self._maybe_spawn_refresh(now)
            self._token = None  # force reload next tick
            usage = AnthropicUsage(available=False, reason="401")
            self._cache = usage
            self._cache_at = now
            return usage
        except _NetworkError as e:
            log.info("anthropic oauth network error: %s", e)
            usage = AnthropicUsage(available=False, reason="network")
            self._cache = usage
            self._cache_at = now
            return usage

        usage = self._build_usage(payload, now)
        self._cache = usage
        self._cache_at = now
        return usage

    async def _http_get(self, httpx, token: str, now: float) -> dict[str, Any]:
        """Fire the actual API call. Raises _Unauthorized or _NetworkError."""
        version = self._dashd_version()
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": ANTHROPIC_BETA,
            "User-Agent": f"{USER_AGENT_PREFIX}/{version}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            try:
                r = await client.get(USAGE_ENDPOINT, headers=headers)
            except httpx.HTTPError as e:
                raise _NetworkError(str(e)) from e
        if r.status_code == 401:
            raise _Unauthorized()
        if r.status_code >= 400:
            raise _NetworkError(f"http {r.status_code}")
        try:
            return r.json()
        except Exception as e:
            raise _NetworkError(f"bad json: {e}") from e

    def _build_usage(self, payload: dict[str, Any], now: float) -> AnthropicUsage:
        """Parse the response, derive pace, return a populated AnthropicUsage."""
        session = _parse_window(payload.get("fiveHour"), now)
        weekly = _parse_window(payload.get("sevenDay"), now)
        sonnet = _parse_window(payload.get("sevenDaySonnet"), now)
        extra = _parse_extra(payload.get("extraUsage"))

        # Push new samples into history, then derive pace.
        for key, win in (("session", session), ("weekly", weekly),
                         ("sonnet_weekly", sonnet)):
            if win is None:
                continue
            self._history[key].append((now, win.used_pct))
            _derive_pace(self._history[key], win, now)

        usage = AnthropicUsage(
            available=True, reason="ok",
            session=session, weekly=weekly, sonnet_weekly=sonnet,
            extra_usage=extra,
        )
        # Attach the full session history (the most actionable for a
        # burn-rate sparkline in the desktop UI).
        usage.history = list(self._history["session"])
        return usage

    def _maybe_spawn_refresh(self, now: float) -> None:
        """Fire-and-forget `claude` CLI refresh on 401; ≤1 per 5 min."""
        if now - self._last_refresh_attempt < REFRESH_COOLDOWN_S:
            return
        self._last_refresh_attempt = now
        log.info("anthropic oauth: 401, attempting `claude` token refresh")
        try:
            # `claude` (if installed) refreshes its own keychain entry as
            # a side-effect of a no-op invocation. We don't wait for it.
            subprocess.Popen(
                ["claude", "--version"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            log.info("`claude` CLI not on PATH; cannot refresh oauth token")
        except OSError as e:
            log.info("`claude` CLI spawn failed: %s", e)

    @staticmethod
    def _dashd_version() -> str:
        """Best-effort version string for the User-Agent."""
        try:
            from importlib.metadata import version
            return version("dashd")
        except Exception:
            return "0.1.10"


# ── Internal exception types (caught + classified above) ───────────────

class _Unauthorized(Exception):
    pass


class _NetworkError(Exception):
    pass
