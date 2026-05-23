"""Microsoft Graph calendar via MSAL device-code flow.

First run prints a URL + code; the user signs in once and the refresh token is
cached to ~/.config/dashd/msgraph_token.json (mode 0600). Subsequent runs are
silent. See docs/microsoft-graph-setup.md for the Azure registration steps.

This collector only needs Calendars.Read.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import msal

from dashd.collectors.base import Collector

log = logging.getLogger("dashd.calendar")

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Calendars.Read"]
TOKEN_PATH = Path.home() / ".config" / "dashd" / "msgraph_token.json"
REQ_TIMEOUT = 10.0


def _read_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if TOKEN_PATH.is_file():
        try:
            cache.deserialize(TOKEN_PATH.read_text())
        except Exception as e:
            log.warning("msgraph token cache load failed: %s", e)
    return cache


def _write_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_PATH.with_suffix(".tmp")
    tmp.write_text(cache.serialize())
    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_PATH)


class CalendarCollector(Collector):
    key = "calendar"

    def __init__(
        self,
        enabled: bool = True,
        client_id: str = "",
        tenant_id: str = "common",
    ) -> None:
        super().__init__(enabled)
        self.client_id = client_id
        self.tenant_id = tenant_id or "common"
        self._app: msal.PublicClientApplication | None = None
        self._cache: msal.SerializableTokenCache | None = None

    def _ensure_app(self) -> msal.PublicClientApplication:
        if self._app is None:
            self._cache = _read_cache()
            self._app = msal.PublicClientApplication(
                client_id=self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                token_cache=self._cache,
            )
        return self._app

    def _acquire_token(self) -> str | None:
        app = self._ensure_app()
        accounts = app.get_accounts()
        if accounts:
            r = app.acquire_token_silent(SCOPES, account=accounts[0])
            if r and "access_token" in r:
                assert self._cache is not None
                _write_cache(self._cache)
                return r["access_token"]

        # Device-code flow. Prints the URL + code to the agent log.
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            log.error("msgraph device flow init failed: %s", flow.get("error_description"))
            return None
        log.warning(
            "Microsoft sign-in required: open %s and enter code %s",
            flow["verification_uri"], flow["user_code"],
        )
        r = app.acquire_token_by_device_flow(flow)  # blocks until user signs in or times out
        if "access_token" not in r:
            log.error("msgraph device flow failed: %s", r.get("error_description"))
            return None
        assert self._cache is not None
        _write_cache(self._cache)
        return r["access_token"]

    async def collect(self) -> dict[str, Any] | None:
        if not self.client_id:
            return None
        # MSAL is sync; run it off the loop so we don't stall the agent.
        import asyncio
        token = await asyncio.to_thread(self._acquire_token)
        if not token:
            return None

        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=24)
        params = {
            "startDateTime": now.isoformat(),
            "endDateTime": end.isoformat(),
            "$orderby": "start/dateTime",
            "$top": "20",
            "$select": "subject,start,end,isAllDay,showAs",
        }
        headers = {"Authorization": f"Bearer {token}",
                   "Prefer": 'outlook.timezone="UTC"'}
        async with httpx.AsyncClient(timeout=REQ_TIMEOUT) as client:
            try:
                r = await client.get(
                    f"{GRAPH}/me/calendarView", params=params, headers=headers,
                )
                r.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("msgraph calendarView failed: %s", e)
                return None

        items = r.json().get("value", [])
        local_today = datetime.now().astimezone().date()
        today_remaining = 0
        next_title: str | None = None
        next_in_min: int | None = None

        for ev in items:
            if ev.get("isAllDay") or (ev.get("showAs") or "").lower() == "free":
                continue
            start_iso = (ev.get("start") or {}).get("dateTime")
            if not start_iso:
                continue
            try:
                # Graph returns UTC ISO with no offset when Prefer header asks for UTC.
                start_dt = datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if start_dt < now:
                continue
            if next_title is None:
                next_title = ev.get("subject") or "(untitled)"
                next_in_min = max(0, int((start_dt - now).total_seconds() / 60))
            if start_dt.astimezone().date() == local_today:
                today_remaining += 1

        return {
            "next_event_title": next_title,
            "next_event_in_min": next_in_min,
            "today_remaining": today_remaining,
        }
