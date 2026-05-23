#include <Arduino.h>
#include "lgfx_panel.h"
#include <math.h>

#include "config.h"
#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

void render_system(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 6;
  char buf[32];

  if (visibility::shown("system.cpu")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "CPU", x, y, theme::ROLE_LABEL);
    y += 16;
    int visible = g_store.cpu_count > 8 ? 8 : g_store.cpu_count;
    for (int i = 0; i < visible; i++) {
      snprintf(buf, sizeof(buf), "%d", i);
      tft.setTextColor(THEME_DIM, THEME_BG);
      tft.setTextDatum(TL_DATUM);
      draw_role(tft, buf, x, y, theme::ROLE_LABEL);
      widgets::hbar(tft, x + 16, y, w - 50, 8, g_store.cpu_pct[i]);
      snprintf(buf, sizeof(buf), "%3d%%", g_store.cpu_pct[i]);
      tft.setTextColor(THEME_FG, THEME_BG);
      tft.setTextDatum(TR_DATUM);
      draw_role(tft, buf, x + w, y - 2, theme::ROLE_LABEL);
      y += 12;
    }
    y += 6;
  }

  if (visibility::shown("system.ram")) {
    // Show pressure (cache-excluded) as the gauge value when available —
    // it's the more useful "would this trigger swapping" indicator. We
    // still surface the raw vm.percent next to it in dim so the user knows
    // both numbers.
    int gauge_pct = (g_store.ram_pressure_pct >= 0) ? g_store.ram_pressure_pct
                                                     : g_store.ram_pct;
    if (gauge_pct >= 0) {
      if (g_store.ram_pressure_pct >= 0 && g_store.ram_pct >= 0
          && g_store.ram_pressure_pct != g_store.ram_pct) {
        snprintf(buf, sizeof(buf), "%d%% real · %d%% total · %.1f/%.1f GB",
                 g_store.ram_pressure_pct, g_store.ram_pct,
                 g_store.ram_used_gb, g_store.ram_total_gb);
      } else {
        snprintf(buf, sizeof(buf), "%d%% · %.1f/%.1f GB",
                 gauge_pct, g_store.ram_used_gb, g_store.ram_total_gb);
      }
    } else snprintf(buf, sizeof(buf), "--");
    widgets::kv(tft, x, y, w, "RAM", buf);
    y += 16;
    widgets::hbar(tft, x, y, w, 10, gauge_pct);
    y += 16;
  }

  if (visibility::shown("system.disk")) {
    if (g_store.disk_pct >= 0) snprintf(buf, sizeof(buf), "%d%%", g_store.disk_pct);
    else snprintf(buf, sizeof(buf), "--");
    widgets::kv(tft, x, y, w, "Disk", buf);
    y += 16;
    widgets::hbar(tft, x, y, w, 10, g_store.disk_pct);
    y += 18;
  }

  if (visibility::shown("system.net")) {
    if (g_store.net_up_kbps >= 0)
      snprintf(buf, sizeof(buf), "%d / %d kbps", g_store.net_down_kbps, g_store.net_up_kbps);
    else
      snprintf(buf, sizeof(buf), "--");
    widgets::kv(tft, x, y, w, "Net dn/up", buf);
    y += 16;
  }

  if (visibility::shown("system.battery")) {
    if (g_store.battery_pct >= 0) {
      const char *charge = g_store.battery_charging == 1 ? " (chg)" :
                          g_store.battery_charging == 0 ? "" : "";
      snprintf(buf, sizeof(buf), "%d%%%s", g_store.battery_pct, charge);
    } else snprintf(buf, sizeof(buf), "n/a");
    widgets::kv(tft, x, y, w, "Battery", buf);
    y += 16;
  }

  if (visibility::shown("system.temp")) {
    if (!isnan(g_store.temp_cpu_c)) snprintf(buf, sizeof(buf), "%.1f C", g_store.temp_cpu_c);
    else                            snprintf(buf, sizeof(buf), "n/a");
    widgets::kv(tft, x, y, w, "CPU temp", buf);
  }
}
