#pragma once
#include "lgfx_panel.h"

// IMPORTANT: do not renumber existing ids without a migration of the
// `pmask` / `porder` NVS blobs. The persisted bitmask is keyed by id —
// changing a page's id silently flips a different page's enabled bit
// for any already-deployed device. PAGE_SYSTEM remains at id 1 as a
// reserved/deprecated slot (v0.1.11 and earlier devices stored it
// there); v0.1.12 onwards skips rendering it and on first boot clears
// bit 1 of the saved mask. New pages append AFTER the legacy block.
enum PageId {
  PAGE_HOME = 0,
  PAGE_SYSTEM,        // deprecated v0.1.12+ — replaced by CPU/MEM/GPU/NET
  PAGE_AI_SPEND,
  PAGE_DEV_FLOW,
  PAGE_GITHUB,
  PAGE_CALENDAR,
  PAGE_MESSAGES,
  PAGE_TIPS,
  PAGE_CPU,           // v0.1.12+
  PAGE_MEMORY,        // v0.1.12+
  PAGE_GPU,           // v0.1.12+
  PAGE_NETWORK,       // v0.1.12+
  PAGE_COUNT
};

// Bitmask of every page id that should never be displayed. Used by
// the main loop to skip the deprecated System slot in next-page /
// auto-advance walks and to migrate any pages_enabled_mask that
// survived from earlier firmware.
constexpr uint32_t PAGES_DEPRECATED_MASK = (1u << PAGE_SYSTEM);

// Length of the per-device pages_order array in DataStore. Sized for
// the 12 current pages with headroom for one round of additions
// without another NVS-blob migration.
constexpr int PAGES_ORDER_LEN = 16;

struct PageEntry {
  PageId id;
  const char *name;
  void (*render)(LGFX &tft, bool full);
};

extern const PageEntry kPages[PAGE_COUNT];

// Per-page render functions.
void render_home(LGFX &tft, bool full);
void render_system(LGFX &tft, bool full);    // deprecated, no-op stub
void render_ai_spend(LGFX &tft, bool full);
void render_dev_flow(LGFX &tft, bool full);
void render_github(LGFX &tft, bool full);
void render_calendar(LGFX &tft, bool full);
void render_messages(LGFX &tft, bool full);
void render_tips(LGFX &tft, bool full);
void render_cpu(LGFX &tft, bool full);       // v0.1.12+
void render_memory(LGFX &tft, bool full);    // v0.1.12+
void render_gpu(LGFX &tft, bool full);       // v0.1.12+
void render_network(LGFX &tft, bool full);   // v0.1.12+

// OTA progress overlay. Drawn instead of the normal page stack while a
// firmware update is in flight (see ota_link.h).
void render_ota_overlay(LGFX &tft, bool full);

// Shared chrome.
void draw_title_bar(LGFX &tft, const char *name, bool host_ok, bool stale);
void draw_footer(LGFX &tft, int page_idx, int page_count, uint32_t last_state_ms);
