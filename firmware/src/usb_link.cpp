#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <atomic>
#include <stdarg.h>
#include <mbedtls/base64.h>

#include "ble_transport.h"
#include "config.h"
#include "data_store.h"
#include "ota_link.h"
#include "pages.h"
#include "pet_widget.h"
#include "transport.h"
#include "usb_link.h"
#include "visibility.h"

int g_pending_show_page = -1;
int g_pending_show_page_id = -1;

// ---- Session ownership --------------------------------------------------
// The first transport to deliver a frame owns the session; a `hello` always
// (re)claims it. Frames from any other transport are ignored, and every
// device→host write is routed back to the owner. This is what stops USB
// and BLE from interleaving into g_store.
// std::atomic — written from apply_line (main loop) AND transport_release
// (called from the BLE onDisconnect callback, NimBLE task context).
static std::atomic<TransportId> s_owner{TRANSPORT_NONE};

TransportId transport_owner() { return s_owner.load(); }

void transport_release(TransportId who) {
  TransportId expected = who;
  s_owner.compare_exchange_strong(expected, TRANSPORT_NONE);
}

// Serialize a device→host JSON document and write it as one '\n'-terminated
// line to whichever transport owns the session.
static void transport_emit(JsonDocument &doc) {
  char buf[1536];   // device→host frames (events/acks/logs) are all small
  size_t n = serializeJson(doc, buf, sizeof(buf) - 2);
  buf[n] = 0;
#ifdef DASHD_ENABLE_BLE
  if (s_owner.load() == TRANSPORT_BLE) {
    g_ble.writeLine(buf);
    return;
  }
#endif
  Serial.print(buf);
  Serial.write('\n');
}

// One Preferences instance for the entire firmware; survives reboot.
static Preferences s_prefs;
static bool s_prefs_open = false;

static void ensure_prefs() {
  if (!s_prefs_open) {
    s_prefs.begin("dashd", false);  // namespace "dashd", read-write
    s_prefs_open = true;
  }
}

static void persist_theme() {
  ensure_prefs();
  s_prefs.putUShort("t_bg",     g_store.theme_bg);
  s_prefs.putUShort("t_fg",     g_store.theme_fg);
  s_prefs.putUShort("t_dim",    g_store.theme_dim);
  s_prefs.putUShort("t_good",   g_store.theme_good);
  s_prefs.putUShort("t_warn",   g_store.theme_warn);
  s_prefs.putUShort("t_crit",   g_store.theme_crit);
  s_prefs.putUShort("t_accent", g_store.theme_accent);
}

static void persist_thresholds() {
  ensure_prefs();
  s_prefs.putInt("thr_cpuw",  g_store.thr_cpu_warn);
  s_prefs.putInt("thr_cpuc",  g_store.thr_cpu_crit);
  s_prefs.putInt("thr_ramw",  g_store.thr_ram_warn);
  s_prefs.putInt("thr_ramc",  g_store.thr_ram_crit);
  s_prefs.putInt("thr_calm",  g_store.thr_calendar_soon_min);
  s_prefs.putInt("thr_cmtm",  g_store.thr_commit_fresh_min);
}

static void persist_pages_mask() {
  ensure_prefs();
  s_prefs.putUInt("pmask", g_store.pages_enabled_mask);
}

static void persist_backlight() {
  ensure_prefs();
  s_prefs.putInt("bl", g_store.backlight);
}

static void persist_layout() {
  ensure_prefs();
  s_prefs.putChar("ly_st", g_store.show_title);
  s_prefs.putChar("ly_sf", g_store.show_footer);
  s_prefs.putChar("ly_rt", g_store.rotation);
}

static void persist_scales() {
  ensure_prefs();
  s_prefs.putChar("sc_t",  g_store.scale_title);
  s_prefs.putChar("sc_l",  g_store.scale_label);
  s_prefs.putChar("sc_v",  g_store.scale_value);
  s_prefs.putChar("sc_b",  g_store.scale_big);
}

static void persist_pages_order() {
  ensure_prefs();
  s_prefs.putBytes("porder", g_store.pages_order, sizeof(g_store.pages_order));
}

