"""Entry point: assemble collectors, fan out state to USB + IPC."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import signal
import time
from pathlib import Path
from typing import Any, Callable

from rich.logging import RichHandler

from dashd.activity import ActivityTracker
from dashd.aggregator import Aggregator
from dashd.ble_trust import TrustStore
from dashd.bus import Bus
from dashd.collectors.calendar_msgraph import CalendarCollector
from dashd.collectors.claude_code import ClaudeCodeCollector
from dashd.collectors.codex import CodexCollector
from dashd.collectors.email_imap import EmailCollector
from dashd.collectors.git import GitCollector
from dashd.collectors.github import GitHubCollector
from dashd.collectors.imessage_macos import IMessageCollector
from dashd.collectors.system import SystemCollector
from dashd.collectors.whatsapp import WhatsAppCollector
from dashd.config import Config, env_override_config, load_config
from dashd.firmware_update import (
    FirmwareRelease, check_for_update, is_newer, stream_update,
)
from dashd.ipc_server import IPCServer
from dashd.protocol import PROTOCOL_VERSION, make_state
from dashd.pet_reactor import PetReactor
from dashd.pets import catalog as pet_catalog
from dashd.pets import install as pet_install
from dashd.pets import preview as pet_preview
from dashd.proc_history import ProcessHistory
from dashd.transport import SerialLink
from dashd.suggestions import SuggestionsEngine

log = logging.getLogger("dashd")
fw_log = logging.getLogger("dashd.fw")

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info":  logging.INFO,
    "warn":  logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _print_event(ev: dict, *, agent: "AgentApp | None" = None) -> None:
    if ev.get("type") != "event":
        log.debug("non-event from device: %s", ev)
        return
    name = ev.get("name", "?")
    if name == "log":
        level = _LEVEL_MAP.get(str(ev.get("level", "info")).lower(), logging.INFO)
        fw_log.log(level, "%s", ev.get("msg", ""))
    elif name == "page_changed":
        log.info("device → page_changed: %s", ev.get("page", "?"))
    elif name in ("boot", "hello_ack"):
        fw = ev.get("fw_version", "?")
        log.info("device → %s fw=%s", name, fw)
        # Track the device firmware version so the OTA flow knows what
        # to compare against. Both `boot` and `hello_ack` carry it.
        if agent is not None and isinstance(fw, str) and fw and fw != "?":
            agent._device_fw_version = fw
    else:
        log.info("device → %s %s", name, {k: v for k, v in ev.items() if k not in ("type", "name")})


def _build_collectors(cfg: Config) -> list:
    collectors: list = []
    if cfg.collectors.system.enabled:
        collectors.append(SystemCollector(enabled=True))
    if cfg.collectors.claude_code.enabled:
        rates = cfg.collectors.claude_code.model_extra.get("rates") if cfg.collectors.claude_code.model_extra else None
        collectors.append(ClaudeCodeCollector(enabled=True, rate_overrides=rates or {}))
    if cfg.collectors.codex.enabled:
        path = None
        extra = cfg.collectors.codex.model_extra or {}
        if extra.get("path"):
            path = Path(str(extra["path"])).expanduser()
        collectors.append(CodexCollector(enabled=True, path=path))
    if cfg.collectors.git.enabled:
        extra = cfg.collectors.git.model_extra or {}
        repos = extra.get("repos") or []
        collectors.append(GitCollector(enabled=True, repos=repos, author_email=cfg.user.email))
    if cfg.collectors.github.enabled:
        extra = cfg.collectors.github.model_extra or {}
        collectors.append(GitHubCollector(enabled=True, token=extra.get("token")))
    if cfg.collectors.calendar.enabled:
        extra = cfg.collectors.calendar.model_extra or {}
        collectors.append(CalendarCollector(
            enabled=True,
            client_id=str(extra.get("client_id") or ""),
            tenant_id=str(extra.get("tenant_id") or "common"),
        ))
    if cfg.collectors.email.enabled:
        extra = cfg.collectors.email.model_extra or {}
        collectors.append(EmailCollector(
            enabled=True,
            host=str(extra.get("host") or ""),
            port=int(extra.get("port") or 993),
            username=str(extra.get("username") or ""),
            password=extra.get("password"),
            mailbox=str(extra.get("mailbox") or "INBOX"),
        ))
    if cfg.collectors.imessage.enabled:
        collectors.append(IMessageCollector(enabled=True))
    if cfg.collectors.whatsapp.enabled:
        collectors.append(WhatsAppCollector(enabled=True))
    return collectors


class BusLogHandler(logging.Handler):
    """Forwards every agent log record onto the bus as a wire-format log event.

    Bridges Python logging → IPC clients so the Electron UI sees the same
    messages the user does in a terminal.
    """
    def __init__(self, bus: Bus) -> None:
        super().__init__()
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bus.publish({
                "type": "event",
                "name": "log",
                "level": record.levelname.lower(),
                "logger": record.name,
                "msg": record.getMessage(),
            })
        except Exception:
            pass


class AgentRuntime:
    """Pipeline core: gathers state, publishes to bus, hosts cmd handlers."""

    def __init__(self, cfg_path: Path | None) -> None:
        self.cfg_path = cfg_path
        self.cfg: Config = env_override_config(load_config(cfg_path))
        self.bus = Bus(queue_size=16)
        self.agg = Aggregator(_build_collectors(self.cfg))
        self.suggestions = SuggestionsEngine(top_n=5)
        self.pet_reactor = PetReactor()
        self.proc_history = ProcessHistory()
        # BLE pairing trust store — persisted host-side trust tokens.
        self.trust = TrustStore()
        # Active BLE pairing session (BlePairing | None), driven by IPC.
        self._pairing: Any = None
        # Latest device firmware version we've heard about (from boot event).
        # Tracked so the OTA flow knows what to compare against.
        self._device_fw_version: str | None = None
        # Most recent firmware update result so the UI can re-query on
        # window-show without re-running a check.
        self._fw_check: FirmwareRelease | None = None
        # Anthropic OAuth usage client (opt-in, default off). When
        # enabled, polls /api/oauth/usage every 60s and surfaces the
        # results in the `anthropic` top-level wire block.
        from dashd.anthropic_oauth import AnthropicOAuthClient
        oauth_enabled = bool(
            self.cfg.collectors.anthropic_oauth.enabled
            if hasattr(self.cfg.collectors, "anthropic_oauth")
            else False
        )
        # Env-var override (DASHD_ANTHROPIC_OAUTH=1).
        if os.environ.get("DASHD_ANTHROPIC_OAUTH", "").strip() in ("1", "true", "yes"):
            oauth_enabled = True
        self._oauth = AnthropicOAuthClient(enabled=oauth_enabled)
        # Latest oauth usage payload — refreshed every ~60s in push_loop;
        # included in every 2s state frame so the desktop UI always sees
        # the freshest values.
        self._oauth_usage: dict[str, Any] | None = None
        self._oauth_last_fetch: float = 0.0
        self.link = self._build_link()
        self.stop_evt = asyncio.Event()
        # Single outbound device-command queue. Every device-bound command —
        # from handle_cmd, the pet reactor, and pet install — is enqueued
        # here, and link_loop is the SOLE consumer / sole link.send() caller.
        # This replaced three `_pending_*` scalars that silently dropped a
        # command if two arrived between link_loop iterations, and removes
        # the second unsynchronized writer that pet install used to be.
        # Bounded so a long disconnect mid-install can't grow it without
        # limit — `_tx_put` drops the oldest on overflow. (Phase 3's
        # ACK-windowed install won't outrun the queue in the first place.)
        self._tx_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        # Inbound event demultiplexer: one-shot waiters keyed on a predicate.
        # link_loop resolves them as device events arrive, so an operation
        # (e.g. pet install) can await a specific device ACK.
        self._event_waiters: list[tuple[Callable[[dict], bool],
                                        "asyncio.Future[dict]"]] = []
        # Tracks whether anyone is actually consuming agent state — the
        # push_loop drops to a slow tick when this is false to save CPU.
        # Updated from two places: ipc_server (per-client set_active cmds)
        # and our own link_loop (device connect / disconnect events).
        self.activity = ActivityTracker()
        # An asyncio event the push_loop waits on so it can interrupt a
        # long idle sleep the moment activity returns. We bridge the
        # ActivityTracker's synchronous callback into this event.
        self._activity_changed = asyncio.Event()
        self.activity.on_change(self._activity_changed.set)

    def _build_link(self):
        """Pick the device transport from cfg.transport.mode.

        - cable     → SerialLink (USB-CDC)
        - bluetooth → BleLink
        - auto      → SerialLink if a dashd USB device is enumerated (or a
                      serial port is explicitly configured), else BleLink.
        The choice is made once at startup; changing mode restarts the
        agent (the Electron app does this via the DASHD_TRANSPORT env var).
        """
        mode = self.cfg.transport.mode
        use_ble = mode == "bluetooth"
        if mode == "auto" and not self.cfg.serial.port:
            from dashd.transport import find_port
            if find_port(self.cfg.serial.vid, self.cfg.serial.pid) is None:
                use_ble = True
        if use_ble:
            from dashd.transport import BleLink
            log.info("transport: Bluetooth LE")
            self.link_kind = "ble"
            # Pass the trust store so the live link authenticates with the
            # stored token; an unpaired device is left for the pairing flow.
            return BleLink(trust_store=self.trust)
        log.info("transport: USB-CDC")
        self.link_kind = "usb"
        return SerialLink(self.cfg.serial.port, self.cfg.serial.baud,
                          self.cfg.serial.vid, self.cfg.serial.pid)

    def reload_config(self) -> None:
        """Re-read config.toml. Rebuilds the collector list in place; the next
        tick uses the new set. Does not restart the serial link."""
        log.info("reloading config from %s", self.cfg_path or "(defaults)")
        self.cfg = env_override_config(load_config(self.cfg_path))
        self.agg = Aggregator(_build_collectors(self.cfg))

    async def handle_cmd(self, msg: dict[str, Any]) -> None:
        name = msg.get("name", "")
        if name == "reload_config":
            self.reload_config()
        elif name == "show_page":
            self._tx_put(
                {"type": "cmd", "name": "show_page",
                 "page": str(msg.get("page") or "")})
        elif name == "set_brightness":
            try:
                val = max(0, min(100, int(msg.get("value", 0))))
            except (TypeError, ValueError):
                pass
            else:
                self._tx_put(
                    {"type": "cmd", "name": "set_brightness", "value": val})
        elif name == "stop":
            self.stop_evt.set()
        elif name == "pets_catalog":
            # Async: fetch and broadcast the catalog over the bus so UIs can
            # subscribe. Spawned as a task so we don't block other cmds.
            asyncio.create_task(self._cmd_catalog())
        elif name == "pets_install":
            slug = str(msg.get("slug") or "").strip()
            if slug:
                asyncio.create_task(self._cmd_install(slug))
        elif name == "pets_preview":
            slug = str(msg.get("slug") or "default").strip()
            asyncio.create_task(self._cmd_preview(slug))
        elif name == "ble_scan":
            # Phase 4a discovery — scan for dashd BLE devices and broadcast
            # the result so the Electron pairing UI can list them.
            asyncio.create_task(self._cmd_ble_scan())
        elif name == "ble_pair":
            addr = str(msg.get("address") or "")
            if addr:
                asyncio.create_task(self._cmd_ble_pair(addr))
        elif name == "ble_pair_code":
            asyncio.create_task(
                self._cmd_ble_pair_code(str(msg.get("code") or "")))
        elif name == "ble_untrust":
            # Drop a paired device. With no address, forget every device
            # (factory reset of pairings).
            addr = msg.get("address")
            if addr:
                self.trust.forget(str(addr))
            else:
                self.trust.forget_all()
            self.bus.publish({"type": "event", "name": "ble_trusted",
                              "devices": self.trust.addresses()},
                             sticky_key="ble_trusted")
        elif name == "ble_trusted_list":
            self.bus.publish({"type": "event", "name": "ble_trusted",
                              "devices": self.trust.addresses()},
                             sticky_key="ble_trusted")
        elif name == "proc_action":
            # `target` is the canonical app label we already shipped in
            # top_ram/top_cpu (e.g. "Google Chrome", "VM (Claude)").
            asyncio.create_task(self._cmd_proc_action(
                str(msg.get("target") or ""),
                str(msg.get("action") or ""),
            ))
        elif name == "fw_check_update":
            asyncio.create_task(self._cmd_fw_check())
        elif name == "fw_update_start":
            asyncio.create_task(self._cmd_fw_update())
        elif name == "fw_update_abort":
            # Best-effort cancel — also send the device-side abort.
            self._tx_put({"type": "cmd", "name": "fw_update_abort"})
        elif name.startswith("pet_") or name in (
            "set_theme", "set_thresholds", "set_pages_enabled",
            "set_pages_order", "set_layout", "set_text_scales", "reset_prefs",
            # Auto-advance: device cycles between enabled pages on a timer
            # (v0.1.9+). Fields: enabled (bool), interval_s (int 3..300),
            # mode ("sequential" | "random"). Clamped + persisted device-side.
            "set_auto_advance",
        ):
            # Pass-through device cmds. Enqueue; link_loop forwards them.
            self._tx_put(
                {"type": "cmd", "name": name,
                 **{k: v for k, v in msg.items() if k not in ("type", "name")}})
        else:
            log.debug("ignored unknown cmd: %s", name)

    def wait_event(self, match: Callable[[dict], bool]) -> "asyncio.Future[dict]":
        """Register a one-shot waiter for an inbound device event. link_loop
        resolves the returned future with the first event for which
        `match(event)` is true. Used by operations that must await a device
        ACK (e.g. the Phase-3 ACK-windowed pet install).

        If the caller cancels/times-out the future (e.g. via wait_for), a
        done-callback prunes it from the registry so cancelled waiters can't
        accumulate while no further events arrive."""
        fut: "asyncio.Future[dict]" = asyncio.get_running_loop().create_future()
        entry = (match, fut)
        self._event_waiters.append(entry)
        fut.add_done_callback(
            lambda _f: self._event_waiters.remove(entry)
            if entry in self._event_waiters else None)
        return fut

    async def _cmd_catalog(self) -> None:
        try:
            entries = await pet_catalog.fetch_catalog()
            self.bus.publish({
                "type": "event", "name": "pets_catalog",
                "entries": [{"slug": e.slug, "name": e.name,
                             "gallery_url": e.gallery_url}
                            for e in entries],
            })
        except Exception as e:
            self.bus.publish({"type": "event", "name": "pets_catalog_error",
                              "error": str(e)})

    async def _cmd_preview(self, slug: str) -> None:
        import base64
        try:
            p = await pet_preview.get_preview(slug)
        except Exception as e:
            log.warning("pet preview failed: %s", e)
            self.bus.publish({"type": "event", "name": "pets_preview_failed",
                              "slug": slug, "error": str(e)})
            return
        b64 = base64.b64encode(p.image_bytes).decode("ascii")
        self.bus.publish({
            "type": "event",
            "name": "pets_preview",
            "slug": p.slug,
            "displayName": p.name,
            "creator": p.creator,
            "source_url": p.source_url,
            "rows": p.rows,
            "cols": p.cols,
            "states": p.states,
            "frames_per_state": p.frames_per_state,
            "image_data_uri": f"data:{p.mime};base64,{b64}",
        })

    async def _cmd_fw_check(self) -> None:
        """Hit GitHub Releases and publish whether an update is available.

        Doesn't actually download anything. The UI then drives `fw_update_start`
        if the user opts in.
        """
        rel = await check_for_update()
        if rel is None:
            self.bus.publish({"type": "event", "name": "fw_update_state",
                              "state": "error",
                              "error": "could not reach GitHub Releases"})
            return
        self._fw_check = rel
        current = self._device_fw_version or "?"
        if not is_newer(rel.version, current):
            self.bus.publish({"type": "event", "name": "fw_update_state",
                              "state": "up_to_date",
                              "current": current, "latest": rel.version})
            return
        self.bus.publish({"type": "event", "name": "fw_update_state",
                          "state": "available",
                          "current": current, "latest": rel.version,
                          "notes": rel.notes,
                          "size_ble": rel.size_ble, "size_usb": rel.size_usb})

    async def _cmd_fw_update(self) -> None:
        """Stream the latest firmware to the device over the active transport.

        Requires a prior `fw_check_update` to populate `self._fw_check` —
        the UI normally does that itself so we don't fetch GitHub twice.
        """
        if self._fw_check is None:
            # Be forgiving: fetch on demand.
            self._fw_check = await check_for_update()
            if self._fw_check is None:
                self.bus.publish({"type": "event", "name": "fw_update_state",
                                  "state": "error",
                                  "error": "could not reach GitHub Releases"})
                return
        rel = self._fw_check
        variant = "ble" if self.link_kind == "ble" else "usb"
        # Use the BLE asset always over BLE; the USB build is smaller and
        # only used for cable connections to save flash room.
        log.info("starting OTA: variant=%s, target=%s", variant, rel.version)

        def _send_cmd(cmd: dict) -> None:
            self._tx_put(cmd)
        await stream_update(rel, variant=variant, send_cmd=_send_cmd,
                            wait_event=self.wait_event,
                            publish=self.bus.publish)

    async def _cmd_proc_action(self, proc_name: str, action: str) -> None:
        """Best-effort actions on a top-process row. `proc_name` is the
        canonical app label the agent already shipped (e.g. "Google Chrome",
        "VM (Claude)"). We resolve it back to one or more PIDs and act."""
        if not proc_name or not action:
            return
        import psutil
        from dashd.collectors.system import _app_key, _attribute_name
        # Collect names once so attribution has full context.
        all_names: set[str] = set()
        candidates: list[psutil.Process] = []
        for p in psutil.process_iter(["name"]):
            try:
                n = p.info.get("name") or ""
                all_names.add(n)
            except Exception:
                pass
        for p in psutil.process_iter(["name", "exe"]):
            try:
                n = p.info.get("name") or ""
                if _app_key(_attribute_name(n, all_names)) == proc_name:
                    candidates.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not candidates:
            self.bus.publish({"type": "event", "name": "proc_action_done",
                              "ok": False, "error": f"no procs match {proc_name!r}"})
            return

        ok = False
        err: str | None = None
        try:
            if action == "activity_monitor":
                # Surface Activity Monitor; the user picks the row themselves.
                import subprocess
                subprocess.Popen(["open", "-a", "Activity Monitor"])
                ok = True
            elif action == "reveal":
                # Reveal one candidate's executable bundle in Finder.
                import subprocess
                exe = candidates[0].exe()
                subprocess.Popen(["open", "-R", exe])
                ok = True
            elif action == "quit":
                for p in candidates:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                ok = True
            else:
                err = f"unknown action {action!r}"
        except Exception as e:
            err = str(e)

        self.bus.publish({
            "type": "event", "name": "proc_action_done",
            "ok": ok, "action": action, "proc_name": proc_name,
            "matched": len(candidates),
            **({"error": err} if err else {}),
        })

    async def _cmd_ble_scan(self) -> None:
        """Scan for dashd BLE devices; publish the result for the pairing UI."""
        try:
            from dashd.transport.ble_link import scan_devices
            devices = await scan_devices()
            self.bus.publish({"type": "event", "name": "ble_scan_result",
                              "devices": devices})
        except Exception as e:
            log.warning("ble scan failed: %s", e)
            self.bus.publish({"type": "event", "name": "ble_scan_result",
                              "devices": [], "error": str(e)})

    def _ble_pairing_state(self, state: str, **extra) -> None:
        self.bus.publish({"type": "event", "name": "ble_pairing_state",
                          "state": state, **extra})

    async def _cmd_ble_pair(self, address: str) -> None:
        """Begin a pairing session — connect (the device shows its code)."""
        from dashd.transport.ble_link import BlePairing
        try:
            if self._pairing is not None:
                await self._pairing.cancel()
            self._pairing = BlePairing(address, self.trust)
            self._ble_pairing_state("connecting", address=address)
            state = await self._pairing.begin()
            if state == "paired":
                self._pairing = None
                self._ble_pairing_state("paired", address=address)
                self.bus.publish({"type": "event", "name": "ble_trusted",
                                  "devices": self.trust.addresses()},
                                 sticky_key="ble_trusted")
            else:
                code = self._pairing.code
                if code:
                    # Surface the code in the agent log → it shows up in the
                    # Electron Logs panel, so the user doesn't have to read
                    # it off the device screen.
                    log.info("BLE pairing code for %s: %s", address, code)
                self._ble_pairing_state("awaiting_code", address=address,
                                        code=code)
        except Exception as e:
            log.warning("ble pair failed: %s", e)
            self._pairing = None
            self._ble_pairing_state("failed", address=address, error=str(e))

    async def _cmd_ble_pair_code(self, code: str) -> None:
        """Submit the user-entered 6-digit code for the active pairing."""
        if self._pairing is None:
            self._ble_pairing_state("failed", error="no pairing in progress")
            return
        try:
            await self._pairing.submit_code(code)
            addr = self._pairing._address
            self._pairing = None
            self._ble_pairing_state("paired", address=addr)
            self.bus.publish({"type": "event", "name": "ble_trusted",
                              "devices": self.trust.addresses()},
                             sticky_key="ble_trusted")
        except Exception as e:
            log.warning("ble pair-code failed: %s", e)
            self._pairing = None
            self._ble_pairing_state("failed", error=str(e))

    async def _cmd_install(self, slug: str) -> None:
        async def device_send(cmd):
            # Enqueue onto the single TX queue — link_loop is the sole
            # link.send() caller, so install chunks can't interleave with
            # state frames or other commands.
            self._tx_put(cmd)

        def wait_ack(seq: int):
            # ACK-windowed flow control: resolves when the device's
            # pet_install_chunk_ack for `seq` arrives via the event demux.
            # 8 s timeout so a lost ACK fails the install instead of hanging;
            # a link drop fails the waiter with ConnectionError.
            return asyncio.wait_for(
                self.wait_event(
                    lambda ev: ev.get("name") == "pet_install_chunk_ack"
                    and ev.get("seq") == seq),
                timeout=8.0)

        def wait_started():
            # Handshake: the device replies with `pet_install_started`
            # carrying ok=true/false (false = LittleFS mount failed or
            # similar device-side reject). Waiting for this BEFORE
            # streaming chunks prevents the "chunk without start" log
            # flood that used to happen when the FS was unmountable.
            # 5 s timeout is generous — the firmware replies as soon as
            # `LittleFS.open(...)` returns, which is fast on healthy flash.
            return asyncio.wait_for(
                self.wait_event(
                    lambda ev: ev.get("name") == "pet_install_started"
                    and ev.get("slug") == slug),
                timeout=5.0)

        self.bus.publish({"type": "event", "name": "pets_install_started", "slug": slug})
        try:
            installed = await pet_install.install(slug, device_send,
                                                  wait_ack=wait_ack,
                                                  wait_started=wait_started)
            self.bus.publish({"type": "event", "name": "pets_install_complete",
                              "slug": installed})
        except Exception as e:
            log.warning("pet install failed: %s", e)
            # Purge any pet_install_* commands still queued for the link
            # so chunks don't keep flowing after we've decided to abort.
            self._purge_tx_queue(
                lambda c: isinstance(c, dict)
                and str(c.get("name") or "").startswith("pet_install_"))
            self.bus.publish({"type": "event", "name": "pets_install_failed",
                              "slug": slug, "error": str(e)})

    async def push_loop(self) -> None:
        fast = self.cfg.update.interval_seconds
        idle = self.cfg.update.idle_interval_seconds
        while not self.stop_evt.is_set():
            payload = await self.agg.gather()

            # Anthropic OAuth usage — refresh at most every 60 s; reuse
            # the cached payload on every other state tick. The fetch is
            # bounded by HTTP_TIMEOUT_S so a stalled API call never
            # blocks the agent loop for long.
            now_ts = time.time()
            if self._oauth.enabled and (now_ts - self._oauth_last_fetch) > 60:
                try:
                    usage = await self._oauth.fetch()
                    self._oauth_usage = usage.to_dict()
                except Exception as e:
                    log.debug("oauth usage fetch failed: %s", e, exc_info=True)
                    self._oauth_usage = {"available": False, "reason": "error"}
                self._oauth_last_fetch = now_ts
            if self._oauth_usage is not None:
                payload["anthropic"] = self._oauth_usage

            # Record per-app RSS history so we can flag memory leaks. Uses
            # the same canonical app names the firmware will display, so a
            # surfaced leak directly matches a row on the Tips page.
            sys = payload.get("system") or {}
            top_ram = sys.get("top_ram") or []
            live_keys: set[str] = set()
            for row in top_ram:
                key = str(row.get("name") or "")
                if not key:
                    continue
                live_keys.add(key)
                self.proc_history.record(key, int(row.get("ram_mb") or 0))
            self.proc_history.prune(live_keys)
            leak = self.proc_history.worst_leak()
            if leak is not None and sys is not None:
                sys["memory_leak"] = leak.to_dict()
                payload["system"] = sys

            # Add real-time suggestions after all collectors fill in.
            payload["suggestions"] = self.suggestions.suggest(payload)
            # Map state changes → pet animation cmds.
            pet_cmd = self.pet_reactor.react(payload)
            if pet_cmd is not None:
                self._tx_put(pet_cmd)
            msg = make_state(payload)
            self.bus.publish(msg)

            # Pick the sleep duration based on consumer activity.
            #   Active   → fast tick (default 2 s)
            #   Idle     → slow tick (default 30 s); idle if no IPC client is
            #              "active" AND the USB device isn't connected.
            # The slow sleep is woken early if activity returns mid-wait
            # (e.g. the user opens the window or plugs the device in).
            if self.activity.has_active_consumer or idle <= fast:
                interval = fast
            else:
                interval = idle
            log.debug("push_loop: interval=%.1fs (active=%s)",
                      interval, self.activity.has_active_consumer)
            self._activity_changed.clear()
            done, pending = await asyncio.wait(
                {asyncio.create_task(self.stop_evt.wait()),
                 asyncio.create_task(self._activity_changed.wait())},
                timeout=interval, return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel and drain the loser task(s) so cancellation completes
            # before the next iteration — no dangling tasks accumulate.
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def _send_hello(self) -> None:
        """Announce ourselves on a freshly-connected link. The firmware
        replies with a `hello_ack` event carrying its fw + protocol
        version. Sent directly (not via the queue): link_loop is the sole
        sender and isn't draining the queue at this point. Over USB the
        handshake is advisory; over BLE (Phase 3) it claims the session."""
        self.link.send({"type": "cmd", "name": "hello", "v": PROTOCOL_VERSION})

    async def _reconnect(self) -> bool:
        """Mark the link down, drop stale queued commands, reconnect, and
        re-handshake. Returns False if a stop was requested mid-reconnect
        (caller should exit link_loop)."""
        self.activity.set_device_connected(False)
        self.bus.publish({"type": "event", "name": "agent_status",
                          "connected": False, "port": None,
                          "transport": self.link_kind},
                         sticky_key="agent_status")
        # Queued commands are stale once the link dropped — a show_page or
        # install chunk from before the disconnect must NOT replay.
        self._drain_tx_queue()
        await asyncio.to_thread(self.link.connect, 30.0, self.stop_evt.is_set)
        if self.stop_evt.is_set():
            return False
        self.activity.set_device_connected(True)
        self._send_hello()
        self.bus.publish({"type": "event", "name": "agent_status",
                          "connected": True, "port": "auto",
                          "transport": self.link_kind},
                         sticky_key="agent_status")
        return True

    async def link_loop(self) -> None:
        """Bridge bus → device and device events → bus, over whichever
        transport `self.link` is. Sole owner of `link.send()`."""
        sub = self.bus.subscribe()
        try:
            # Pass stop_evt.is_set so a SIGTERM during the (potentially
            # forever) connect retry loop interrupts it instead of hanging.
            await asyncio.to_thread(self.link.connect, 30.0, self.stop_evt.is_set)
            if self.stop_evt.is_set():
                return
            self.activity.set_device_connected(True)
            self._send_hello()
            self.bus.publish({"type": "event", "name": "agent_status",
                              "connected": True, "port": "auto",
                              "transport": self.link_kind},
                             sticky_key="agent_status")
            while not self.stop_evt.is_set():
                # Pull next state (or any other broadcast event) from the bus.
                try:
                    msg = await asyncio.wait_for(sub.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    msg = None

                if msg is not None and msg.get("type") == "state":
                    if not self.link.send(msg):
                        if not await self._reconnect():
                            return
                        continue
                # Drain every queued device command — single writer, so
                # commands can't interleave or overwrite one another. A
                # failed command send means the link dropped — reconnect
                # immediately rather than stalling until the next state.
                cmd_failed = False
                while not cmd_failed:
                    try:
                        cmd = self._tx_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if not self.link.send(cmd):
                        cmd_failed = True
                if cmd_failed:
                    if not await self._reconnect():
                        return
                    continue

                for ev in self.link.read_events():
                    _print_event(ev, agent=self)
                    self.bus.publish(ev)
                    self._dispatch_event(ev)
        finally:
            self.bus.unsubscribe(sub)
            self.link.close()
            self._drain_tx_queue()
            # Fail any outstanding event waiters so awaiting operations
            # don't hang once the link is gone.
            for _match, fut in list(self._event_waiters):
                if not fut.done():
                    fut.set_exception(ConnectionError("device link closed"))
            self._event_waiters.clear()

    def _drain_tx_queue(self) -> None:
        """Discard all queued device commands. Called when the link drops —
        queued commands are stale and must not replay on reconnect."""
        while not self._tx_queue.empty():
            try:
                self._tx_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _purge_tx_queue(self, predicate: Callable[[dict], bool]) -> int:
        """Selectively drop queued device commands matching `predicate`.
        Other commands keep their order. Returns the number purged.

        Used to stop a failed pet install from flooding the device with
        buffered `pet_install_chunk` commands after we've already given
        up — the device would just reject each with `ok=false` and log
        "chunk without start"."""
        kept: list[dict] = []
        purged = 0
        while not self._tx_queue.empty():
            try:
                c = self._tx_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if predicate(c):
                purged += 1
            else:
                kept.append(c)
        for c in kept:
            try:
                self._tx_queue.put_nowait(c)
            except asyncio.QueueFull:
                break
        return purged

    def _tx_put(self, cmd: dict) -> None:
        """Enqueue an outbound device command. On overflow drop the oldest —
        a backed-up queue means the link is stalled, so the freshest command
        is the one worth keeping (mirrors the bus's drop-oldest policy)."""
        if self._tx_queue.full():
            try:
                self._tx_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._tx_queue.put_nowait(cmd)
        except asyncio.QueueFull:
            pass

    def _dispatch_event(self, ev: dict[str, Any]) -> None:
        """Resolve any one-shot event waiters that match this device event."""
        if not self._event_waiters:
            return
        remaining: list[tuple[Callable[[dict], bool], "asyncio.Future[dict]"]] = []
        for match, fut in self._event_waiters:
            if fut.done():
                continue
            try:
                hit = match(ev)
            except Exception:
                hit = False
            if hit:
                fut.set_result(ev)
            else:
                remaining.append((match, fut))
        self._event_waiters = remaining


async def _main(cfg_path: Path | None, ipc_enabled: bool, ipc_port: int) -> None:
    runtime = AgentRuntime(cfg_path)

    # Wire python-logging → bus so IPC clients see agent logs too.
    bus_handler = BusLogHandler(runtime.bus)
    bus_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(bus_handler)

    ipc: IPCServer | None = None
    if ipc_enabled:
        ipc = IPCServer(runtime.bus, runtime.handle_cmd, port=ipc_port,
                        activity=runtime.activity)
        try:
            await ipc.start()
        except OSError:
            # Another agent owns the port. Don't start a competing pipeline.
            # Exit non-zero (special code 3) so the supervisor distinguishes
            # "already-running peer" from a normal clean shutdown and stops
            # respawning in a tight loop.
            sys.exit(3)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runtime.stop_evt.set)
        except NotImplementedError:
            pass

    # Kick the pricing catalog's daily refresh on its own task. Fails
    # silently if offline / httpx not installed — bundled snapshot stays
    # the source of truth in that case.
    from dashd.pricing import starts_background_refresh
    starts_background_refresh(loop)

    log.info("dashd starting; waiting for device...")
    try:
        await asyncio.gather(
            runtime.push_loop(),
            runtime.link_loop(),
        )
    finally:
        if ipc:
            await ipc.stop()
        logging.getLogger().removeHandler(bus_handler)
        log.info("dashd stopped")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="dashd")
    ap.add_argument("--config", type=Path, default=None, help="Path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--no-ipc", action="store_true", help="Disable the local IPC server")
    ap.add_argument("--ipc-port", type=int,
                    default=int(os.environ.get("DASHD_IPC_PORT") or 52317),
                    help="Local IPC TCP port (default 52317)")
    return ap.parse_args()


def run() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_time=True)],
    )
    try:
        asyncio.run(_main(args.config, not args.no_ipc, args.ipc_port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
