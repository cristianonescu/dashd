# Collectors

A reference for what each collector reads, when it returns `null`, and where to look if it misbehaves.

## SystemCollector — `system.*`

- Source: `psutil`.
- Returns `null` for `battery_*` if the machine has no battery (`psutil.sensors_battery()` → `None`).
- Returns `null` for `temp_cpu_c` on macOS — `psutil.sensors_temperatures()` is a no-op there. Linux usually works.
- Net throughput is computed as Δ-bytes since the previous tick (so the first reading shows whatever happened in the last 2 s, not absolute totals).
- **Top processes**: `top_cpu` and `top_ram` each carry the top 3 processes by CPU% / RAM% respectively. Per-process CPU is measured by priming `Process.cpu_percent()` on the collector's first call and reading the delta on each subsequent call. Names truncated to 14 chars. A short ignore list (`kernel_task`, `WindowServer`, `loginwindow`, `launchd`, …) drops noise.
- **XPC attribution**: an anonymous `com.apple.Virtualization.VirtualMachine` (the XPC service backing apps like Claude Desktop, Docker, OrbStack) is rewritten to `VM (<app>)` based on which known VM-using app is currently running. Falls back to `Apple VM` if we can't identify a driver. Known apps: Docker, OrbStack, Claude, UTM, Tart, VMware, Parallels, Lima, Colima — add more in `_VM_DRIVERS` in `system.py`.

## Suggestions engine — `suggestions[]`

- Source: `dashd/suggestions.py` — a stateless rule list that consumes the just-aggregated payload and emits up to 5 ranked suggestions per tick.
- Each rule returns `{severity: "crit"|"warn"|"info", text: "..."}` or `None`.
- The engine sorts by severity (`crit` first), trims to top-N (5 by default), and adds the array under the top-level `suggestions` key.
- Built-in rules:
  - RAM ≥ 92 % → **crit**, names the worst RAM hog
  - RAM ≥ 80 % → **warn**
  - CPU avg ≥ 80 % → **crit**, names the worst CPU hog
  - CPU avg ≥ 60 % → **warn**
  - CPU temp ≥ 90/80 °C → **crit/warn**
  - Battery ≤ 10/20 % when unplugged → **crit/warn**
  - Disk ≥ 95/85 % → **crit/warn**
  - Claude / Codex 5h block ≥ 90/75 % → **crit/warn**
  - Calendar event in ≤ 2 / ≤ 5 min → **crit/warn**
  - Git: no commit in ≥ 3 h → **warn**
  - GitHub: ≥ 3 CI failures → **crit**, ≥ 5 PRs → **warn**, ≥ 1 PR → **info**
- Adding a rule is one function in [agent/dashd/suggestions.py](../agent/dashd/suggestions.py) added to the `RULES` list — no wire-protocol change needed.

## ClaudeCodeCollector — `ai.claude_code.*`

- **Source paths**: tried in order, first match wins. dashd reads from every `projects/*.jsonl` it finds across all matching roots, with `(message_id, request_id)` dedup so a file scanned twice doesn't double-count.
  1. `$CLAUDE_CONFIG_DIR` (comma-separated, takes precedence)
  2. `$XDG_CONFIG_HOME/claude` (defaults to `~/.config/claude`)
  3. `~/.claude` (legacy default)