static void persist_auto_advance() {
  ensure_prefs();
  s_prefs.putBool ("aa_en", g_store.auto_advance_enabled);
  s_prefs.putUShort("aa_iv", g_store.auto_advance_interval_s);
  s_prefs.putUChar("aa_md", g_store.auto_advance_mode);
}

void usb_link_restore_prefs() {
  ensure_prefs();
  g_store.theme_bg     = s_prefs.getUShort("t_bg",     0);
  g_store.theme_fg     = s_prefs.getUShort("t_fg",     0);
  g_store.theme_dim    = s_prefs.getUShort("t_dim",    0);
  g_store.theme_good   = s_prefs.getUShort("t_good",   0);
  g_store.theme_warn   = s_prefs.getUShort("t_warn",   0);
  g_store.theme_crit   = s_prefs.getUShort("t_crit",   0);
  g_store.theme_accent = s_prefs.getUShort("t_accent", 0);
  g_store.thr_cpu_warn = s_prefs.getInt("thr_cpuw", -1);
  g_store.thr_cpu_crit = s_prefs.getInt("thr_cpuc", -1);
  g_store.thr_ram_warn = s_prefs.getInt("thr_ramw", -1);
  g_store.thr_ram_crit = s_prefs.getInt("thr_ramc", -1);
  g_store.thr_calendar_soon_min = s_prefs.getInt("thr_calm", -1);
  g_store.thr_commit_fresh_min  = s_prefs.getInt("thr_cmtm", -1);
  g_store.pages_enabled_mask = s_prefs.getUInt("pmask", 0);
  g_store.backlight = s_prefs.getInt("bl", -1);
  g_store.show_title  = s_prefs.getChar("ly_st", -1);
  g_store.show_footer = s_prefs.getChar("ly_sf", -1);
  g_store.rotation    = s_prefs.getChar("ly_rt", -1);
  g_store.scale_title = s_prefs.getChar("sc_t", -1);
  g_store.scale_label = s_prefs.getChar("sc_l", -1);
  g_store.scale_value = s_prefs.getChar("sc_v", -1);
  g_store.scale_big   = s_prefs.getChar("sc_b", -1);
  if (s_prefs.getBytesLength("porder") == sizeof(g_store.pages_order)) {
    s_prefs.getBytes("porder", g_store.pages_order, sizeof(g_store.pages_order));
  }
  // Auto-advance settings. Defaults match the struct defaults (enabled,
  // 8 s, sequential) so upgrading users opt into cycling automatically.
  // The Electron Settings UI lets them disable it without losing the
  // interval / mode preference.
  g_store.auto_advance_enabled = s_prefs.getBool("aa_en", true);
  g_store.auto_advance_interval_s = s_prefs.getUShort("aa_iv", 8);
  g_store.auto_advance_mode = s_prefs.getUChar("aa_md", 0);
  // Belt and braces: clamp restored interval in case a future firmware
  // bug or an out-of-range cmd ever wrote a bad value to NVS.
  if (g_store.auto_advance_interval_s < 3)   g_store.auto_advance_interval_s = 3;
  if (g_store.auto_advance_interval_s > 300) g_store.auto_advance_interval_s = 300;
  if (g_store.auto_advance_mode > 1)         g_store.auto_advance_mode = 0;
}

static char s_line[USB_RX_LINE_MAX];
static size_t s_len = 0;

void usb_link_begin() {
  // Software RX buffer for native USB CDC. Default is 256 B; we bump it
  // so bursts of pet-install chunks don't overflow while LittleFS sector
  // writes briefly stall the loop.
  //
  // Pet install streams up to `window=8` chunks before waiting for an
  // ACK (see agent/dashd/pets/install.py). Each chunk is 2 KB raw +
  // base64 + JSON envelope ≈ 2.7 KB on the wire. 8 × 2.7 KB ≈ 22 KB —
  // an 8 KB buffer (the pre-v0.1.10 value) overflows mid-burst,
  // truncating a chunk line and producing `json parse: InvalidInput`
  // around byte 1300. ESP32-C3 has 320 KB SRAM so 32 KB is comfortable
  // and gives full window headroom + slack for display redraws / BLE
  // polling that briefly delay USB drain.
  Serial.setRxBufferSize(32768);
  Serial.begin(460800);
}

