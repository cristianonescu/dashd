# Wire protocol

Newline-delimited UTF-8 JSON, one message per line, same framing in both directions. The same JSON travels over either transport:

- **USB-CDC** — 460 800 baud, point-to-point.
- **BLE GATT** — write-without-response for host → device, notify for device → host. Same JSON, MTU-chunked by the GATT layer.

The full protocol catalog (with all collector fields) is in section 5 of the project spec. This document tracks what is actually implemented today.

## State frame

### Host → Device (every 2 s)

```json
{
  "type": "state",
  "v": 1,
  "ts": 1779266062,
  "source": {"host": "mbp-cristian", "os": "Darwin", "id": "a1b2c3d4e5f6"},
  "system": {
    "cpu_pct": [20, 30, 11, 18],
    "cpu_freq_mhz": 3200,           // current clock (v0.1.12+)
    "cpu_freq_max_mhz": 4500,       // platform max (v0.1.12+)
    "load_1m": 2.41,                // (v0.1.12+)
    "load_5m": 1.98,
    "load_15m": 1.62,
    "ram_pct": 42,                  // includes cache (macOS overestimates)
    "ram_pressure_pct": 31,         // active+wired only, cache excluded
    "ram_used_gb": 25.6,
    "ram_total_gb": 64.0,
    "ram_swap_pct": 0,              // (v0.1.12+)
    "ram_swap_used_gb": 0.0,
    "ram_swap_total_gb": 4.0,
    "ram_active_gb": 18.4,          // (v0.1.12+, where exposed)
    "ram_inactive_gb": 7.2,
    "ram_cached_gb": 12.3,
    "disk_pct": 1,
    "net_up_kbps": 0,               // aggregate (kept for back-compat)
    "net_down_kbps": 81,
    "ifaces": [                     // top-3 by traffic (v0.1.12+)
      {"name": "en0", "up_kbps": 12, "down_kbps": 81,
       "up_total_mb": 4, "down_total_mb": 27,
       "is_up": true, "is_active": true},
      {"name": "utun4", "up_kbps": 0, "down_kbps": 0,
       "up_total_mb": 0, "down_total_mb": 0,
       "is_up": true, "is_active": false}
    ],
    "battery_pct": 80,
    "battery_charging": true,
    "temp_cpu_c": null
  },
  "gpu": {                          // top-level (v0.1.12+)
    "available": true,              // false ⇒ no GPU detected / not supported
    "vendor": "Apple",              // "Apple" | "NVIDIA" | "AMD"
    "name": "Apple M5 Max",
    "util_pct": 26,
    "vram_used_mb": 2584,
    "vram_total_mb": null,          // null on Apple Silicon (unified memory)
    "temp_c": null,
    "power_w": null,
    "count": 1                      // total GPUs detected (we surface the first)
  },
  "ai": {
    "claude_code": {
      "tokens_today": 15489679,
      "cost_today_usd": 37.11,
      "tokens_block": 8410230,         // tokens consumed since current 5h window opened
      "cost_block_usd": 21.05,
      "block_pct": 26,                 // legacy alias of block_elapsed_pct
      "block_elapsed_pct": 26,         // 0..100, time-elapsed in 5h window
      "block_used_pct": 12,            // 0..100, tokens / budget; null if no budget set
      "block_resets_in_min": 221,
      "block_resets_at": 1779283662,   // epoch; from "Claude AI usage limit reached" event when present
      "tokens_this_week": 92114338,
      "cost_this_week_usd": 218.40,
      "burn_tokens_per_min": 5320,     // rolling burn rate over this block
      "burn_projected_cap_min": 73,    // null unless block_used_pct is computable (budget set)
      "models": {"opus": 15489679, "sonnet": 0, "haiku": 0},
      "top_projects": [
        {"name": "Users/foo/dashd",   "tokens": 12000000},
        {"name": "Users/foo/other",   "tokens":  3489679}
      ]
    },
    "codex": {
      "tokens_today": 412230,          // derived from cumulative-total diffs
      "cost_today_usd": null,          // no public Codex pricing
      "block_pct": 42,                 // legacy alias of block_elapsed_pct
      "block_elapsed_pct": 42,         // Codex reports usage % directly
      "block_used_pct": 42,            // same value for Codex
      "block_resets_in_min": 142,
      "block_resets_at": 1779265200,
      "session_active": true
    }
  },
  "git": {
    "branch": "main",
    "commits_today": 0,
    "loc_added": 0,
    "loc_removed": 0,
    "minutes_since_last_commit": null
  }
}
```

