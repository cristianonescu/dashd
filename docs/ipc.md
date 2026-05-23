# Local IPC

The agent exposes a **local-only TCP server** on `127.0.0.1:52317` (override with `--ipc-port` or `DASHD_IPC_PORT`). Same newline-JSON framing as the USB link. The Electron app uses it; you can also write your own client.

## Auth

A 32-byte URL-safe token is written to `~/.config/dashd/ipc.token` (mode 0600) the first time the agent starts. The client must send a `hello` with that token before any state frames flow.

This is **not a security boundary** — anyone with read access to your home directory can read the token. It's just a "don't accidentally bind to random local scripts" check.

## Wire protocol

### Client → Server

```jsonc
// First message after connect — required.
{"type": "hello", "token": "<contents of ipc.token>"}

// After hello_ack with ok=true:
{"type": "cmd", "name": "reload_config"}
{"type": "cmd", "name": "show_page", "page": "AI Spend"}
{"type": "cmd", "name": "set_brightness", "value": 80}

// Auto-advance page cycling (v0.1.12+). Default ON with 8 s sequential.
// All fields optional — omitted fields keep the device's current value.
// Persisted in device NVS so cycling continues even with the host offline.
{"type": "cmd", "name": "set_auto_advance",
 "enabled": true, "interval_s": 8, "mode": "sequential"}

{"type": "cmd", "name": "stop"}            // ask the agent to exit

// Window-visibility hint. The agent throttles its collectors down to a
// slow idle tick (~30 s, see config.update.idle_interval_seconds) when
// no client has signalled active=true AND no USB device is connected.
// Send active=true on window show, false on hide.
{"type": "cmd", "name": "set_active", "active": true}

// Bluetooth discovery. Scans for dashd BLE devices; the agent
// replies with a `ble_scan_result` event listing what it found.
{"type": "cmd", "name": "ble_scan"}
// Device → host: {"type":"event","name":"ble_scan_result",
//                 "devices":[{"name":"dashd-A3F2","address":"…","rssi":-54}]}

// Bluetooth pairing. `ble_pair` connects to the chosen device — which then
// shows a 6-digit code on its screen — and the agent replies with a
// `ble_pairing_state` event ("connecting" → "awaiting_code", or "paired"
// straight away if this host is already trusted, or "failed"). The user
// reads the code off the device and submits it with `ble_pair_code`.
{"type": "cmd", "name": "ble_pair", "address": "<from ble_scan_result>"}
{"type": "cmd", "name": "ble_pair_code", "code": "482915"}
//   → events: {"type":"event","name":"ble_pairing_state","state":"awaiting_code"}
//             {"type":"event","name":"ble_pairing_state","state":"paired"}

// Forget a paired device (no address = forget all). `ble_trusted_list`
// queries. Both reply with a `ble_trusted` event listing addresses. The
// trust store is persisted at ~/.config/dashd/ble_trust.json (mode 0600).
{"type": "cmd", "name": "ble_untrust", "address": "<addr>"}
{"type": "cmd", "name": "ble_trusted_list"}

// Transport selection. Override the [transport].mode in config.toml at
// runtime by setting the DASHD_TRANSPORT env var ("cable" | "bluetooth"
// | "auto") before the agent starts. The Electron app does this on its
// own — Settings → Connection writes the choice to UI prefs and
// restarts the agent so it picks up the new mode.

// Act on a top-process row. `target` is the canonical app label that
// appeared in system.top_ram / system.top_cpu (post aggregation).
{"type": "cmd", "name": "proc_action", "target": "Google Chrome", "action": "reveal"}
{"type": "cmd", "name": "proc_action", "target": "Google Chrome", "action": "activity_monitor"}
{"type": "cmd", "name": "proc_action", "target": "Google Chrome", "action": "quit"}
// Device → host: {"type":"event","name":"proc_action_done","ok":true,"matched":23,...}

// Firmware OTA. The agent talks to GitHub Releases on the host's behalf
// — no API token needed (public endpoint). It compares the device's
// reported `fw_version` (captured from boot/hello_ack) against the
// release's tag and replies via `fw_update_state` events.
{"type": "cmd", "name": "fw_check_update"}
//   → {"type":"event","name":"fw_update_state",
//      "state":"up_to_date","current":"0.1.12","latest":"0.1.12"}
//   or {"…","state":"available","current":"…","latest":"…",
//       "notes":"<markdown>", "size_ble":1015200, "size_usb":760000}
//   or {"…","state":"error","error":"could not reach GitHub Releases"}

// Start a firmware update — only valid after `fw_check_update` returned
// "available" (or, if you call it cold, the agent fetches itself).
// Streaming progresses through multiple state events:
//   downloading → flashing → rebooting → done   (success path)
//   downloading → … → error                       (failure path)
{"type": "cmd", "name": "fw_update_start"}
// Progress events the UI hooks for the progress bar:
//   {"type":"event","name":"fw_update_state",
//    "state":"downloading","version":"0.1.12","bytes":N,"total":M}
//   {"type":"event","name":"fw_update_state",
//    "state":"flashing","version":"0.1.12","bytes":N,"total":M}
//   {"type":"event","name":"fw_update_state",
//    "state":"rebooting","version":"0.1.12"}
//   {"type":"event","name":"fw_update_state",
//    "state":"done","version":"0.1.12"}

// Cancel a flight in progress.
{"type": "cmd", "name": "fw_update_abort"}
```

### Server → Client

```jsonc
{"type": "hello_ack", "ok": true}                 // response to hello

// Then continuous fan-out of:
{"type": "state", "ts": 1779269000, "system": {...}, "ai": {...}, ...}
{"type": "event", "name": "log",  "level": "info",  "logger": "dashd.fw", "msg": "..."}
{"type": "event", "name": "agent_status", "connected": true, "port": "auto", "transport": "usb"}
{"type": "event", "name": "page_changed", "page": "System"}
```

State frames are the same payload that goes to the device. Logs come from both Python logging (any agent module) **and** firmware `log` events the device emits — both pass through the bus.

## Backpressure

Each subscriber gets a bounded queue. If a slow client falls behind, the oldest unsent frame for that client is dropped (drop-oldest) — we'd rather show a fresh frame than block the producer or hold gigabytes in memory.

## Sticky replay

A handful of one-shot events — currently `agent_status` and `ble_trusted` — are "sticky": the bus caches the most recent value per key and replays it to any newly authenticated subscriber, right after `hello_ack` and before any live frames. This fixes a class of bug where the Electron app launched while the device was already plugged in, missed the initial `connected: true` event (which fired before the IPC client connected), and reported the device as disconnected until the user physically unplugged and replugged. New clients now learn the current link state — and the trusted-BLE-devices list — on join, without needing a fresh transition.

## Minimal Python client

```python
import json, socket
from pathlib import Path

token = (Path.home()/".config/dashd/ipc.token").read_text().strip()
s = socket.create_connection(("127.0.0.1", 52317))
f = s.makefile("rwb", buffering=0)
f.write((json.dumps({"type":"hello","token":token})+"\n").encode())
print("ack:", json.loads(f.readline()))
for _ in range(5):
    print(json.loads(f.readline()))
```