static void apply_top_procs(JsonArrayConst arr, DataStore::TopProc out[], int &count_out, int cap) {
  count_out = 0;
  if (arr.isNull()) return;
  for (JsonVariantConst v : arr) {
    if (count_out >= cap) break;
    JsonObjectConst o = v.as<JsonObjectConst>();
    DataStore::TopProc &p = out[count_out];
    strlcpy(p.name, o["name"] | "?", DataStore::NAME_LEN);
    p.cpu_pct = o["cpu_pct"] | 0.0f;
    p.ram_pct = o["ram_pct"] | 0.0f;
    p.ram_mb  = o["ram_mb"]  | 0;
    p.procs   = o["procs"]   | 0;
    count_out++;
  }
}

static void apply_suggestions(JsonArrayConst arr) {
  g_store.suggestions_count = 0;
  if (arr.isNull()) return;
  for (JsonVariantConst v : arr) {
    if (g_store.suggestions_count >= DataStore::SUG_N) break;
    JsonObjectConst o = v.as<JsonObjectConst>();
    DataStore::Suggestion &s = g_store.suggestions[g_store.suggestions_count];
    strlcpy(s.severity, o["severity"] | "info", sizeof(s.severity));
    strlcpy(s.text,     o["text"]     | "",     DataStore::SUG_LEN);
    g_store.suggestions_count++;
  }
}

static void apply_system(JsonObjectConst sys) {
  JsonArrayConst cpu = sys["cpu_pct"].as<JsonArrayConst>();
  int n = 0;
  for (JsonVariantConst v : cpu) {
    if (n >= (int)(sizeof(g_store.cpu_pct) / sizeof(g_store.cpu_pct[0]))) break;
    g_store.cpu_pct[n++] = v.as<int>();
  }
  g_store.cpu_count   = n;
  g_store.ram_pct          = sys["ram_pct"]          | -1;
  g_store.ram_pressure_pct = sys["ram_pressure_pct"] | -1;
  g_store.ram_used_gb      = sys["ram_used_gb"]      | -1.0f;
  g_store.ram_total_gb = sys["ram_total_gb"] | -1.0f;
  g_store.disk_pct    = sys["disk_pct"]     | -1;
  g_store.net_up_kbps   = sys["net_up_kbps"]   | -1;
  g_store.net_down_kbps = sys["net_down_kbps"] | -1;
  g_store.battery_pct = sys["battery_pct"]  | -1;

  JsonVariantConst bc = sys["battery_charging"];
  if (bc.isNull()) g_store.battery_charging = -1;
  else             g_store.battery_charging = bc.as<bool>() ? 1 : 0;

  JsonVariantConst t = sys["temp_cpu_c"];
  g_store.temp_cpu_c = t.isNull() ? NAN : t.as<float>();

  apply_top_procs(sys["top_cpu"].as<JsonArrayConst>(),
                  g_store.top_cpu, g_store.top_cpu_count, DataStore::TOP_N);
  apply_top_procs(sys["top_ram"].as<JsonArrayConst>(),
                  g_store.top_ram, g_store.top_ram_count, DataStore::TOP_N);
}

static void apply_claude_code(JsonObjectConst cc) {
  g_store.cc_tokens_today        = cc["tokens_today"]        | -1L;
  g_store.cc_cost_today_usd      = cc["cost_today_usd"]      | (float)NAN;
  // Prefer the new clearer field, fall back to legacy `block_pct` so the
  // firmware running an old agent payload still gets a sensible value.
  g_store.cc_block_elapsed_pct   = cc["block_elapsed_pct"]
                                   | (cc["block_pct"] | -1);
  g_store.cc_block_pct           = g_store.cc_block_elapsed_pct;
  g_store.cc_block_used_pct      = cc["block_used_pct"]      | -1;
  g_store.cc_block_resets_in_min = cc["block_resets_in_min"] | -1;
  g_store.cc_tokens_block        = cc["tokens_block"]        | -1L;
  JsonObjectConst m = cc["models"].as<JsonObjectConst>();
  g_store.cc_tokens_opus   = m["opus"]   | -1L;
  g_store.cc_tokens_sonnet = m["sonnet"] | -1L;
  g_store.cc_tokens_haiku  = m["haiku"]  | -1L;
  g_store.cc_burn_tokens_per_min    = cc["burn_tokens_per_min"]    | -1;
  g_store.cc_burn_projected_cap_min = cc["burn_projected_cap_min"] | -1;
  g_store.cc_tokens_this_week       = cc["tokens_this_week"]       | -1L;
  g_store.cc_cost_this_week_usd     = cc["cost_this_week_usd"]     | (float)NAN;
}

