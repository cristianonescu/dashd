#include <Arduino.h>
#include "lgfx_panel.h"

#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

static uint16_t freshness_color(int minutes) {
  if (minutes < 0) return THEME_DIM;
  if (minutes <= 30) return THEME_GOOD;
  if (minutes <= 120) return THEME_WARN;
  return THEME_CRIT;
}

void render_dev_flow(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 6;
  char buf[64];

  if (visibility::shown("dev.branch")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "branch", x, y, theme::ROLE_LABEL);
    y += 12;
    tft.setTextColor(THEME_ACCENT, THEME_BG);
    const char *br = g_store.git_branch[0] ? g_store.git_branch : "--";
    draw_role(tft, br, x, y, theme::ROLE_VALUE);
    y += 32;
  }

  if (visibility::shown("dev.commits")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "commits today", x, y, theme::ROLE_LABEL);
    if (g_store.git_commits_today >= 0)
      snprintf(buf, sizeof(buf), "%d", g_store.git_commits_today);
    else
      snprintf(buf, sizeof(buf), "--");
    tft.setTextColor(THEME_FG, THEME_BG);
    tft.setTextDatum(TR_DATUM);
    draw_role(tft, buf, x + w, y - 2, theme::ROLE_BIG);
    y += 38;
  }

  if (visibility::shown("dev.loc")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "LOC today", x, y, theme::ROLE_LABEL);
    y += 18;
    if (g_store.git_loc_added >= 0) {
      snprintf(buf, sizeof(buf), "+%d", g_store.git_loc_added);
      tft.setTextColor(THEME_GOOD, THEME_BG);
      draw_role(tft, buf, x, y, theme::ROLE_VALUE);
      snprintf(buf, sizeof(buf), "-%d", g_store.git_loc_removed);
      tft.setTextColor(THEME_CRIT, THEME_BG);
      tft.setTextDatum(TR_DATUM);
      draw_role(tft, buf, x + w, y, theme::ROLE_VALUE);
    } else {
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, "--", x, y, theme::ROLE_VALUE);
    }
    y += 32;
  }

  if (visibility::shown("dev.last_commit")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "since last commit", x, y, theme::ROLE_LABEL);
    y += 18;
    int m = g_store.git_minutes_since_last_commit;
    if (m < 0) snprintf(buf, sizeof(buf), "--");
    else if (m < 60) snprintf(buf, sizeof(buf), "%dm", m);
    else snprintf(buf, sizeof(buf), "%dh %dm", m / 60, m % 60);
    tft.setTextColor(freshness_color(m), THEME_BG);
    draw_role(tft, buf, x, y, theme::ROLE_BIG);
  }
}