Any field may be `null`. The firmware tolerates missing fields and renders `--` / `n/a` accordingly.

**Envelope fields:**
- `v` — protocol version (integer). Bumped on a breaking wire-format change so a peer can negotiate. v1 firmware ignores it.
- `source` — the originating machine: `host` (hostname), `os` (`Darwin`/`Linux`/`Windows`), `id` (a hash of the MAC-based node id — the raw MAC never goes on the wire). **Display / routing metadata only — never trusted for identity or authorization.** It's reserved for the future multi-computer feature; v1 firmware ignores it. A connected peer could put any value here, so future routing binds frames to the authenticated session, not to `source.id`.

**Block fields semantics:**
- `block_elapsed_pct` — fraction of the 5h window already passed. Updated continuously; resets when a new block starts.
- `block_used_pct` — fraction of the user's per-block token budget consumed (only present when the budget is set via `[collectors.claude_code] block_token_budget` / `DASHD_CLAUDE_BLOCK_BUDGET`).
- `block_resets_at` — epoch when the current block ends. Sourced from Claude Code's `"Claude AI usage limit reached|<epoch>"` error events when the user has hit the cap; otherwise derived from hour-floored first-message-in-window + 5h.
- `block_pct` — kept for back-compat with v0.1.2 firmware; equals `block_elapsed_pct`. New firmware should read the explicit field.

**Codex tokens:** dashd derives per-session tokens by diffing successive cumulative `total_token_usage` values from `token_count` events (the trick borrowed from ccusage). State persists in `~/.config/dashd/codex_state.json`. Codex's own `rate_limits.primary.used_percent` is the actual quota usage — exposed as `block_used_pct` and `block_pct`; there's no time-elapsed metric for Codex. Cost stays `null` because Anthropic / OpenAI don't publish Codex pricing publicly.

### Anthropic OAuth gauges (v0.1.12+, opt-in)

When the user enables the Anthropic OAuth API in **Settings → Privacy**, dashd polls `GET https://api.anthropic.com/api/oauth/usage` every 60 s and surfaces the response in a new top-level `anthropic` block:

```jsonc
"anthropic": {
  "available": true,
  "reason": "ok",                        // "disabled"|"no_token"|"401"|"network"|"ok"
  "session": {                            // 5h rate-limit window
    "used_pct": 2.0,
    "resets_at": 1779548400,
    "resets_in_min": 233,
    "window_minutes": 300,
    "pace_delta_pct": -1.2,               // actual - expected; <0 = ahead of pace
    "pace_status": "on_track",            // |slightly_ahead|ahead|far_ahead|slightly_behind|behind|far_behind|warming_up
    "will_last_to_reset": true,           // burn-rate projection vs reset time
    "eta_to_cap_min": null                // null when rate≤0
  },
  "weekly":        {…same shape…},
  "sonnet_weekly": {…same shape…},
  "extra_usage": {
    "enabled": true, "limit_usd": 2000, "used_usd": 0,
    "used_pct": 0, "currency": "USD"
  }
}
```

When the user hasn't opted in, or the token is missing / expired / unreachable, the block is `{"available": false, "reason": "<short tag>"}`. The Electron app's Usage tab + tray popover prefer this block when present and fall back to the JSONL-derived `block_elapsed_pct` / `block_used_pct` otherwise. The firmware ignores the entire block — it's transparent on the wire to the device.

### Device → Host (events)