static void apply_codex(JsonObjectConst cx) {
  g_store.cx_tokens_today        = cx["tokens_today"]        | -1L;
  g_store.cx_block_elapsed_pct   = cx["block_elapsed_pct"]
                                    | (cx["block_pct"] | -1);
  g_store.cx_block_pct           = g_store.cx_block_elapsed_pct;
  g_store.cx_block_used_pct      = cx["block_used_pct"]      | -1;
  g_store.cx_block_resets_in_min = cx["block_resets_in_min"] | -1;
  JsonVariantConst sa = cx["session_active"];
  g_store.cx_session_active = sa.isNull() ? -1 : (sa.as<bool>() ? 1 : 0);
}

static void apply_github(JsonObjectConst gh) {
  g_store.gh_prs_awaiting_review  = gh["prs_awaiting_review"]  | -1;
  g_store.gh_ci_failures_24h      = gh["ci_failures_24h"]      | -1;
  g_store.gh_unread_notifications = gh["unread_notifications"] | -1;
}

static void apply_calendar(JsonObjectConst cal) {
  const char *t = cal["next_event_title"] | "";
  strlcpy(g_store.cal_next_event_title, t, sizeof(g_store.cal_next_event_title));
  g_store.cal_next_event_in_min = cal["next_event_in_min"] | -1;
  g_store.cal_today_remaining   = cal["today_remaining"]   | -1;
}

static void apply_messages(JsonObjectConst m) {
  g_store.msg_email_unread    = m["email"]["unread"]   | -1;
  // slack/teams not wired in Phase 3 but the slots stay so future phases just fill in.
  g_store.msg_slack_unread    = m["slack"]["unread"]   | -1;
  g_store.msg_slack_mentions  = m["slack"]["mentions"] | -1;
  g_store.msg_teams_unread    = m["teams"]["unread"]   | -1;
  g_store.msg_imessage_unread = m["imessage"]["unread"] | -1;
  g_store.msg_whatsapp_unread = m["whatsapp"]["unread"] | -1;
}

static void apply_git(JsonObjectConst g) {
  const char *br = g["branch"] | "";
  strlcpy(g_store.git_branch, br, sizeof(g_store.git_branch));
  g_store.git_commits_today              = g["commits_today"]             | -1;
  g_store.git_loc_added                  = g["loc_added"]                 | -1;
  g_store.git_loc_removed                = g["loc_removed"]               | -1;
  g_store.git_minutes_since_last_commit  = g["minutes_since_last_commit"] | -1;
}

static int page_id_by_name(const char *name) {
  if (!name) return -1;
  for (int i = 0; i < PAGE_COUNT; i++) {
    if (strcmp(kPages[i].name, name) == 0) return i;
  }
  return -1;
}

