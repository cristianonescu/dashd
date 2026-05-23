// CPU page — dedicated all-cores view (v0.1.12+).
//
// Replaces the CPU section of the old aggregated System page. Shows
// per-core utilization with one bar per core, an "avg" row, load
// average (1/5/15m), current/max frequency, temperature, and the top
// CPU-consuming processes.
//
// Layout fits the 240×320 portrait panel:
//   header (title bar handled outside)
//   per-core bars (8 cores; on systems with more we fold by 2 into 8 rows)
//   "avg" row
//   load avg + frequency line
//   top-3 processes section
//
// Visibility groups: `cpu.cores`, `cpu.load`, `cpu.freq`, `cpu.temp`,
// `cpu.top` — hideable independently from Settings → Elements.
#include <Arduino.h>
#include <math.h>
#include "lgfx_panel.h"

#include "config.h"
#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

void render_cpu(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(),
                 THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 4;
  char buf[40];

  if (visibility::shown("cpu.cores")) {
    int core_count = g_store.cpu_count > 8 ? 8 : g_store.cpu_count;
    int avg_pct = 0;
    if (core_count > 0) {
      int sum = 0;
      for (int i = 0; i < core_count; i++) sum += g_store.cpu_pct[i];
      avg_pct = sum / core_count;
    }

    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "CORES", x, y, theme::ROLE_LABEL);
    y += 14;
    for (int i = 0; i < core_count; i++) {
      snprintf(buf, sizeof(buf), "%d", i);
      tft.setTextColor(THEME_DIM, THEME_BG);
      tft.setTextDatum(TL_DATUM);
      draw_role(tft, buf, x, y, theme::ROLE_LABEL);
      widgets::hbar(tft, x + 14, y + 1, w - 50, 7, g_store.cpu_pct[i]);
      snprintf(buf, sizeof(buf), "%3d%%", g_store.cpu_pct[i]);
      tft.setTextColor(THEME_FG, THEME_BG);
      tft.setTextDatum(TR_DATUM);
      draw_role(tft, buf, x + w, y - 2, theme::ROLE_LABEL);
      y += 11;
    }
    if (core_count > 0) {
      y += 2;
      tft.setTextColor(THEME_DIM, THEME_BG);
      tft.setTextDatum(TL_DATUM);
      draw_role(tft, "avg", x, y, theme::ROLE_LABEL);
      widgets::hbar(tft, x + 14, y + 1, w - 50, 7, avg_pct);
      snprintf(buf, sizeof(buf), "%3d%%", avg_pct);
      tft.setTextColor(THEME_ACCENT, THEME_BG);
      tft.setTextDatum(TR_DATUM);
      draw_role(tft, buf, x + w, y - 2, theme::ROLE_LABEL);
      y += 14;
    }
  }

  if (visibility::shown("cpu.load")) {
    if (!isnan(g_store.load_1m)) {
      snprintf(buf, sizeof(buf), "%.2f  %.2f  %.2f",
               g_store.load_1m, g_store.load_5m, g_store.load_15m);
    } else {
      snprintf(buf, sizeof(buf), "n/a");
    }
    widgets::kv(tft, x, y, w, "Load 1/5/15", buf);
    y += 14;
  }

  if (visibility::shown("cpu.freq")) {
    if (g_store.cpu_freq_mhz > 0) {
      if (g_store.cpu_freq_max_mhz > 0) {
        snprintf(buf, sizeof(buf), "%d / %d MHz",
                 g_store.cpu_freq_mhz, g_store.cpu_freq_max_mhz);
      } else {
        snprintf(buf, sizeof(buf), "%d MHz", g_store.cpu_freq_mhz);
      }
    } else {
      snprintf(buf, sizeof(buf), "n/a");
    }
    widgets::kv(tft, x, y, w, "Freq", buf);
    y += 14;
  }

  if (visibility::shown("cpu.temp")) {
    if (!isnan(g_store.temp_cpu_c)) {
      snprintf(buf, sizeof(buf), "%.1f C", g_store.temp_cpu_c);
    } else {
      snprintf(buf, sizeof(buf), "n/a");
    }
    widgets::kv(tft, x, y, w, "Temp", buf);
    y += 14;
  }

  if (visibility::shown("cpu.top") && g_store.top_cpu_count > 0) {
    y += 4;
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "TOP", x, y, theme::ROLE_LABEL);
    y += 14;
    int limit = g_store.top_cpu_count > DataStore::TOP_N
                  ? DataStore::TOP_N : g_store.top_cpu_count;
    for (int i = 0; i < limit; i++) {
      const auto &row = g_store.top_cpu[i];
      snprintf(buf, sizeof(buf), "%.0f%%", row.cpu_pct);
      widgets::kv(tft, x, y, w, row.name, buf);
      y += 13;
    }
  }
}