```json
{"type": "event", "name": "boot", "fw_version": "0.1.12", "v": 1}
{"type": "event", "name": "hello_ack", "fw_version": "0.1.12", "v": 1}
{"type": "event", "name": "page_changed", "page": "AI Spend"}
{"type": "event", "name": "button_long_press"}
{"type": "event", "name": "log", "level": "info", "msg": "dashd fw 0.1.12 up, 8 pages, w=240 h=320"}
```

## Handshake

On a freshly-connected link the host sends `{"type":"cmd","name":"hello","v":1}`; the device replies with a `hello_ack` event carrying its `fw_version` and protocol `v`. Over USB-CDC the handshake is advisory (the link is point-to-point and works without it). Over BLE it is mandatory: the device binds the session to the transport the `hello` arrived on (one active transport at a time — USB is exclusive, BLE is a single session slot in v1) and routes all replies back to it.

**BLE connect sequence** — the device drops every BLE frame until the central authenticates, so the order is: **connect → write the 6-digit code or trust token to the AUTH characteristic → `hello` → `hello_ack`**. If no `hello_ack` arrives, the token was rejected (re-pair). See `docs/ipc.md` for the pairing commands.

The agent forwards `log` events to its `dashd.fw` logger at the matching level (`debug` / `info` / `warn` / `error`), so the firmware can write through the agent instead of needing a separate serial monitor. Other events are logged once at info level. Future phases may react to them (e.g. `show_page` ack).

In firmware, use the `LOGI` / `LOGW` / `LOGE` / `LOGD` macros from `usb_link.h` — they're printf-style and emit a single JSON event line.

## Network collectors

```jsonc
{
  "github":   { "prs_awaiting_review": 3, "ci_failures_24h": 1, "unread_notifications": 5 },
  "calendar": { "next_event_title": "Sprint Planning", "next_event_in_min": 17, "today_remaining": 3 },
  "messages": {
    "email":    { "unread": 12 },
    "slack":    null,
    "teams":    null,
    "imessage": null,
    "whatsapp": null
  }
}
```

## Top processes + suggestions

The system block carries top-N processes (N = 3) sorted by CPU and by RAM. A top-level `suggestions` array carries the latest advice from the agent's rule engine — already ranked, ready to render.

```jsonc
{
  "system": {
    // ...existing fields...
    "ram_pressure_pct": 48,                   // active+wired only; cache excluded
    "top_cpu": [
      { "name": "Google Chrome", "cpu_pct": 42.0, "ram_pct": 16.2, "ram_mb": 10366, "procs": 23 },
      { "name": "VM (Claude)",   "cpu_pct":  2.0, "ram_pct":  4.3, "ram_mb":  2771, "procs": 1 }
    ],
    "top_ram": [
      { "name": "Google Chrome", "cpu_pct": 42.0, "ram_pct": 16.2, "ram_mb": 10366, "procs": 23 },
      { "name": "VM (Claude)",   "cpu_pct":  2.0, "ram_pct":  4.3, "ram_mb":  2771, "procs": 1 }
    ],
    "memory_leak": {                          // optional: only when an app's RSS is climbing
      "name": "Google Chrome", "delta_mb": 540, "window_min": 5
    }
  },
  "suggestions": [
    { "severity": "crit", "text": "RAM 93% — close Slack" },
    { "severity": "warn", "text": "Claude block 78%" },
    { "severity": "info", "text": "2 PRs need review" }
  ]
}
```

Severities are `"crit"` / `"warn"` / `"info"`. The firmware renders them on the **Tips** page in the corresponding theme color (CRIT / WARN / ACCENT). Process names are truncated to ~14 chars by the agent so the wire frame stays tight (~840 B with 3 top-CPU + 3 top-RAM + 5 suggestions).

Each `messages.*` channel is its own nested object so future collectors (Slack mentions, Teams DM counts, etc.) can grow extra fields without breaking the firmware's parse path. Counts of `-1` from collectors map to `null` on the wire if no payload at all; otherwise the firmware renders `--` from a missing field.

## Host → Device commands

