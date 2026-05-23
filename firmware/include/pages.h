#pragma once
#include "lgfx_panel.h"

enum PageId {
  PAGE_HOME = 0,
  PAGE_SYSTEM,
  PAGE_AI_SPEND,
  PAGE_DEV_FLOW,
  PAGE_GITHUB,
  PAGE_CALENDAR,
  PAGE_MESSAGES,
  PAGE_TIPS,
  PAGE_COUNT
};

struct PageEntry {
  PageId id;
  const char *name;
  void (*render)(LGFX &tft, bool full);
};

extern const PageEntry kPages[PAGE_COUNT];

// Per-page render functions.
void render_home(LGFX &tft, bool full);
void render_system(LGFX &tft, bool full);
void render_ai_spend(LGFX &tft, bool full);
void render_dev_flow(LGFX &tft, bool full);
void render_github(LGFX &tft, bool full);
void render_calendar(LGFX &tft, bool full);
void render_messages(LGFX &tft, bool full);
void render_tips(LGFX &tft, bool full);

// OTA progress overlay. Drawn instead of the normal page stack while a
// firmware update is in flight (see ota_link.h).
void render_ota_overlay(LGFX &tft, bool full);

// Shared chrome.
void draw_title_bar(LGFX &tft, const char *name, bool host_ok, bool stale);
void draw_footer(LGFX &tft, int page_idx, int page_count, uint32_t last_state_ms);
