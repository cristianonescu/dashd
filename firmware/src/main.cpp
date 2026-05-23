#include <Arduino.h>
#include <esp_random.h>
#include "lgfx_panel.h"

#include "ble_transport.h"
#include "button.h"
#include "config.h"
#include "data_store.h"
#include "ota_link.h"
#include "pages.h"
#include "pet_widget.h"
#include "theme.h"
#include "transitions.h"
#include "usb_link.h"
#include "visibility.h"

const PageEntry kPages[PAGE_COUNT] = {
  {PAGE_HOME,     "Home",     render_home},
  {PAGE_SYSTEM,   "System",   render_system},
  {PAGE_AI_SPEND, "AI Spend", render_ai_spend},
  {PAGE_DEV_FLOW, "Dev Flow", render_dev_flow},
  {PAGE_GITHUB,   "GitHub",   render_github},
  {PAGE_CALENDAR, "Calendar", render_calendar},
  {PAGE_MESSAGES, "Messages", render_messages},
  {PAGE_TIPS,     "Tips",     render_tips},
};

static LGFX tft;
static int s_slot_idx = 0;             // index INTO the display order, not page id
static bool s_force_full_redraw = true;
static uint32_t s_last_redraw_ms = 0;
static bool s_was_host_ok = false;
static bool s_was_stale = false;
static uint8_t s_last_backlight = 255;
static uint8_t s_last_rotation = 0;

// ----- Auto-advance state -----
// `s_last_advance_ms` is reset to `millis()` on any manual page change
// (button or host show_page) so the next auto-tick is a full interval
// away. `g_auto_advance_dirty` is set from usb_link.cpp when settings
// change so we rebuild the random shuffle bag and restart the timer.
// `s_aa_bag[]` holds a Fisher-Yates shuffled queue of enabled slot
// indices for random mode — drained one at a time, refilled when empty.
static uint32_t s_last_advance_ms = 0;
uint8_t g_auto_advance_dirty = 0;
static uint8_t s_aa_bag[PAGE_COUNT];
static uint8_t s_aa_bag_pos = 0;
static uint8_t s_aa_bag_len = 0;

// Map "slot in the user's order" → "page id".
// pages_order[i] == -1 means "use the default order at this slot".
static int page_id_at_slot(int slot) {
  if (slot < 0) slot = 0;
  if (slot >= PAGE_COUNT) slot %= PAGE_COUNT;
  int8_t mapped = g_store.pages_order[slot];
  if (mapped >= 0 && mapped < PAGE_COUNT) return mapped;
  return slot;  // default: identity
}

static inline bool page_enabled(int page_id) {
  if (g_store.pages_enabled_mask == 0) return true;
  return (g_store.pages_enabled_mask & (1u << page_id)) != 0;
}

// Advance to the next slot whose mapped page is enabled.
static int next_enabled_slot(int from) {
  for (int step = 1; step <= PAGE_COUNT; step++) {
    int candidate = (from + step) % PAGE_COUNT;
    if (page_enabled(page_id_at_slot(candidate))) return candidate;
  }
  return 0;
}

// How many of the 8 slots are enabled — used as the footer denominator.
// Defaults to PAGE_COUNT when nothing is disabled (mask == 0 means "all").
static int enabled_page_count() {
  if (g_store.pages_enabled_mask == 0) return PAGE_COUNT;
  int n = 0;
  for (int i = 0; i < PAGE_COUNT; i++) {
    if (page_enabled(page_id_at_slot(i))) n++;
  }
  return n;
}

// Position of `slot` among only the enabled slots (0-based) — used as the
// footer numerator. Walks the order list counting enabled slots up to
// and including `slot`. If `slot` itself is disabled we still return
// something sensible (the count of enabled slots before it), but in
// practice s_slot_idx is always on an enabled slot because every code
// path that mutates it routes through next_enabled_slot / find_home_slot.
static int enabled_ordinal_for_slot(int slot) {
  int ord = 0;
  for (int i = 0; i < PAGE_COUNT && i < slot; i++) {
    if (page_enabled(page_id_at_slot(i))) ord++;
  }
  return ord;
}