static void apply_cmd(JsonObjectConst c) {
  const char *name = c["name"] | "";
  if (strcmp(name, "show_page") == 0) {
    const char *p = c["page"] | "";
    int id = page_id_by_name(p);
    if (id >= 0) g_pending_show_page = id;
  } else if (strcmp(name, "set_brightness") == 0) {
    int v = c["value"] | -1;
    if (v >= 0 && v <= 100) {
      g_store.backlight = (v * 255) / 100;
      persist_backlight();
    }
  } else if (strcmp(name, "set_auto_advance") == 0) {
    // Atomic update of all three fields. Any field can be omitted —
    // the existing value is kept. Interval is clamped to [3, 300] s
    // here, defensively (UI clamps too but firmware is the source of
    // truth for bounds, in case a third-party IPC client sends junk).
    JsonVariantConst en = c["enabled"];
    if (!en.isNull()) g_store.auto_advance_enabled = en.as<bool>();
    JsonVariantConst iv = c["interval_s"];
    if (!iv.isNull()) {
      int v = iv.as<int>();
      if (v < 3) v = 3; else if (v > 300) v = 300;
      g_store.auto_advance_interval_s = (uint16_t)v;
    }
    JsonVariantConst md = c["mode"];
    if (!md.isNull()) {
      // Accept both string ("sequential"/"random") and integer (0/1).
      // Strings are friendlier on the wire; ints survive future JSON
      // tooling that loses field types.
      if (md.is<const char *>()) {
        const char *s = md.as<const char *>();
        g_store.auto_advance_mode = (s && strcmp(s, "random") == 0) ? 1 : 0;
      } else if (md.is<int>()) {
        int v = md.as<int>();
        g_store.auto_advance_mode = (v == 1) ? 1 : 0;
      }
    }
    persist_auto_advance();
    // Signal main loop that the bag / timer should reset on next tick
    // — interval changes shouldn't compound, and a mode switch needs
    // a fresh shuffle (extern in data_store.h).
    g_auto_advance_dirty = 1;
  } else if (strcmp(name, "set_theme") == 0) {
    JsonObjectConst t = c["colors"].as<JsonObjectConst>();
    if (!t.isNull()) {
      g_store.theme_bg     = t["bg"]     | g_store.theme_bg;
      g_store.theme_fg     = t["fg"]     | g_store.theme_fg;
      g_store.theme_dim    = t["dim"]    | g_store.theme_dim;
      g_store.theme_good   = t["good"]   | g_store.theme_good;
      g_store.theme_warn   = t["warn"]   | g_store.theme_warn;
      g_store.theme_crit   = t["crit"]   | g_store.theme_crit;
      g_store.theme_accent = t["accent"] | g_store.theme_accent;
      persist_theme();
    }
  } else if (strcmp(name, "set_thresholds") == 0) {
    JsonObjectConst t = c["thresholds"].as<JsonObjectConst>();
    if (!t.isNull()) {
      g_store.thr_cpu_warn = t["cpu_warn"] | g_store.thr_cpu_warn;
      g_store.thr_cpu_crit = t["cpu_crit"] | g_store.thr_cpu_crit;
      g_store.thr_ram_warn = t["ram_warn"] | g_store.thr_ram_warn;
      g_store.thr_ram_crit = t["ram_crit"] | g_store.thr_ram_crit;
      g_store.thr_calendar_soon_min = t["calendar_soon_min"] | g_store.thr_calendar_soon_min;
      g_store.thr_commit_fresh_min  = t["commit_fresh_min"]  | g_store.thr_commit_fresh_min;
      persist_thresholds();
    }
  } else if (strcmp(name, "set_pages_enabled") == 0) {
    uint32_t mask = c["mask"] | g_store.pages_enabled_mask;
    g_store.pages_enabled_mask = mask;
    persist_pages_mask();
    // Auto-advance's random shuffle bag is keyed off which slots are
    // enabled — flag dirty so we rebuild it on the next tick. Without
    // this the bag can still hand out a slot the user just disabled.
    g_auto_advance_dirty = 1;
  } else if (strcmp(name, "set_pages_order") == 0) {
    JsonArrayConst arr = c["order"].as<JsonArrayConst>();
    if (!arr.isNull()) {
      int n = 0;
      int8_t buf[8] = {-1,-1,-1,-1,-1,-1,-1,-1};
      for (JsonVariantConst v : arr) {
        if (n >= 8) break;
        int id = v.as<int>();
        if (id < 0 || id >= PAGE_COUNT) continue;
        buf[n++] = (int8_t)id;
      }
      // Empty array (or all-invalid) = reset to default order.
      memcpy(g_store.pages_order, buf, sizeof(buf));
      persist_pages_order();
      g_auto_advance_dirty = 1;   // see set_pages_enabled comment
    }
  } else if (strcmp(name, "set_layout") == 0) {
    if (c["show_title"].is<bool>())  g_store.show_title  = c["show_title"].as<bool>()  ? 1 : 0;
    if (c["show_footer"].is<bool>()) g_store.show_footer = c["show_footer"].as<bool>() ? 1 : 0;
    if (c["rotation"].is<int>()) {
      int r = c["rotation"].as<int>();
      if (r >= 0 && r <= 3) g_store.rotation = (int8_t)r;
    }
    persist_layout();
  } else if (strcmp(name, "set_text_scales") == 0) {
    JsonObjectConst t = c["scales"].as<JsonObjectConst>();
    if (!t.isNull()) {
      auto clamp = [](int v) -> int8_t {
        if (v < 1) v = 1; if (v > 4) v = 4; return (int8_t)v;
      };
      if (t["title"].is<int>()) g_store.scale_title = clamp(t["title"].as<int>());
      if (t["label"].is<int>()) g_store.scale_label = clamp(t["label"].as<int>());
      if (t["value"].is<int>()) g_store.scale_value = clamp(t["value"].as<int>());
      if (t["big"].is<int>())   g_store.scale_big   = clamp(t["big"].as<int>());
      persist_scales();
    }
  } else if (strcmp(name, "pet_set_state") == 0) {
    const char *st = c["state"] | "";
    if (st[0]) pet_set_state(st);
  } else if (strcmp(name, "pet_set_enabled") == 0) {
    pet_set_enabled(c["enabled"] | true);
  } else if (strcmp(name, "pet_set_corner") == 0) {
    int v = c["corner"] | 1;
    if (v < 0) v = 0; if (v > 3) v = 3;
    pet_set_corner((PetCorner)v);
  } else if (strcmp(name, "pet_set_active") == 0) {
    const char *slug = c["slug"] | "default";
    bool ok = pet_set_active(slug);
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "pet_activated";
    doc2["slug"] = pet_active_slug(); doc2["ok"] = ok;
    transport_emit(doc2);
  } else if (strcmp(name, "pet_remove") == 0) {
    const char *slug = c["slug"] | "";
    pet_remove(slug);
  } else if (strcmp(name, "pet_install_start") == 0) {
    const char *slug = c["slug"] | "";
    size_t total = (size_t)(c["size"] | 0);
    bool ok = pet_install_start(slug, total);
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "pet_install_started";
    doc2["slug"] = slug; doc2["ok"] = ok;
    transport_emit(doc2);
  } else if (strcmp(name, "pet_install_chunk") == 0) {
    uint32_t seq = c["seq"] | 0;
    const char *b64 = c["data"] | "";
    static uint8_t scratch[3072];
    size_t out_len = 0;
    int rc = mbedtls_base64_decode(scratch, sizeof(scratch), &out_len,
                                   (const unsigned char *)b64, strlen(b64));
    bool ok = (rc == 0) && pet_install_chunk(seq, scratch, out_len);
    // ACK every chunk so the agent can wait for completion before sending
    // the next one — that's the real flow control.
    JsonDocument ack;
    ack["type"] = "event"; ack["name"] = "pet_install_chunk_ack";
    ack["seq"] = seq; ack["ok"] = ok;
    transport_emit(ack);
    if (!ok) LOGW("pet_install_chunk seq=%u failed (rc=%d, len=%u)",
                  (unsigned)seq, rc, (unsigned)out_len);
  } else if (strcmp(name, "pet_install_end") == 0) {
    bool ok = pet_install_end();
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "pet_install_ended";
    doc2["ok"] = ok; doc2["slug"] = pet_active_slug();
    transport_emit(doc2);

  // ── Firmware OTA ─────────────────────────────────────────────────────
  // Same chunked-with-ACK pattern as pet installs. The agent has already
  // downloaded the .bin from GitHub Releases and verified its SHA — we
  // just stream bytes here and re-verify after the last chunk lands.
  } else if (strcmp(name, "fw_update_begin") == 0) {
    size_t total = (size_t)(c["size"] | 0);
    const char *sha = c["sha256"] | "";
    const char *ver = c["version"] | "";
    bool ok = ota_begin(total, sha, ver);
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "fw_update_started";
    doc2["ok"] = ok; doc2["version"] = ver;
    if (!ok) doc2["error"] = ota_last_error();
    transport_emit(doc2);
    if (ok) LOGI("fw_update_begin v=%s size=%u", ver, (unsigned)total);
  } else if (strcmp(name, "fw_update_chunk") == 0) {
    uint32_t seq = c["seq"] | 0;
    const char *b64 = c["data"] | "";
    static uint8_t scratch[3072];
    size_t out_len = 0;
    int rc = mbedtls_base64_decode(scratch, sizeof(scratch), &out_len,
                                   (const unsigned char *)b64, strlen(b64));
    bool ok = (rc == 0) && ota_write_chunk(scratch, out_len);
    JsonDocument ack;
    ack["type"] = "event"; ack["name"] = "fw_update_chunk_ack";
    ack["seq"] = seq; ack["ok"] = ok;
    if (!ok) ack["error"] = ota_last_error();
    transport_emit(ack);
    // Throttle progress events to every ~16 KB so we don't drown the link
    // (chunks are ~2 KB, would emit every chunk otherwise). Always emit
    // the final progress when we've reached total so the host sees 100%.
    static size_t s_last_progress_bytes = 0;
    size_t now_bytes = ota_bytes_received();
    if (now_bytes - s_last_progress_bytes >= 16 * 1024 || now_bytes == ota_total_size()) {
      JsonDocument prog;
      prog["type"] = "event"; prog["name"] = "fw_update_progress";
      prog["bytes"] = (uint32_t)now_bytes;
      prog["total"] = (uint32_t)ota_total_size();
      transport_emit(prog);
      s_last_progress_bytes = now_bytes;
    }
    if (!ok) LOGW("fw_update_chunk seq=%u failed: %s",
                  (unsigned)seq, ota_last_error());
  } else if (strcmp(name, "fw_update_end") == 0) {
    // ota_end() reboots on success — anything past that is the failure path.
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "fw_update_done";
    if (ota_end()) {
      // Unreachable: ota_end() called esp_restart(). Defensive emit anyway.
      doc2["ok"] = true; doc2["version"] = ota_target_version();
    } else {
      doc2["ok"] = false;
      doc2["error"] = ota_last_error();
    }
    transport_emit(doc2);
  } else if (strcmp(name, "fw_update_abort") == 0) {
    ota_abort("host-cancelled");
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "fw_update_done";
    doc2["ok"] = false; doc2["error"] = ota_last_error();
    transport_emit(doc2);

  } else if (strcmp(name, "set_element_visible") == 0) {
    const char *id = c["id"] | "";
    bool vis = c["visible"] | true;
    if (id[0]) visibility::set_hidden(id, !vis);
  } else if (strcmp(name, "reset_visibility") == 0) {
    visibility::clear_all();
  } else if (strcmp(name, "get_visibility") == 0) {
    // Echo the current hidden-hash list back so the UI can sync.
    JsonDocument doc2;
    doc2["type"] = "event"; doc2["name"] = "visibility_state";
    JsonArray arr = doc2["hidden_hashes"].to<JsonArray>();
    for (size_t i = 0; i < visibility::hidden_count(); i++) {
      arr.add((uint32_t)visibility::hidden_at(i));
    }
    transport_emit(doc2);
  } else if (strcmp(name, "hello") == 0) {
    // Host handshake. Replying with hello_ack lets the agent learn the
    // firmware + protocol version. (Phase 3 also makes this the point
    // where the device binds the session to the sending transport.)
    usb_send_event("hello_ack");
  } else if (strcmp(name, "reset_prefs") == 0) {
    ensure_prefs();
    s_prefs.clear();
    visibility::clear_all();
    g_store.theme_bg = g_store.theme_fg = g_store.theme_dim = 0;
    g_store.theme_good = g_store.theme_warn = g_store.theme_crit = g_store.theme_accent = 0;
    g_store.thr_cpu_warn = g_store.thr_cpu_crit = -1;
    g_store.thr_ram_warn = g_store.thr_ram_crit = -1;
    g_store.thr_calendar_soon_min = g_store.thr_commit_fresh_min = -1;
    g_store.pages_enabled_mask = 0;
    g_store.backlight = -1;
    g_store.show_title = g_store.show_footer = g_store.rotation = -1;
    g_store.scale_title = g_store.scale_label = g_store.scale_value = g_store.scale_big = -1;
    for (size_t i = 0; i < sizeof(g_store.pages_order); i++) g_store.pages_order[i] = -1;
  }
}

