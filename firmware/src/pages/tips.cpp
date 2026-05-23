#include <Arduino.h>
#include "lgfx_panel.h"

#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

// Color for a suggestion severity string.
static uint16_t severity_color(const char *sev) {
  if (sev && strcmp(sev, "crit") == 0) return THEME_CRIT;
  if (sev && strcmp(sev, "warn") == 0) return THEME_WARN;
  return THEME_ACCENT;  // info / unknown
}

// Severity glyph at the start of each line.
static const char *severity_glyph(const char *sev) {
  if (sev && strcmp(sev, "crit") == 0) return "!";
  if (sev && strcmp(sev, "warn") == 0) return "·";
  return " ";
}

static void draw_proc_row(LGFX &tft, int x, int y, int w, const DataStore::TopProc &p, bool ram_view) {
  // Name + (×procs) suffix if this is an aggregated app.
  tft.setTextColor(THEME_FG, THEME_BG);
  tft.setTextDatum(TL_DATUM);
  char label[DataStore::NAME_LEN + 8];
  if (p.procs > 1) snprintf(label, sizeof(label), "%s ×%d", p.name[0] ? p.name : "?", p.procs);
  else             snprintf(label, sizeof(label), "%s",     p.name[0] ? p.name : "?");
  draw_role(tft, label, x, y, theme::ROLE_LABEL);

  char buf[16];
  if (ram_view) {
    if (p.ram_mb >= 1024) snprintf(buf, sizeof(buf), "%.1fG", p.ram_mb / 1024.0);
    else                  snprintf(buf, sizeof(buf), "%dM", p.ram_mb);
  } else {
    snprintf(buf, sizeof(buf), "%.0f%%", p.cpu_pct);
  }
  tft.setTextColor(THEME_DIM, THEME_BG);
  tft.setTextDatum(TR_DATUM);
  draw_role(tft, buf, x + w, y, theme::ROLE_LABEL);
}

void render_tips(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 6;
  char buf[80];

  bool drew_section = false;

  if (visibility::shown("tips.suggestions")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "SUGGESTIONS", x, y, theme::ROLE_LABEL);
    y += 16;

    if (g_store.suggestions_count == 0) {
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, "all clear", x + 4, y, theme::ROLE_LABEL);
      y += 16;
    } else {
      for (int i = 0; i < g_store.suggestions_count; i++) {
        const auto &s = g_store.suggestions[i];
        uint16_t c = severity_color(s.severity);
        // Severity capsule on the left — small colored dot that gives the
        // list a clearer visual rhythm than a leading glyph alone.
        tft.fillCircle(x + 4, y + 5, 3, c);
        tft.setTextColor(THEME_FG, THEME_BG);
        draw_role(tft, s.text, x + 14, y, theme::ROLE_LABEL);
        y += 15;
      }
    }
    drew_section = true;
  }

  if (visibility::shown("tips.top_cpu")) {
    if (drew_section) {
      y += 4;
      tft.drawFastHLine(x, y, w, THEME_HAIRLINE);
      y += 4;
    }
    tft.setTextColor(THEME_DIM, THEME_BG);
    draw_role(tft, "TOP CPU", x, y, theme::ROLE_LABEL);
    y += 14;
    if (g_store.top_cpu_count == 0) {
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, "—", x + 4, y, theme::ROLE_LABEL);
      y += 14;
    } else {
      for (int i = 0; i < g_store.top_cpu_count; i++) {
        draw_proc_row(tft, x + 4, y, w - 8, g_store.top_cpu[i], /*ram_view=*/false);
        y += 14;
      }
    }
    drew_section = true;
  }

  if (visibility::shown("tips.top_ram")) {
    if (drew_section) {
      y += 4;
      tft.drawFastHLine(x, y, w, THEME_HAIRLINE);
      y += 4;
    }
    tft.setTextColor(THEME_DIM, THEME_BG);
    draw_role(tft, "TOP RAM", x, y, theme::ROLE_LABEL);
    y += 14;
    if (g_store.top_ram_count == 0) {
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, "—", x + 4, y, theme::ROLE_LABEL);
    } else {
      for (int i = 0; i < g_store.top_ram_count; i++) {
        draw_proc_row(tft, x + 4, y, w - 8, g_store.top_ram[i], /*ram_view=*/true);
        y += 14;
      }
    }
  }
}
