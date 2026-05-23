// Network page — dedicated complete network view (v0.1.12+).
//
// Shows:
//   - active outbound interface name + up/down kbps headline
//   - per-interface table (top 3 by combined traffic, with daily totals)
//
// Visibility groups: `network.headline`, `network.ifaces`.
#include <Arduino.h>
#include <math.h>
#include "lgfx_panel.h"

#include "config.h"
#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

static const char *format_total(long mb, char *buf, size_t cap) {
  if (mb < 0) {
    snprintf(buf, cap, "n/a");
  } else if (mb >= 1024) {
    snprintf(buf, cap, "%.1f GB", mb / 1024.0f);
  } else {
    snprintf(buf, cap, "%ld MB", mb);
  }
  return buf;
}

void render_network(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(),
                 THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 6;
  char buf[40];

  // Find the active iface (first with is_active=true, else first).
  const DataStore::IfaceStat *active = nullptr;
  for (int i = 0; i < g_store.iface_count; i++) {
    if (g_store.ifaces[i].is_active) { active = &g_store.ifaces[i]; break; }
  }
  if (active == nullptr && g_store.iface_count > 0) {
    active = &g_store.ifaces[0];
  }

  if (visibility::shown("network.headline")) {
    if (active != nullptr) {
      widgets::kv(tft, x, y, w, "Active", active->name);
      y += 14;
      snprintf(buf, sizeof(buf), "%c %d kbps",
               (char)0x19 /* ↓ in many bitmap fonts; fallback char if missing */,
               active->down_kbps >= 0 ? active->down_kbps : 0);
      widgets::kv(tft, x, y, w, "Down", buf);
      y += 13;
      snprintf(buf, sizeof(buf), "%d kbps",
               active->up_kbps >= 0 ? active->up_kbps : 0);
      widgets::kv(tft, x, y, w, "Up", buf);
      y += 13;
    } else if (g_store.net_up_kbps >= 0 || g_store.net_down_kbps >= 0) {
      // Legacy aggregate fallback when per-iface data isn't available.
      snprintf(buf, sizeof(buf), "%d kbps",
               g_store.net_down_kbps >= 0 ? g_store.net_down_kbps : 0);
      widgets::kv(tft, x, y, w, "Down", buf);
      y += 13;
      snprintf(buf, sizeof(buf), "%d kbps",
               g_store.net_up_kbps >= 0 ? g_store.net_up_kbps : 0);
      widgets::kv(tft, x, y, w, "Up", buf);
      y += 13;
    } else {
      widgets::kv(tft, x, y, w, "Status", "no data");
      y += 14;
    }
  }

  if (visibility::shown("network.ifaces") && g_store.iface_count > 0) {
    y += 4;
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "INTERFACES", x, y, theme::ROLE_LABEL);
    y += 14;
    int limit = g_store.iface_count > DataStore::IFACE_N
                  ? DataStore::IFACE_N : g_store.iface_count;
    for (int i = 0; i < limit; i++) {
      const auto &iface = g_store.ifaces[i];
      // Row 1: name + active marker
      const char *marker = iface.is_active ? " *"
                         : iface.is_up     ? ""
                                           : " (down)";
      snprintf(buf, sizeof(buf), "%s%s", iface.name, marker);
      tft.setTextColor(THEME_FG, THEME_BG);
      tft.setTextDatum(TL_DATUM);
      draw_role(tft, buf, x, y, theme::ROLE_LABEL);
      // Row 2: rates
      char dnbuf[16], upbuf[16];
      snprintf(dnbuf, sizeof(dnbuf), "%dk",
               iface.down_kbps >= 0 ? iface.down_kbps : 0);
      snprintf(upbuf, sizeof(upbuf), "%dk",
               iface.up_kbps >= 0 ? iface.up_kbps : 0);
      snprintf(buf, sizeof(buf), "%s dn / %s up", dnbuf, upbuf);
      tft.setTextColor(THEME_DIM, THEME_BG);
      tft.setTextDatum(TR_DATUM);
      draw_role(tft, buf, x + w, y, theme::ROLE_LABEL);
      y += 12;
      // Row 3: cumulative totals since the OS-level counters reset
      // (typically system boot). Not aligned to wall-clock midnight.
      char dn_total[20], up_total[20];
      format_total(iface.down_total_mb, dn_total, sizeof(dn_total));
      format_total(iface.up_total_mb,   up_total, sizeof(up_total));
      snprintf(buf, sizeof(buf), "since boot: %s dn / %s up", dn_total, up_total);
      tft.setTextColor(THEME_DIM, THEME_BG);
      tft.setTextDatum(TL_DATUM);
      draw_role(tft, buf, x, y, theme::ROLE_LABEL);
      y += 14;
    }
  }
}
