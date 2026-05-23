#pragma once
#include <Arduino.h>

// Most-recent parsed state from the host. Pages read fields they care about
// and treat sentinel values as "n/a" — never crash on absence.
//
// Sentinels: ints use -1, floats use NAN, strings use empty.

struct DataStore {
  // Liveness.
  uint32_t last_state_ms = 0;
  bool ever_received = false;

  // Theme overrides (RGB565). 0 means "use compiled-in default".
  uint16_t theme_bg     = 0;
  uint16_t theme_fg     = 0;
  uint16_t theme_dim    = 0;
  uint16_t theme_good   = 0;
  uint16_t theme_warn   = 0;
  uint16_t theme_crit   = 0;
  uint16_t theme_accent = 0;

  // Threshold overrides. -1 means "use compiled-in default".
  int thr_cpu_warn  = -1;
  int thr_cpu_crit  = -1;
  int thr_ram_warn  = -1;
  int thr_ram_crit  = -1;
  int thr_calendar_soon_min = -1;
  int thr_commit_fresh_min  = -1;

  // Backlight level (0..255). -1 means full.
  int backlight = -1;

  // Pages-enabled bitmask. Bit i = page id i enabled. 0 means "all enabled".
  uint32_t pages_enabled_mask = 0;

  // Display order — pages_order[slot] = page id at that slot. -1 = default
  // (use the canonical order in kPages).
  int8_t pages_order[8] = {-1, -1, -1, -1, -1, -1, -1, -1};

  // Text scale multipliers per semantic role. -1 = default (1). Clamped 1..4
  // when applied so a runaway value can't overflow the screen.
  int8_t scale_title = -1;
  int8_t scale_label = -1;
  int8_t scale_value = -1;
  int8_t scale_big   = -1;

  // Title bar + footer visibility. -1 = default (visible).
  int8_t show_title  = -1;
  int8_t show_footer = -1;

  // Screen rotation (0..3). -1 = default (0 = portrait).
  int8_t rotation = -1;

  // Auto-advance: device cycles through enabled pages on a timer with
  // no button press needed.
  //   auto_advance_enabled  — default true on first boot (NVS key
  //                           absent ⇒ true); user-toggleable from
  //                           Electron Settings.
  //   auto_advance_interval_s — seconds between cycles, clamped to
  //                           [3, 300] by the firmware. Default 8.
  //   auto_advance_mode     — 0 = sequential (next_enabled_slot order),
  //                           1 = random (Fisher-Yates shuffle bag
  //                           over enabled slots; refilled when drained).
  // The timer resets to "now" on any user interaction (button press,
  // host show_page command) so a manual gesture doesn't get followed
  // by an immediate auto-advance.
  bool    auto_advance_enabled  = true;
  uint16_t auto_advance_interval_s = 8;
  uint8_t auto_advance_mode     = 0;

  // system.*
  int  cpu_pct[8]  = {0};
  int  cpu_count   = 0;
  int  ram_pct     = -1;
  int  ram_pressure_pct = -1;   // active+wired only; cache excluded
  float ram_used_gb  = -1.0f;
  float ram_total_gb = -1.0f;
  int  disk_pct    = -1;
  int  net_up_kbps   = -1;
  int  net_down_kbps = -1;
  int  battery_pct = -1;
  int  battery_charging = -1;     // -1 unknown, 0 false, 1 true
  float temp_cpu_c = NAN;

  // ai.claude_code.*
  long  cc_tokens_today   = -1;
  float cc_cost_today_usd = NAN;
  // Time-based block progress (0–100, ~ elapsed/5h). Legacy `cc_block_pct`
  // duplicates `cc_block_elapsed_pct` for back-compat with v0.1.2.
  int   cc_block_pct           = -1;
  int   cc_block_elapsed_pct   = -1;
  // Token-based block progress (0–100, tokens used / configured budget).
  // -1 when no per-block budget is set.
  int   cc_block_used_pct      = -1;
  int   cc_block_resets_in_min = -1;
  long  cc_tokens_block        = -1;   // tokens consumed in current 5h window
  long  cc_tokens_opus         = -1;
  long  cc_tokens_sonnet       = -1;
  long  cc_tokens_haiku        = -1;
  // Burn rate / projection — both derived from this-block activity.
  int   cc_burn_tokens_per_min      = -1;
  int   cc_burn_projected_cap_min   = -1;
  // Last 7 days.
  long  cc_tokens_this_week    = -1;
  float cc_cost_this_week_usd  = NAN;

  // ai.codex.*
  // Codex reports actual usage %, not time-elapsed — so `cx_block_pct`
  // (legacy) and `cx_block_used_pct` are the same value here.
  long  cx_tokens_today          = -1;
  int   cx_block_pct             = -1;
  int   cx_block_elapsed_pct     = -1;
  int   cx_block_used_pct        = -1;
  int   cx_block_resets_in_min   = -1;
  int   cx_session_active        = -1;   // -1 unknown, 0 false, 1 true

  // git.*
  char  git_branch[40] = {0};
  int   git_commits_today = -1;
  int   git_loc_added   = -1;
  int   git_loc_removed = -1;
  int   git_minutes_since_last_commit = -1;

  // github.*
  int gh_prs_awaiting_review = -1;
  int gh_ci_failures_24h     = -1;
  int gh_unread_notifications = -1;

  // calendar.*
  char cal_next_event_title[48] = {0};
  int  cal_next_event_in_min = -1;
  int  cal_today_remaining   = -1;

  // messages.*
  int msg_email_unread    = -1;
  int msg_slack_unread    = -1;
  int msg_slack_mentions  = -1;
  int msg_teams_unread    = -1;
  int msg_imessage_unread = -1;
  int msg_whatsapp_unread = -1;

  // ----- Top processes + suggestions (refreshed every tick, not persisted) -----
  static constexpr int TOP_N = 3;
  static constexpr int SUG_N = 5;
  static constexpr int NAME_LEN = 16;
  static constexpr int SUG_LEN = 64;

  struct TopProc {
    char  name[NAME_LEN] = {0};
    float cpu_pct = 0;
    float ram_pct = 0;
    int   ram_mb  = 0;
    int   procs   = 0;   // >1 means this row sums multiple helper processes
  };
  TopProc top_cpu[TOP_N];
  TopProc top_ram[TOP_N];
  int top_cpu_count = 0;
  int top_ram_count = 0;

  struct Suggestion {
    char severity[8] = {0};       // "crit" | "warn" | "info"
    char text[SUG_LEN] = {0};
  };
  Suggestion suggestions[SUG_N];
  int suggestions_count = 0;

  bool host_alive(uint32_t now_ms, uint32_t stale_ms) const {
    return ever_received && (now_ms - last_state_ms) < stale_ms;
  }
};

extern DataStore g_store;

// Set by any code that mutates `g_store.auto_advance_*` so the main
// loop's page-cycling timer resets and the random shuffle bag is
// rebuilt on the next tick. Cleared by the consumer in main.cpp.
// Defined in main.cpp.
extern uint8_t g_auto_advance_dirty;
