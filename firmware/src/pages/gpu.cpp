// GPU page — dedicated GPU view (v0.1.12+).
//
// Cross-platform best-effort. The agent's gpu collector emits
// `gpu.available = true` only when it could read a utilization
// percentage. When false, we render a friendly "GPU stats not
// available" rather than a blank screen full of dashes.
//
// On macOS Apple Silicon, VRAM is unified-memory; the agent reports
// `vram_total_mb = -1` (n/a) and only `vram_used_mb` populated from
// IOAccelerator's "In use system memory". The page detects that and
// shows "Used" without a /total denominator.
//
// Visibility groups: `gpu.headline`, `gpu.util`, `gpu.vram`,
// `gpu.thermals`.
#include <Arduino.h>
#include <math.h>
#include "lgfx_panel.h"

#include "config.h"
#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

void render_gpu(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(),
                 THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 8;
  char buf[40];

  if (!g_store.gpu_available) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TC_DATUM);
    draw_role(tft, "GPU stats not available", tft.width() / 2, y + 60,
              theme::ROLE_LABEL);
    draw_role(tft, "(no GPU detected, or", tft.width() / 2, y + 80,
              theme::ROLE_LABEL);
    draw_role(tft, "platform reports n/a)", tft.width() / 2, y + 92,
              theme::ROLE_LABEL);
    tft.setTextDatum(TL_DATUM);
    return;
  }

  if (visibility::shown("gpu.headline")) {
    if (g_store.gpu_name[0] != '\0') {
      widgets::kv(tft, x, y, w, "Device", g_store.gpu_name);
      y += 14;
    }
    if (g_store.gpu_vendor[0] != '\0') {
      const char *suffix =
          (g_store.gpu_count > 1) ? " (+more)" : "";
      snprintf(buf, sizeof(buf), "%s%s", g_store.gpu_vendor, suffix);
      widgets::kv(tft, x, y, w, "Vendor", buf);
      y += 14;
    }
    y += 2;
  }

  if (visibility::shown("gpu.util")) {
    if (g_store.gpu_util_pct >= 0) {
      snprintf(buf, sizeof(buf), "%d%%", g_store.gpu_util_pct);
    } else {
      snprintf(buf, sizeof(buf), "n/a");
    }
    widgets::kv(tft, x, y, w, "Util", buf);
    y += 14;
    widgets::hbar(tft, x, y, w, 10, g_store.gpu_util_pct);
    y += 16;
  }

  if (visibility::shown("gpu.vram")) {
    if (g_store.gpu_vram_total_mb > 0) {
      int pct = (int)((long)g_store.gpu_vram_used_mb * 100
                      / g_store.gpu_vram_total_mb);
      snprintf(buf, sizeof(buf), "%d / %d MB", g_store.gpu_vram_used_mb,
               g_store.gpu_vram_total_mb);
      widgets::kv(tft, x, y, w, "VRAM", buf);
      y += 14;
      widgets::hbar(tft, x, y, w, 8, pct);
      y += 12;
    } else if (g_store.gpu_vram_used_mb >= 0) {
      // Unified memory (Apple Silicon) — used only, no fixed total.
      snprintf(buf, sizeof(buf), "%d MB (unified)",
               g_store.gpu_vram_used_mb);
      widgets::kv(tft, x, y, w, "VRAM", buf);
      y += 14;
    }
  }

  if (visibility::shown("gpu.thermals")) {
    if (g_store.gpu_temp_c > 0) {
      snprintf(buf, sizeof(buf), "%d C", g_store.gpu_temp_c);
      widgets::kv(tft, x, y, w, "Temp", buf);
      y += 14;
    }
    if (g_store.gpu_power_w > 0) {
      snprintf(buf, sizeof(buf), "%d W", g_store.gpu_power_w);
      widgets::kv(tft, x, y, w, "Power", buf);
      y += 14;
    }
  }
}
