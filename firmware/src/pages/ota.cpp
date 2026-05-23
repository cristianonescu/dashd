/**
 * OTA progress overlay.
 *
 * Drawn on top of the normal page stack whenever `ota_active()` returns
 * true. main.cpp's loop diverts to `render_ota_overlay()` instead of
 * the regular page render while an update is in flight, so the user
 * sees:
 *
 *   ┌────────────────────────────┐
 *   │     Updating firmware       │
 *   │       0.1.1 → 0.1.2         │
 *   │                             │
 *   │     ████████░░░░░░  62 %    │
 *   │     820 / 1310 KB           │
 *   │                             │
 *   │   Do not unplug the device  │
 *   └────────────────────────────┘
 *
 * On failure we hold the screen for ~5 s with a red "Update failed —
 * previous firmware kept" message before returning to the normal pages.
 */
#include <Arduino.h>
#include "lgfx_panel.h"

#include "config.h"
#include "ota_link.h"
#include "theme.h"

void render_ota_overlay(LGFX &tft, bool full_redraw) {
  // We don't bother with partial redraws — the whole screen is OTA-owned
  // while this is active. Reduces the bookkeeping versus the regular pages.
  if (full_redraw) {
    tft.fillScreen(THEME_BG);
  }

  const int W = tft.width();
  const int H = tft.height();

  // Title.
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(THEME_FG, THEME_BG);
  tft.setTextSize(1);
  tft.setFont(&fonts::Font4);
  tft.drawString("Updating firmware", W / 2, 36);

  // Version transition.
  tft.setFont(&fonts::Font2);
  tft.setTextColor(THEME_DIM, THEME_BG);
  String header = String(DASHD_FW_VERSION) + " \xE2\x86\x92 " + ota_target_version();
  tft.drawString(header.c_str(), W / 2, 64);

  // Big progress bar.
  const int bar_x = 20;
  const int bar_y = H / 2 - 12;
  const int bar_w = W - 40;
  const int bar_h = 24;
  const size_t total = ota_total_size();
  const size_t got   = ota_bytes_received();
  int pct = (total > 0) ? (int)((got * 100) / total) : 0;
  if (pct > 100) pct = 100;
  if (pct < 0) pct = 0;

  // Track.
  tft.drawRoundRect(bar_x, bar_y, bar_w, bar_h, 6, THEME_HAIRLINE);
  // Fill.
  int fill_w = (bar_w * pct) / 100;
  // Clear inside (so the bar shrinks gracefully if we restart — shouldn't
  // happen, but defensive).
  tft.fillRoundRect(bar_x + 1, bar_y + 1, bar_w - 2, bar_h - 2, 5, THEME_BG);
  if (fill_w > 2) {
    tft.fillRoundRect(bar_x + 1, bar_y + 1, fill_w - 2, bar_h - 2, 5, THEME_ACCENT);
  }

  // Percentage in the centre of the bar.
  tft.setFont(&fonts::Font2);
  tft.setTextColor(THEME_FG, THEME_ACCENT);
  String pct_str = String(pct) + " %";
  tft.drawString(pct_str.c_str(), W / 2, bar_y + bar_h / 2);

  // Byte counter.
  tft.setTextColor(THEME_DIM, THEME_BG);
  String bytes_str = String((got + 1023) / 1024) + " / " +
                     String((total + 1023) / 1024) + " KB";
  tft.drawString(bytes_str.c_str(), W / 2, bar_y + bar_h + 22);

  // Footer warning.
  tft.setFont(&fonts::Font2);
  tft.setTextColor(THEME_WARN, THEME_BG);
  tft.drawString("Do not unplug the device", W / 2, H - 32);

  // Error overlay if the OTA failed mid-stream.
  if (ota_failed()) {
    tft.setTextColor(THEME_CRIT, THEME_BG);
    tft.drawString("Update failed", W / 2, H / 2 - 60);
    tft.setFont(&fonts::Font0);
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.drawString(ota_last_error(), W / 2, H / 2 - 44);
  }
}