static void apply_line(const char *line, size_t len, TransportId src) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line, len);
  if (err) {
    LOGW("json parse: %s (len=%u)", err.c_str(), (unsigned)len);
    return;
  }
  const char *type = doc["type"] | "";

  // ---- Session-ownership gate ----
  // A `hello` always (re)claims the session for the sending transport.
  // Any other frame is processed only if it comes from the current owner
  // (or claims an unowned session). Frames from a non-owner are dropped —
  // this is what stops USB and BLE interleaving into g_store.
  bool is_hello = (strcmp(type, "cmd") == 0 &&
                   strcmp(doc["name"] | "", "hello") == 0);
  if (is_hello) {
    s_owner.store(src);
  } else if (s_owner.load() == TRANSPORT_NONE) {
    s_owner.store(src);
  } else if (s_owner.load() != src) {
    return;  // not the session owner — ignore
  }

  if (strcmp(type, "cmd") == 0) { apply_cmd(doc.as<JsonObjectConst>()); return; }
  if (strcmp(type, "state") != 0) return;
  g_store.last_state_ms = millis();
  g_store.ever_received = true;
  JsonVariantConst sys = doc["system"];
  if (!sys.isNull() && sys.is<JsonObjectConst>()) {
    apply_system(sys.as<JsonObjectConst>());
  }
  JsonVariantConst ai = doc["ai"];
  if (!ai.isNull() && ai.is<JsonObjectConst>()) {
    JsonVariantConst cc = ai["claude_code"];
    if (!cc.isNull() && cc.is<JsonObjectConst>()) apply_claude_code(cc.as<JsonObjectConst>());
    JsonVariantConst cx = ai["codex"];
    if (!cx.isNull() && cx.is<JsonObjectConst>()) apply_codex(cx.as<JsonObjectConst>());
  }
  JsonVariantConst gt = doc["git"];
  if (!gt.isNull() && gt.is<JsonObjectConst>()) {
    apply_git(gt.as<JsonObjectConst>());
  }
  JsonVariantConst gh = doc["github"];
  if (!gh.isNull() && gh.is<JsonObjectConst>()) apply_github(gh.as<JsonObjectConst>());
  JsonVariantConst cal = doc["calendar"];
  if (!cal.isNull() && cal.is<JsonObjectConst>()) apply_calendar(cal.as<JsonObjectConst>());
  JsonVariantConst msgs = doc["messages"];
  if (!msgs.isNull() && msgs.is<JsonObjectConst>()) apply_messages(msgs.as<JsonObjectConst>());
  JsonVariantConst sug = doc["suggestions"];
  if (!sug.isNull()) apply_suggestions(sug.as<JsonArrayConst>());
}