// Fill `s_aa_bag` with the slot indices of every enabled page, in
// Fisher-Yates shuffled order. Called when the bag empties or when
// settings change. Excludes `avoid_slot` from the FIRST position so
// a refill doesn't immediately re-show the same page that just ended.
static void rebuild_aa_bag(int avoid_slot) {
  s_aa_bag_len = 0;
  for (int i = 0; i < PAGE_COUNT; i++) {
    if (page_enabled(page_id_at_slot(i))) {
      s_aa_bag[s_aa_bag_len++] = (uint8_t)i;
    }
  }
  // Fisher-Yates shuffle. `esp_random()` is hardware-RNG-backed on the
  // ESP32-C3 so it's cheap to call repeatedly.
  for (int i = s_aa_bag_len - 1; i > 0; i--) {
    uint32_t j = esp_random() % (uint32_t)(i + 1);
    uint8_t tmp = s_aa_bag[i];
    s_aa_bag[i] = s_aa_bag[j];
    s_aa_bag[j] = tmp;
  }
  // If the first item is the slot we want to avoid AND there's a
  // second item, swap them. With only one enabled page (degenerate)
  // we accept the no-op.
  if (s_aa_bag_len >= 2 && s_aa_bag[0] == (uint8_t)avoid_slot) {
    uint8_t tmp = s_aa_bag[0];
    s_aa_bag[0] = s_aa_bag[1];
    s_aa_bag[1] = tmp;
  }
  s_aa_bag_pos = 0;
}

// Pick the next slot for random-mode auto-advance using the shuffle
// bag. Refills the bag if empty. Returns `from` if nothing is enabled.
static int random_enabled_slot(int from) {
  if (s_aa_bag_pos >= s_aa_bag_len) rebuild_aa_bag(from);
  if (s_aa_bag_len == 0) return from;
  return (int)s_aa_bag[s_aa_bag_pos++];
}

static int find_home_slot() {
  // Home is page id 0. Look for the slot that maps to it.
  for (int i = 0; i < PAGE_COUNT; i++) {
    if (page_id_at_slot(i) == PAGE_HOME && page_enabled(PAGE_HOME)) return i;
  }
  return next_enabled_slot(-1);
}

static void apply_backlight() {
  uint8_t target = (g_store.backlight < 0) ? 255 : (uint8_t)g_store.backlight;
  if (target == s_last_backlight) return;
  s_last_backlight = target;
  tft.setBrightness(target);
}

static void apply_rotation() {
  uint8_t r = theme::rotation();
  if (r == s_last_rotation) return;
  s_last_rotation = r;
  tft.setRotation(r);
  s_force_full_redraw = true;
}

static void draw_all(bool full) {
  int page_id = page_id_at_slot(s_slot_idx);
  const PageEntry &p = kPages[page_id];
  uint32_t now = millis();
  bool host_ok = g_store.ever_received;
  bool stale = !g_store.host_alive(now, HOST_STALE_MS);

  if (full || host_ok != s_was_host_ok || stale != s_was_stale) {
    if (full) {
      tft.fillScreen(THEME_BG);
    }
    draw_title_bar(tft, p.name, host_ok, stale);
    s_was_host_ok = host_ok;
    s_was_stale = stale;
  }

  p.render(tft, full);
  // Footer shows "ordinal/enabled-count" — e.g. "3/5" when 3 of 5
  // enabled pages is currently visible. The raw slot index / PAGE_COUNT
  // would give wrong numbers any time the user has disabled a page.
  draw_footer(tft, enabled_ordinal_for_slot(s_slot_idx),
              enabled_page_count(), g_store.last_state_ms);
  pet_tick(tft);   // overlay last so it sits on top of every page

#ifdef DASHD_ENABLE_BLE
  // Pair-Mode screen — drawn on top of everything while a BLE central is
  // connected but hasn't entered the pairing code yet.
  if (g_ble.needs_pairing()) {
    const int w = 196, h = 104;
    const int x = (tft.width() - w) / 2;
    const int y = (tft.height() - h) / 2;
    tft.fillRoundRect(x, y, w, h, 10, THEME_SURFACE);
    tft.drawRoundRect(x, y, w, h, 10, THEME_ACCENT);
    tft.setTextDatum(TC_DATUM);
    tft.setTextColor(THEME_DIM, THEME_SURFACE);
    tft.drawString("BLUETOOTH PAIRING", x + w / 2, y + 12, 2);
    tft.setTextColor(THEME_FG, THEME_SURFACE);
    tft.setTextSize(3);
    tft.drawString(g_ble.pairing_code(), x + w / 2, y + 38, 2);
    tft.setTextSize(1);
    tft.setTextColor(THEME_DIM, THEME_SURFACE);
    tft.drawString("enter this code in dashd", x + w / 2, y + 84, 1);
    tft.setTextDatum(TL_DATUM);
  }
#endif
}

