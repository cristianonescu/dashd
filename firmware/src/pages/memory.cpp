// Memory page — dedicated complete memory view (v0.1.12+).
//
// Shows:
//   - the pressure bar (the meaningful "would this swap?" metric on
//     macOS — cache excluded) with the headline used/total GB
//   - swap usage (used / total)
//   - cached / active / inactive breakdown when the OS exposes them
//   - top-3 processes by RSS
//
// Visibility groups: `memory.headline`, `memory.swap`,
// `memory.breakdown`, `memory.top`.
#include <Arduino.h>
#include <math.h>
#include "lgfx_panel.h"

#include "config.h"
#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

void render_memory(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(),
                 THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 6;
  char buf[48];

  if (visibility::shown("memory.headline")) {
    int gauge_pct = (g_store.ram_pressure_pct >= 0) ? g_store.ram_pressure_pct
                                                    : g_store.ram_pct;
    if (gauge_pct >= 0 && g_store.ram_total_gb > 0) {
      if (g_store.ram_pressure_pct >= 0 && g_store.ram_pct >= 0
          && g_store.ram_pressure_pct != g_store.ram_pct) {
        snprintf(buf, sizeof(buf), "%d%% real / %d%% incl. cache",
                 g_store.ram_pressure_pct, g_store.ram_pct);
      } else {
        snprintf(buf, sizeof(buf), "%d%%", gauge_pct);
      }
    } else {
      snprintf(buf, sizeof(buf), "--");
    }
    widgets::kv(tft, x, y, w, "Used", buf);
    y += 14;
    widgets::hbar(tft, x, y, w, 10, gauge_pct);
    y += 16;
    if (g_store.ram_total_gb > 0) {
      snprintf(buf, sizeof(buf), "%.1f / %.1f GB",
               g_store.ram_used_gb, g_store.ram_total_gb);
      widgets::kv(tft, x, y, w, "", buf);
      y += 14;
    }
  }

  if (visibility::shown("memory.swap")) {
    if (g_store.ram_swap_total_gb > 0) {
      snprintf(buf, sizeof(buf), "%.1f / %.1f GB  (%d%%)",
               g_store.ram_swap_used_gb, g_store.ram_swap_total_gb,
               g_store.ram_swap_pct);
    } else {
      snprintf(buf, sizeof(buf), "off");
    }
    widgets::kv(tft, x, y, w, "Swap", buf);
    y += 14;
    if (g_store.ram_swap_total_gb > 0) {
      widgets::hbar(tft, x, y, w, 6, g_store.ram_swap_pct);
      y += 10;
    }
  }

  if (visibility::shown("memory.breakdown")) {
    bool any = (g_store.ram_active_gb > 0 || g_store.ram_inactive_gb > 0 ||
                g_store.ram_cached_gb > 0);
    if (any) {
      y += 4;
      tft.setTextColor(THEME_DIM, THEME_BG);
      tft.setTextDatum(TL_DATUM);
      draw_role(tft, "BREAKDOWN", x, y, theme::ROLE_LABEL);
      y += 14;
      if (g_store.ram_active_gb >= 0) {
        snprintf(buf, sizeof(buf), "%.1f GB", g_store.ram_active_gb);
        widgets::kv(tft, x, y, w, "Active", buf);
        y += 13;
      }
      if (g_store.ram_inactive_gb >= 0) {
        snprintf(buf, sizeof(buf), "%.1f GB", g_store.ram_inactive_gb);
        widgets::kv(tft, x, y, w, "Inactive", buf);
        y += 13;
      }
      if (g_store.ram_cached_gb >= 0) {
        snprintf(buf, sizeof(buf), "%.1f GB", g_store.ram_cached_gb);
        widgets::kv(tft, x, y, w, "Cached", buf);
        y += 13;
      }
    }
  }

  if (visibility::shown("memory.top") && g_store.top_ram_count > 0) {
    y += 4;
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "TOP", x, y, theme::ROLE_LABEL);
    y += 14;
    int limit = g_store.top_ram_count > DataStore::TOP_N
                  ? DataStore::TOP_N : g_store.top_ram_count;
    for (int i = 0; i < limit; i++) {
      const auto &row = g_store.top_ram[i];
      if (row.ram_mb >= 1024) {
        snprintf(buf, sizeof(buf), "%.1fG", row.ram_mb / 1024.0f);
      } else {
        snprintf(buf, sizeof(buf), "%dM", row.ram_mb);
      }
      widgets::kv(tft, x, y, w, row.name, buf);
      y += 13;
    }
  }
}