- **Tokens & cost today**: sums `input + output + cache_creation + cache_read` tokens across all assistant messages with timestamp ≥ local midnight. Cost is computed from a vendored snapshot of [LiteLLM's pricing catalog](https://github.com/BerriAI/litellm) (see [agent/dashd/pricing/](../agent/dashd/pricing/)). The pricing module supports Anthropic's tiered above-200k rates and explicit `cache_creation_input_token_cost` / `cache_read_input_token_cost`. The agent refreshes the snapshot daily from LiteLLM's `raw.githubusercontent.com` URL via httpx; failures fall back to the bundled snapshot silently. Per-model overrides live under `[collectors.claude_code.rates]` in config.
- **5h block**: rolling rate-limit window. Block start is the **explicit reset epoch** parsed from `"Claude AI usage limit reached|<epoch>"` events in the JSONL when present; otherwise it falls back to ccusage's heuristic of flooring the earliest in-window assistant timestamp to the hour boundary. The wire surfaces:
  - `block_elapsed_pct` — `elapsed * 100 / 5h`, clamped 0–100. Legacy `block_pct` is preserved as an alias for back-compat with v0.1.2 firmware.
  - `block_used_pct` — `tokens_block * 100 / budget` when a per-block budget is configured (see below); null otherwise.
  - `block_resets_in_min`, `block_resets_at` (epoch).
  - `tokens_block`, `cost_block_usd` — totals for the current window only.
- **Per-block budget** (optional): set `DASHD_CLAUDE_BLOCK_BUDGET=<tokens>` env var, or `[collectors.claude_code] block_token_budget = <tokens>` in `config.toml`, to unlock `block_used_pct` and the burn-rate projection. Without a budget, only the time-elapsed metric (`block_elapsed_pct`) is shown.
- **Burn rate**: `burn_tokens_per_min` is `tokens_block / max(1, elapsed_min)`. With a budget set, `burn_projected_cap_min = remaining_budget / burn_rate` — the agent's "hits cap in N min" projection.
- **Last 7 days**: `tokens_this_week`, `cost_this_week_usd`.
- **`models`**: tokens grouped to short labels (`opus`, `sonnet`, `haiku`, `other`).
- **`top_projects[]`**: top 3 codebases by today's tokens. The slug under `projects/` is unescaped from Claude Code's `-`-separated layout so it reads like a normal path tail (e.g. `Users/foo/dashd`).
- Files older than `min(midnight, block_window_start, week_start)` are skipped by mtime to keep the scan cheap.

## CodexCollector — `ai.codex.*`

- **Source**: every `~/.codex/sessions/**/rollout-*.jsonl` file modified in the last 24h is scanned (not just the most recently modified — dashd needs cumulative totals from every active session).
- **State persistence**: dashd keeps `~/.config/dashd/codex_state.json` (`{session_path → {last_total, file_mtime}}` + `tokens_today` + `day`). On every collect, the delta between the current session's `total_token_usage` and the persisted `last_total` is added to `tokens_today`. State survives agent restarts, re-baselines on session truncation, and prunes entries untouched for 3 days.
- **Token derivation**: the Codex JSONL emits `event_msg` events of type `token_count` with `payload.info.total_token_usage` (cumulative across the session, the trick borrowed from [ccusage](https://github.com/ryoppippi/ccusage)). The four sub-fields summed: `input_tokens + cached_input_tokens + output_tokens + reasoning_output_tokens`.
- **Block info**: `rate_limits.primary` from the freshest session carries `used_percent` (surfaced as both `block_pct` legacy alias and `block_used_pct`) and `resets_at` (epoch → `block_resets_in_min` + `block_resets_at`). Codex reports actual quota usage, not time-elapsed — so `block_used_pct` for Codex is meaningful even without a configured budget.
- **`cost_today_usd` is always `null`** — Anthropic / OpenAI don't publish Codex pricing publicly, so dashd refuses to invent dollar figures.
- `session_active` is true when the latest rollout file was modified within the last 5 minutes.

## GitCollector — `git.*`

- Source: subprocesses against each configured repo (`[collectors.git] repos = [...]`).
- `branch`: from the first valid repo, via `git symbolic-ref --short HEAD` (works on unborn branches; falls back to `@<sha>` if detached).
- `commits_today`, `loc_added`, `loc_removed`: `git log --since=midnight --author=<user.email> --numstat` summed across all configured repos. The email comes from `[user] email` — set it correctly or you'll see zeros.
- `minutes_since_last_commit`: time since the freshest `%ct` across all repos by **any** author — a "have I shipped lately" focus indicator, not a personal-only metric.
- Each git invocation has a 5 s timeout. If git isn't on PATH, the collector returns `null` cleanly.

## GitHubCollector — `github.*`

- PAT-authenticated. Set `GITHUB_TOKEN` env var, or `[collectors.github] token = "..."` (less safe). Required scopes: `repo` + `notifications`.
- **`prs_awaiting_review`**: Search API, `is:pr is:open review-requested:@me archived:false`. Counts all repos you have access to.
- **`ci_failures_24h`**: best-effort proxy — open PRs you authored in the last 24h that the Search API tags as failing. The Search API doesn't index workflow runs directly; for a fully accurate count we'd need to walk `/repos/{owner}/{repo}/actions/runs` per watched repo. Good enough for an at-a-glance dashboard.
- **`unread_notifications`**: `/notifications?all=false&per_page=50`. Caps at 50 — if you have more, expect 50 displayed.
- 10 s timeout per call. Each sub-query independently substitutes `-1` on failure so one rate-limited endpoint doesn't blank the whole panel.

## CalendarCollector — `calendar.*` (Microsoft Graph)

- MSAL **device-code flow** — no client secret, no redirect URL. First run prints a `https://microsoft.com/devicelogin` URL + code; sign in once and you're set.
- Token cache: `~/.config/dashd/msgraph_token.json` (mode 0600). Delete it to force a re-auth.
- Reads `/me/calendarView` for the next 24 hours, sorted by start time, asks Graph to return all times in UTC. Skips all-day events and `showAs == free`.
- Surfaces the next non-free event's title and minutes until start; `today_remaining` counts non-free events still on your local-time today.
- See [docs/microsoft-graph-setup.md](microsoft-graph-setup.md) for the Azure app registration.

## EmailCollector — `messages.email`

- IMAP over SSL via `aioimaplib`. Configure `[collectors.email]` `host`, `port` (default 993), `username`, `mailbox` (default `INBOX`).
- Password from `DASHD_EMAIL_PASSWORD` env var (never config.toml). For Gmail use an **App Password** (your normal password won't work with IMAP).
- One fresh connection per tick: LOGIN → SELECT → SEARCH UNSEEN → LOGOUT. Simple, no IDLE/keepalive bookkeeping.
- Returns `{"unread": N}` (`-1` on failure).

## IMessageCollector — `messages.imessage` (macOS only)

- Source: read-only SQLite open of `~/Library/Messages/chat.db` (`mode=ro&immutable=1` so we never lock anything Messages.app cares about).
- **Requires Full Disk Access** for the terminal / IDE running the agent. Without it, sqlite raises `unable to open database file`. The collector detects this, logs **once** at WARN level pointing the user at System Settings → Privacy & Security → Full Disk Access, then keeps quiet — subsequent calls return `{"unread": -1}` silently.
- Query: `SELECT COUNT(*) FROM message WHERE is_read = 0 AND is_from_me = 0` — matches the Messages.app badge (all-time, all conversations).
- Non-macOS: returns `None` (platform-gated, no error).

## WhatsAppCollector — `messages.whatsapp` (best-effort, currently `null`)

Slot exists, always returns `None`. There is no clean way to read WhatsApp's unread count from outside the app — every approach (Web automation, undocumented SQLite, NotificationCenter scraping) is either ToS-violating or unreliable. Detailed reasoning in [whatsapp.md](whatsapp.md).

## Skipped (intentionally)

- `SlackCollector` (`messages.slack`)
- `TeamsCollector` (`messages.teams`)

Stub files return `None`. The Messages page renders those cells as `--`. Re-enable when you want them.

## How failures propagate

A collector's `collect()` may raise; the aggregator wraps it in `safe_collect()`, logs the exception, and substitutes `None` for that one slot. Other collectors and the rest of the pipeline are unaffected. Tests for this live in [agent/tests/test_aggregator.py](../agent/tests/test_aggregator.py).