All `cmd` messages travel over whichever transport currently owns the session — USB-CDC or BLE (see the Handshake section; the device routes replies back to the same transport). The device persists theme, threshold, brightness, and pages-mask values to NVS so they survive reboots.

```jsonc
{"type": "cmd", "name": "show_page", "page": "AI Spend"}
{"type": "cmd", "name": "set_brightness", "value": 80}       // 0..100 %

// Auto-advance: device cycles through enabled pages on a timer (v0.1.12+).
// Default ON with 8 s sequential. All fields optional — omitted fields
// keep their current value. Persisted to NVS so the cycle continues
// across reboots, including when the host is offline.
{"type": "cmd", "name": "set_auto_advance",
 "enabled": true,
 "interval_s": 8,                     // 3..300 (clamped device-side)
 "mode": "sequential"}                // "sequential" | "random"
// A button press or host `show_page` resets the countdown so the user
// gets a full interval to read the page they just selected.

{"type": "cmd", "name": "set_theme",
 "colors": {                                                  // RGB565 uint16
   "bg": 2145, "fg": 65535, "dim": 31727,
   "good": 15946, "warn": 54212, "crit": 63658, "accent": 19839
 }}

{"type": "cmd", "name": "set_thresholds",
 "thresholds": {
   "cpu_warn": 70, "cpu_crit": 90,
   "ram_warn": 80, "ram_crit": 95,
   "calendar_soon_min": 15,
   "commit_fresh_min": 30
 }}

{"type": "cmd", "name": "set_pages_enabled", "mask": 255}    // bit i = page i; 0 = all

// Pet overlay (small animated sprite, on top of every page).
{"type": "cmd", "name": "pet_set_enabled", "enabled": true}
{"type": "cmd", "name": "pet_set_corner",  "corner": 1}      // 0=TR 1=BR 2=BL 3=TL
{"type": "cmd", "name": "pet_set_state",   "state": "wave"}
{"type": "cmd", "name": "pet_set_active",  "slug": "claw-d"} // "default" = embedded Claw'd

// Pet install (host → device). Streamed in newline-JSON chunks; the device
// ACKs every chunk so the host can flow-control without blasting the
// 256 B kernel RX buffer.
{"type": "cmd", "name": "pet_install_start", "slug": "claw-d", "size": 279268}
{"type": "cmd", "name": "pet_install_chunk", "seq": 0, "data": "<base64 ≤2 KB>"}
//   device → host:  {"type":"event","name":"pet_install_chunk_ack","seq":0,"ok":true}
...
{"type": "cmd", "name": "pet_install_end"}
//   device → host:  {"type":"event","name":"pet_install_ended","ok":true,"slug":"claw-d"}

{"type": "cmd", "name": "pet_remove", "slug": "claw-d"}

// Catalog (agent-side, doesn't reach the device).
{"type": "cmd", "name": "pets_catalog"}        // → event "pets_catalog" w/ entries
{"type": "cmd", "name": "pets_install", "slug": "claw-d"}  // download+convert+stream

{"type": "cmd", "name": "set_pages_order",
 "order": [0, 2, 1, 3, 4, 5, 6, 7]}                             // 8-slot list, ids 0..7; [] = default

{"type": "cmd", "name": "set_text_scales",
 "scales": {"title": 1, "label": 1, "value": 2, "big": 1}}   // each clamped 1..4

{"type": "cmd", "name": "set_layout",
 "show_title": true, "show_footer": false, "rotation": 0}    // rotation 0..3 (×90°)

// Per-element show/hide. Each ID corresponds to a `visibility::shown("…")`
// gate in firmware/src/pages/*. The device persists the hidden set in NVS.
{"type": "cmd", "name": "set_element_visible", "id": "system.battery", "visible": false}
{"type": "cmd", "name": "reset_visibility"}                  // re-show everything
{"type": "cmd", "name": "get_visibility"}                    // → event "visibility_state"

{"type": "cmd", "name": "reset_prefs"}                       // clear ALL NVS overrides
```