void dashd_apply_line(const char *line, size_t len, TransportId src) {
  // Shared entry point so a BLE transport can feed the same parser the
  // USB path uses. Caller guarantees main-loop context. `src` drives the
  // ownership gate inside apply_line.
  apply_line(line, len, src);
}

bool usb_link_poll() {
  bool applied = false;
  while (Serial.available()) {
    int b = Serial.read();
    if (b < 0) break;
    if (b == '\n') {
      if (s_len > 0) {
        s_line[s_len] = 0;
        apply_line(s_line, s_len, TRANSPORT_USB);
        applied = true;
      }
      s_len = 0;
    } else if (b == '\r') {
      // ignore
    } else if (s_len < USB_RX_LINE_MAX - 1) {
      s_line[s_len++] = (char)b;
    } else {
      // Line too long; drop it and resync at next newline.
      s_len = 0;
    }
  }
  return applied;
}

void usb_send_event(const char *name) {
  JsonDocument doc;
  doc["type"] = "event";
  doc["name"] = name;
  // boot + hello_ack carry the firmware identity so the host can negotiate.
  if (strcmp(name, "boot") == 0 || strcmp(name, "hello_ack") == 0) {
    doc["fw_version"] = DASHD_FW_VERSION;
    doc["v"] = DASHD_PROTOCOL_VERSION;
  }
  transport_emit(doc);
}

void usb_send_event_page(const char *name, const char *page) {
  JsonDocument doc;
  doc["type"] = "event";
  doc["name"] = name;
  doc["page"] = page;
  transport_emit(doc);
}

void usb_log(const char *level, const char *fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  JsonDocument doc;
  doc["type"]  = "event";
  doc["name"]  = "log";
  doc["level"] = level;
  doc["msg"]   = buf;
  transport_emit(doc);
}
