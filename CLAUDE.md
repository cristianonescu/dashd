# Notes for future Claude sessions on dashd

Lessons accumulated across several intense release loops. Read these
before touching the release workflow, OTA code, AI-usage collectors,
or the Mac auto-update path — every one of them cost real debugging
time the first time around.

## Project rhythm

- **Always update docs BEFORE tagging a release.** No exceptions.
  README.md, docs/*.md, CLAUDE.md — audit them against the code
  before `git tag`. The audit loop is: open the docs that reference
  what changed, fix stale claims, run the codex doc-audit subagent
  for an independent check, fold the doc commits into the release
  squash. If you tag first and update docs after, the release ends
  up with mismatched documentation in the wild — caught in v0.1.6
  when this rule was missed.
- **Releases are single commits.** The user's pattern is "one commit
  per release tag" — soft-reset → squash → force-move the tag →
  force-push main + tag. CI re-triggers on the tag force-push and
  rebuilds against the new commit. Doc-only follow-ups get folded
  into the same release commit (no rebuild needed if no code changed,
  but the tag move still re-triggers CI; the rebuilt binaries
  `--clobber` the previous ones byte-equivalently).
- **Force-pushing main is allowed**, but the auto-mode classifier
  blocks it until the user explicitly approves. Ask once with
  AskUserQuestion before the first force-push of a session; the
  approval covers the whole "amend → force-push" loop.
- **Releases stay drafts until I publish.** CI's
  `gh release view || gh release create --draft` creates a draft.
  Always verify shas before un-drafting, then
  `gh release edit <tag> --draft=false --notes-file /tmp/notes.md`.
- **electron-updater only sees published releases**, not drafts. If
  the user reports "app says I'm on the latest", check if the new
  release is still a draft. This is the easiest miss to make.

## CI workflow (`.github/workflows/build.yml`)

Several edge cases I've hit:

- **`electron-builder --publish=onTagOrDraft` has a "skip if existing
  release is >2h old" guard.** It fires *before* upload and silently
  drops everything. Even `--publish=always` doesn't override it. Use
  `--publish=never` + an explicit `gh release upload <tag> ... --clobber`
  step instead. This is currently how the workflow is set up.
- **`actions/upload-artifact@v4`'s `path:` doesn't accept comma-
  separated values.** Use YAML multi-line:
  ```yaml
  artifact: |
    ui/release/*.AppImage
    ui/release/*.deb
  ```
  A comma-joined string is treated as one literal path and matches
  nothing.
- **Include `*.zip`, `*.blockmap`, and `latest-*.yml`** in BOTH the
  `actions/upload-artifact` paths AND the `gh release upload` glob.
  Mac auto-update needs the .zip; electron-updater delta-updates need
  the blockmap; the feed file is what tells the updater a new release
  exists. Skipping any of these breaks auto-update silently.
- **Auto-create the release before uploading.** Every upload step does
  `gh release view <tag> || gh release create <tag> --draft || true`.
  Idempotent + race-tolerant for the per-OS matrix.
- **`gh release upload` on Windows occasionally hits transient
  HTTP 401 Bad credentials.** Re-running just the failed Windows job
  fixes it. Same `GITHUB_TOKEN`, same workflow, mysterious Microsoft
  thing. Don't waste time debugging — just rerun.
- **macos-13 (Intel) runners are deprecated** and queue for hours.
  Dropped in favor of macos-14 cross-building both arm64 + x64 DMGs.
  Don't add it back without checking GitHub Actions runner availability.
- **`gh release` URLs are CDN-cached.** Right after `--clobber` or
  publishing, expect ~10s of stale 404s before the new file is
  reachable at the public URL. Trust the `gh release view` API view,
  not the public download URL during this window.

## macOS auto-update reality

- **Squirrel.Mac rejects ad-hoc-signed bundles.** Each ad-hoc signing
  pass produces a different designated-requirement hash, so the new
  bundle's DR never matches the installed bundle's. There is no
  workaround without an Apple Developer ID Application certificate
  ($99/yr).
- **The fix in `ui/electron/updater.ts` is a `manual-required` phase.**
  When `autoUpdater.on("error", ...)` fires a message matching
  `/code sign|signature|signing|did not pass validation|requirement/i`,
  surface a calm "available — manual install on macOS" banner instead
  of a scary red error. Never treat this as a real failure.
- **The mac DMG + ZIP are both produced** by setting both targets
  in `electron-builder.yml mac.target`. The DMG is for manual install,
  the ZIP is what Squirrel.Mac actually downloads.
- **`afterPack: build/afterPack.js`** runs the ad-hoc codesign so the
  bundle has *some* signature (Gatekeeper is less alarming on the
  first launch). Without this hook the entire updater path silently
  fails on macOS.

## LittleFS / pet install

- **Pass the partition label EXPLICITLY to `LittleFS.begin()`.** The
  Arduino-ESP32 LittleFS library defaults `partitionLabel="spiffs"`
  (see `~/.platformio/packages/framework-arduinoespressif32/libraries/LittleFS/src/LittleFS.h:27`),
  but our `partitions.csv` labels the partition `littlefs`. Without
  the explicit arg, every mount call returns false with
  `ESP_ERR_NOT_FOUND`, and `LittleFS.format()` is also label-keyed so
  it can't recover. This is what made pet installs silently fail from
  v0.1.0 through v0.1.9 — the v0.1.8 format-on-fail recovery was using
  the same wrong label. Fix in v0.1.10 (`pet_widget.cpp:fs_ensure`):
  `LittleFS.begin(true, "/littlefs", 10, "littlefs")`. Constants live
  at the top of the file (`kLittleFSPartitionLabel`, `kLittleFSBasePath`).
  If you ever add another `LittleFS.*` call site, use those constants.

## Firmware OTA (`agent/dashd/firmware_update.py` + `firmware/src/ota_link.cpp`)

- **WINDOW=1, NOT WINDOW=8 for OTA.** The pet installer uses
  WINDOW=8 to keep the link full while LittleFS writes drain
  (sub-millisecond). Sizing the firmware-side `Serial.setRxBufferSize()`
  for the burst is non-negotiable: 8 chunks × ~2.7 KB per JSON line ≈
  22 KB. v0.1.10 set it to 32 KB; the pre-v0.1.10 value (8 KB) caused
  mid-stream `json parse: InvalidInput (len=~1300)` errors as chunk
  lines were truncated. For firmware OTA, `esp_ota_write` takes
  ~30-50 ms while flash sector erase + program runs. Pumping 8
  chunks (~23 KB) into the USB-CDC stream during a single write
  window overflows the ESP32-C3's small kernel RX buffer (~64-256 B).
  The device silently drops chunk tails, logs
  `json parse: InvalidInput (len=NNN)`, and the OTA aborts at ~2%.
  This was the v0.1.4 bug that v0.1.5 fixed.
- **Both `*.dmg` AND `*.bin` go to the same release.** Firmware bins
  are built by the dedicated `firmware (esp32-c3)` CI job, attached
  via `gh release upload`. The agent's firmware-update IPC downloads
  the matching variant (`-ble.bin` over Bluetooth, `-usb.bin` over
  cable) from the latest release.
- **Auto-rollback is the safety net.** `esp_ota_mark_app_valid_cancel_rollback()`
  is called from main.cpp's setup() right after the first `boot` event.
  If the new firmware never reaches that point, the bootloader reverts
  to the previous slot on next reset. The device cannot be bricked
  by a bad image — only by yanking power before the post-boot probe.
- **`_ota_err_name(esp_err_t)` translates ESP-IDF codes to readable
  strings.** Always use it in error messages. `esp_ota_write=-5379`
  is useless; `esp_ota_write validate_failed (-5379) at byte 16384/1015200`
  diagnoses the failure in one line.

## AI usage collectors (Claude Code + Codex)

The ccusage repo (https://github.com/ryoppippi/ccusage) is the
reference implementation. Many of its tricks were missed by my
initial collector designs.

- **Claude Code's authoritative reset signal** is a line in the JSONL
  containing `Claude AI usage limit reached|<epoch>`. Parse it.
  Without this, dashd guesses the reset time from "first message in
  last 5h", which drifts.
- **Hour-floor block start** is ccusage's heuristic when no explicit
  reset event is available. Not documented by Anthropic but matches
  what they appear to use.
- **Deduplicate by (message_id, request_id).** Files get re-read
  on every collect tick; without dedup tokens get counted multiple
  times.
- **Claude Code path discovery** tries `$CLAUDE_CONFIG_DIR`
  (comma-separated, multi-path), `$XDG_CONFIG_HOME/claude`, then
  `~/.claude`. Not just the last one.
- **Codex JSONL events have `payload.type == "token_count"`** —
  NOT `payload.message.type` as the v0.1.2 collector assumed.
  Cumulative totals live at `payload.info.total_token_usage`
  with sub-fields `input_tokens + cached_input_tokens + output_tokens
  + reasoning_output_tokens`. The wrong path silently returned 0
  for every Codex session forever. Verify against real
  `~/.codex/sessions/.../rollout-*.jsonl` content before assuming
  the format.
- **Codex tokens come from cumulative-delta diffs.** Persist
  `{session_path → {last_total, file_mtime}}` to
  `~/.config/dashd/codex_state.json`. Subtract on each collect.
  Use the file path (NOT mtime-bucketed) as the session key —
  bucketing by mtime breaks long-running sessions when an append
  pushes mtime across an hour boundary, silently dropping deltas.
- **`setdefault` doesn't repair non-dict values.** If a corrupted
  state file has `"sessions": "string"`, `setdefault` leaves it
  alone. Always `isinstance(state["sessions"], dict)` before
  `.get()` access. The canonical `'str' object has no attribute 'get'`
  error means "I forgot to check the shape somewhere".
- **Cost stays `null` for Codex.** Anthropic / OpenAI don't publish
  Codex pricing publicly. Don't invent dollar figures.
- **Pricing comes from a vendored LiteLLM snapshot** at
  `agent/dashd/pricing/litellm-pricing.json` + daily background
  fetch into `~/.config/dashd/litellm-pricing.cache.json`. Replaces
  the hand-maintained `rates.py` that used to be in v0.1.2.
- **`block_used_pct` requires a configured budget.** Set
  `DASHD_CLAUDE_BLOCK_BUDGET` env var or
  `[collectors.claude_code] block_token_budget` in config.toml.
  Without it, only the time-elapsed metric is shown.

## Wire-protocol field semantics

The user-facing names matter:

- `block_pct` — kept as legacy alias of `block_elapsed_pct` so v0.1.2
  firmware reading the old field doesn't break. New firmware should
  read the explicit name.
- `block_elapsed_pct` — time-elapsed in the 5h window.
- `block_used_pct` — fraction of the configured token budget consumed.
  Null when no budget is set.
- For Codex, `block_used_pct == block_pct` because Codex reports
  actual quota usage, not time-elapsed. The naming is misleading
  but kept for symmetry with Claude.

## Type-safety patterns

The agent collectors are async and run on every tick — one
unhandled exception per collector tick floods the logs.

- `safe_collect()` catches everything but logs only the message at
  WARNING. **Always also emit `exc_info=True` at DEBUG level** so
  `dashd -v` surfaces the full traceback. The user can pull it on
  demand without modifying the collector.
- Defensively `isinstance(x, dict)` before `.get()` for any value
  read from an external file (JSONL, JSON state, config.toml).
- Defensively `_safe_rate(v)` for any value from pricing tables —
  NaN, inf, negative, string → 0. Poisoned values are the realistic
  failure mode when fetching JSON from an external source.

## React renderer (`ui/src/components/LiveView.tsx`)

- **Update the LiveView whenever the wire protocol gains new fields.**
  Easy miss: v0.1.3 added 11 new wire fields, but only the firmware
  page was updated. LiveView kept showing the old three. The user
  noticed.
- **electron-updater returns release notes as HTML** (from
  Releases.atom). Render with `dangerouslySetInnerHTML` in a styled
  container, NOT inside `<pre>` as text. The release body is the
  maintainer's own — `dangerouslySetInnerHTML` risk surface is bounded.

## Things I keep wanting to do that don't work

- `gh run watch` always backgrounds itself with our Bash tool
  setup. Don't try to foreground it — accept the notification when
  it pings.
- `git push` of a force-moved tag does retrigger CI on the new commit.
  No need to also `workflow_dispatch`.
- The .claude/ directory in this repo is project-local Claude config
  (not committed). Don't accidentally stage it; the gitignore doesn't
  cover it.