All settings persist to ESP32 Preferences (NVS) so they survive reboots and host disconnects. `reset_prefs` clears every override back to compile-time defaults.

The Electron UI uses the local IPC server to issue these; the agent forwards them to the device over the active transport (USB or BLE).

## Firmware OTA

The firmware can be updated over either transport. The agent downloads the matching `.bin` from GitHub Releases (`-ble.bin` over Bluetooth, `-usb.bin` over the cable), computes its SHA256, and streams it to the device using the same ACK-windowed chunk pattern the pet installer already uses.

```jsonc
// Host → Device. Begin a write to the inactive OTA slot. Size + sha256
// are used by the device to validate the write before flipping the
// active slot. Reject if the size exceeds the slot (currently 1.5 MB).
{"type": "cmd", "name": "fw_update_begin",
 "size": 1015200,
 "sha256": "8d3f…2a7b",            // 64 lowercase hex chars
 "version": "0.1.12"}

// Device → Host:
{"type":"event","name":"fw_update_started","ok":true,"version":"0.1.12"}
//   on failure: {"…","ok":false,"error":"bad sha256 (expected 64 hex chars)"}

// Stream chunks. The agent uses WINDOW=1 for firmware OTA (strictly
// send-and-wait, NOT the windowed flow control pet_install uses):
// each esp_ota_write takes ~30-50 ms while flash sector erase+program
// runs, and pumping 8 chunks (~23 KB) in that window overflows the
// ESP32-C3's small USB-CDC kernel RX buffer. WINDOW=1 is reliable;
// a ~1 MB firmware still ships in ~30 s over USB.
{"type": "cmd", "name": "fw_update_chunk", "seq": 0,
 "data": "<base64-encoded ≤2 KB>"}

// Device → Host (per chunk):
{"type":"event","name":"fw_update_chunk_ack","seq":0,"ok":true}

// Device → Host (every ~16 KB of received data, plus on completion):
{"type":"event","name":"fw_update_progress",
 "bytes": 16384, "total": 1015200}

// Finalize. Device re-reads the just-written bytes, hashes them, and
// only flips the boot pointer if the hash matches the host-provided
// sha256. On success the device reboots in ~500 ms — the host won't
// see a reply because the link drops. On failure, an event is emitted
// and the previous firmware keeps running.
{"type": "cmd", "name": "fw_update_end"}
//   on failure: {"type":"event","name":"fw_update_done",
//                "ok":false,"error":"sha256 mismatch"}

// Cancel a flight in progress. Aborts the OTA write; previous firmware
// stays active. Safe to call at any time.
{"type": "cmd", "name": "fw_update_abort"}
```

**Rollback safety net.** Before the new firmware finishes booting it is in "pending verification" mode. If it doesn't call `esp_ota_mark_app_valid_cancel_rollback()` (firmware does this right after the first `boot` event, ~150 ms into setup), the bootloader reverts to the previous slot on next reset. The device cannot be bricked by a bad image — only by writing one and physically pulling power before the post-boot probe runs, in which case the rollback fires automatically on the next power-cycle.

**Variant detection.** The host picks the matching `.bin`:
- Cable transport → `dashd-firmware-vX.Y.Z-usb.bin`
- Bluetooth transport → `dashd-firmware-vX.Y.Z-ble.bin`

Both builds use the same OTA partition layout — they can replace each other.

## Framing rules

- One message = one line terminated by `\n`. No `\r`. No leading whitespace.
- Lines longer than 4096 bytes (`USB_RX_LINE_MAX`) are dropped by the firmware — both transports resync at the next `\n`. Phase 2 frames are ~600 bytes; base64 pet-install chunks are the largest at ~2.8 KB — still well under the cap.
- Each internal bus subscriber (the USB link, every IPC client) has a bounded **16-deep** queue. On overflow the **oldest** frame is dropped, so a slow consumer always sees the freshest state rather than a stale backlog.
- Either side may close the link at any time; the other side reconnects with exponential backoff (max 30 s on the agent, immediate retry on the device).
