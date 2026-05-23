# dashd — TODO

Anything we're deliberately not building right now lives here so we can pick
it up later without re-deciding the design every time.

---

## Future features (not yet started)

### 1. Plugin system — HTML + JS user pages

A plugin is a directory under `~/.config/dashd/plugins/<name>/`:

```
manifest.json   name, version, refresh rate, dimensions
index.html     entry point
script.js      optional — subscribes to dashd state
style.css      optional
```

The HTML/JS runs in a sandboxed iframe with `sandbox="allow-scripts"` and a
preload-injected API:

```js
window.dashd.state.subscribe((state) => …)   // every agent push
window.dashd.state.current                    // latest snapshot
window.dashd.events.subscribe((evt) => …)     // agent + firmware events
window.dashd.sendCmd({ name: "show_page", page: "AI Spend" })
```

**Design decisions (settled):**

- **Render to device** via Electron-as-renderer (not UI-only). Hidden
  240×320 `BrowserWindow` per plugin → periodic `webContents.capturePage()`
  → PNG → RGB565 → streamed to a new firmware `PAGE_PLUGIN` page. Trade-off:
  plugins only animate on the device while the Electron app is running.
  When the UI closes, the device shows the last captured frame or a
  "plugin host offline" state.
- **File-based editor (not in-app Monaco)**. Settings → Plugins gets a
  "Scaffold new plugin…" button that creates the directory with a starter
  template and opens it in the user's default editor. Lighter, real-IDE
  friendly, no 3 MB Monaco bundle.
- **Ship 2–3 example plugins** on first install: big clock, countdown to a
  configurable date, live crypto/stock price. Gives the feature a story.

**Scope:** ~2.5–3 days.
- Day 1 — plugin loader + manifest schema + filesystem watcher + iframe
  host in a new "Custom" UI tab + starter-template scaffolder.
- Day 2 — hidden 240×320 `BrowserWindow` per plugin, periodic
  `capturePage()`, PNG→RGB565 in Node (via `sharp`), `frame_install_*`
  cmds on the device (mirroring the existing `pet_install_*` flow).
- Day 3 — firmware `PAGE_PLUGIN` that displays an arbitrary uploaded
  frame, multi-plugin support (cycle through them), error states,
  docs + example plugins.

**Plugin ideas worth shipping as examples or curated templates:**
- Big analog clock (`<canvas>` typography)
- Countdown to a date (release day, birthday)
- Live crypto / stock price (plugin owns its `fetch()`)
- Weather card (plugin calls OpenWeather; user provides their own key)
- "Now playing" from Spotify
- Personal Pomodoro UI (lives alongside the built-in one in #2)
- GitHub contributions heatmap (plugin owns its auth)

### 2. Pomodoro / focus timer

Built-in 25/5 timer on the device. Long-press + short-press combo starts
a session; countdown takes over the title bar across every page. Pet
reactor: `running` while focused, `wave` at the end, `tired` after
4 cycles. Optional macOS shortcut integration to toggle Focus mode while
a session runs.

**Scope:** ~1 day. New `FocusState` slot in `DataStore`, host-issued
`focus_start` / `focus_stop` cmds, a tiny countdown widget rendered into
the title bar by `theme.cpp`.

### 3. Historical sparklines on every page

Every numeric value already has a stream. Keep a 10-minute rolling buffer
in the agent (~300 floats per metric) and ship per-metric history arrays
in the wire format. Pages get a `widgets::sparkline()` helper that
renders a 32-bar inline mini-chart next to the current value.

**Scope:** ~1.5 days. `dashd.history.Buffer` in the agent, optional
`*_history` arrays in the state payload, firmware widget.

Concrete examples: CPU load over the last 10 min on System; AI spend
curve on AI Spend; commits-per-hour on Dev Flow.

### 4. Local web mirror

The agent already runs a TCP IPC server. Add a read-only HTTP/SSE
endpoint on a sibling port (52318) that serves a tiny static page
mirroring the device — same JSON state, same `LiveView` React tree from
the UI, reused. Token-gated like the IPC server, off by default, opt-in
in Settings → General. Optional QR code so you can scan it from your
phone in another room.

**Scope:** ~1 day. `aiohttp` server in the agent + reusing existing
React `LiveView` component.

### 5. Notifications + audible alerts (with hardware piezo)

Add a piezo buzzer on a free GPIO (~$0.50 part). Firmware blips it for
critical events the user is currently missing: meeting in 60 s, CI just
broke, AI block hit 95 %. Short-press during an alert = snooze.

**Scope:** ~1.5 days + a soldering iron. New GPIO + LEDC tone driver in
firmware, alert-priority pipeline in the agent (de-dupes, respects quiet
hours), Settings → Alerts pane (per-event sound on/off, tone picker).

**Software-only fallback:** if no piezo is wired, flash the backlight at
4 Hz for the same alert duration. Still cheap, still attention-grabbing.

---

## Deferred work from earlier phases

These came up during the original build-out and were explicitly
de-scoped. Each has enough context here that we don't need to re-decide.

### Collectors

- **Slack** (`messages.slack`) — collector stub exists, returns `None`.
  Needs a user / bot token; the Messages page has a slot ready for it.
- **Teams** (`messages.teams`) — same shape as Slack via Microsoft Graph.
  Re-uses the existing calendar OAuth flow.
- **Codex cost** — `ai.codex.cost_today_usd` stays `null` because
  Anthropic / OpenAI don't publish Codex pricing publicly. `tokens_today`
  is populated since v0.1.3 via the cumulative-delta diff (see
  `agent/dashd/collectors/codex.py`). Wire this up once a pricing
  source exists.
- **WhatsApp** — `messages.whatsapp` slot exists, always `null`. No
  reliable public API. See [docs/whatsapp.md](docs/whatsapp.md).

### UI

- **Per-collector form-based settings editor** — currently the
  Collectors pane is read-only; edits happen in `~/.config/dashd/config.toml`.
  A guided editor with `tomlkit` (comment-preserving writes) +
  `keytar` (OS-keyring secret storage) is the next step.

### Packaging / release

- **Code signing + notarization** — currently unsigned. macOS users get
  the Gatekeeper warning; Windows gets SmartScreen. To remove: Apple
  Developer Program ($99/yr) + Windows EV cert (~$300/yr). Hooks already
  present in [ui/electron-builder.yml](ui/electron-builder.yml).
- **Auto-update wired to a real release host** — `electron-updater` is
  hooked up to GitHub Releases, but we haven't shipped any tagged builds
  yet. First `git tag v0.2.0 && git push --tags` exercises the CI
  workflow in `.github/workflows/build.yml`.
- **Per-OS / per-arch agent binaries via CI** — PyInstaller can't
  cross-compile. Workflow is already configured to run per-OS; we just
  haven't tagged a release.
- **Firmware flashing from the UI** — bundle esptool + Web Serial in the
  Electron app so users don't have to run `pio run -t upload`. Adds
  native-USB plumbing on Windows and Linux.

---

## Polish / small things

- **App icon** — current icon is fine; replace with a sharper original
  if/when we have one.
- **Status bar in the UI** — show last frame timestamp + agent uptime
  more prominently.
- **Tray-icon badge** — paint a tiny red dot on the menu-bar icon when
  there's an active `crit` suggestion.
- **Settings: search box** — the settings tab is getting big. Add a
  filter so the user can jump to "rotation" or "GitHub token".
- **Dashboard window resizing** — the UI assumes 1100×760; check that
  cards stack reasonably on small windows.