void setup() {
  pinMode(PIN_BACKLIGHT, OUTPUT);
  digitalWrite(PIN_BACKLIGHT, HIGH);

  tft.init();
  tft.setRotation(0);
  apply_backlight();
  tft.fillScreen(THEME_BG);

  button_begin();
  usb_link_begin();
#ifdef DASHD_ENABLE_BLE
  g_ble.begin();   // start NimBLE advertising alongside USB-CDC
#endif
  usb_link_restore_prefs();
  visibility::begin();
  pet_begin();
  apply_backlight();
  apply_rotation();

  // If the saved configuration leaves the current slot unreachable, jump to Home.
  if (!page_enabled(page_id_at_slot(s_slot_idx))) s_slot_idx = find_home_slot();

  delay(100);
  usb_send_event("boot");
  LOGI("dashd fw %s up, %d pages, w=%d h=%d", DASHD_FW_VERSION,
       PAGE_COUNT, tft.width(), tft.height());

  // At this point we've initialized everything that matters and have
  // already announced ourselves to the host. If this is the first boot
  // after an OTA, commit the new image so the bootloader doesn't roll
  // back on the next reset. No-op on a normal boot.
  ota_mark_running_ok();

  draw_all(true);
  s_last_redraw_ms = millis();
  s_last_advance_ms = millis();   // start the auto-advance countdown
}

void loop() {
  bool changed = usb_link_poll();
#ifdef DASHD_ENABLE_BLE
  // Drain the BLE RX stream into the shared parser. The active-owner gate
  // (usb_link.cpp) keeps USB and BLE from both applying frames at once.
  changed |= g_ble.poll();
  // Force one full redraw when the Pair-Mode overlay appears or clears so
  // it doesn't linger after pairing completes.
  static bool s_was_pairing = false;
  bool pairing = g_ble.needs_pairing();
  if (pairing != s_was_pairing) {
    s_force_full_redraw = true;
    s_was_pairing = pairing;
  }
#endif

  // OTA overlay takes precedence over everything else — pages are paused,
  // button events ignored, transitions stopped. The user gets a single
  // dedicated screen until the update either finishes or fails.
  static bool s_was_ota = false;
  bool ota_now = ota_active() || ota_failed();
  if (ota_now != s_was_ota) {
    s_force_full_redraw = true;
    s_was_ota = ota_now;
  }
  if (ota_now) {
    uint32_t now_ms = millis();
    if (s_force_full_redraw || (now_ms - s_last_redraw_ms) >= 100) {
      render_ota_overlay(tft, s_force_full_redraw);
      s_force_full_redraw = false;
      s_last_redraw_ms = now_ms;
    }
    return;
  }

  // Pending page change from the host? The cmd sends a page NAME → id; we
  // resolve back to whichever slot is currently mapped to that id (or just
  // place it on slot 0 if it isn't in the user's order yet).
  if (g_pending_show_page >= 0 && g_pending_show_page < PAGE_COUNT) {
    int target_id = g_pending_show_page;
    int target_slot = -1;
    for (int i = 0; i < PAGE_COUNT; i++) {
      if (page_id_at_slot(i) == target_id) { target_slot = i; break; }
    }
    if (target_slot >= 0 && target_slot != s_slot_idx) {
      int from = s_slot_idx;
      s_slot_idx = target_slot;
      // host-driven jumps are direction-agnostic — slide forward.
      transition_begin(from, target_slot, TRANSITION_FORWARD);
      usb_send_event_page("page_changed", kPages[target_id].name);
      // Manual page change — restart the auto-advance countdown so the
      // user gets a full interval to read the page they just selected.
      s_last_advance_ms = millis();
    }
    g_pending_show_page = -1;
  }

  ButtonEvent ev = button_poll();
  if (!transition_active() && ev == BTN_SHORT_PRESS) {
    int from = s_slot_idx;
    s_slot_idx = next_enabled_slot(s_slot_idx);
    if (s_slot_idx != from) {
      transition_begin(from, s_slot_idx, TRANSITION_FORWARD);
    }
    // Emit BOTH the raw gesture and the resulting page change so the UI can
    // surface gesture activity independently of what it triggered.
    usb_send_event("button_short_press");
    usb_send_event_page("page_changed", kPages[page_id_at_slot(s_slot_idx)].name);
    s_last_advance_ms = millis();
  } else if (!transition_active() && ev == BTN_LONG_PRESS) {
    int from = s_slot_idx;
    s_slot_idx = find_home_slot();
    if (s_slot_idx != from) {
      transition_begin(from, s_slot_idx, TRANSITION_HOME);
    }
    usb_send_event("button_long_press");
    usb_send_event_page("page_changed", kPages[page_id_at_slot(s_slot_idx)].name);
    s_last_advance_ms = millis();
  }

  // Auto-advance: cycle through enabled pages on a timer with no button
  // press needed. Disabled by user → no-op. Held off during transitions,
  // OTA, and BLE pairing so we don't fight the host or interrupt the
  // user's gestures.
  if (g_auto_advance_dirty) {
    s_last_advance_ms = millis();
    s_aa_bag_len = 0;     // force a rebuild on next random pick
    s_aa_bag_pos = 0;
    g_auto_advance_dirty = 0;
  }
  if (g_store.auto_advance_enabled && !transition_active()
      && !pet_install_active()
#ifdef DASHD_ENABLE_BLE
      && !g_ble.needs_pairing()
#endif
      ) {
    uint16_t iv = g_store.auto_advance_interval_s;
    if (iv < 3) iv = 3;
    uint32_t iv_ms = (uint32_t)iv * 1000;
    if ((millis() - s_last_advance_ms) >= iv_ms) {
      int from = s_slot_idx;
      int next = (g_store.auto_advance_mode == 1)
                   ? random_enabled_slot(from)
                   : next_enabled_slot(from);
      if (next != from) {
        s_slot_idx = next;
        transition_begin(from, next, TRANSITION_FORWARD);
        usb_send_event_page("page_changed",
                            kPages[page_id_at_slot(s_slot_idx)].name);
      }
      // Reset even if next == from (e.g. only one enabled page) so we
      // don't tight-loop trying to advance every tick.
      s_last_advance_ms = millis();
    }
  }

  apply_backlight();
  apply_rotation();

  uint32_t now = millis();
  // While a transition is running, drive it on a tight 16 ms loop so the
  // pet's wipe looks smooth. Skip the normal page redraw. When the
  // transition completes (`done == true`), schedule a full redraw on
  // the next tick — the transition itself only paints the wipe + clears
  // the content band; draw_all() then handles the actual new-page
  // render with correct slot→id resolution and enabled-count footer.
  if (transition_active()) {
    bool done = transition_tick(tft);
    s_last_redraw_ms = now;
    if (done) {
      s_force_full_redraw = true;
    } else {
      return;
    }
  }
  if (s_force_full_redraw || changed || (now - s_last_redraw_ms) >= DISPLAY_REDRAW_MS) {
    draw_all(s_force_full_redraw);
    s_force_full_redraw = false;
    s_last_redraw_ms = now;
  } else {
    // No full redraw this tick — still let the pet animate at its own pace.
    pet_tick(tft);
  }
}
