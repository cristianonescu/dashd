#include <Arduino.h>
#include "lgfx_panel.h"

#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"

void render_calendar(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }
  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 8;
  char buf[40];

  if (visibility::shown("cal.countdown")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "next event in", x, y, theme::ROLE_LABEL);
    y += 18;

    int m = g_store.cal_next_event_in_min;
    uint16_t color = THEME_FG;
    if (m >= 0) {
      if (m <= 5) color = THEME_CRIT;
      else if (m <= 15) color = THEME_WARN;
      else color = THEME_GOOD;
      if (m < 60) snprintf(buf, sizeof(buf), "%dm", m);
      else        snprintf(buf, sizeof(buf), "%dh %02dm", m / 60, m % 60);
    } else {
      snprintf(buf, sizeof(buf), "--");
      color = THEME_DIM;
    }
    tft.setTextColor(color, THEME_BG);
    draw_role(tft, buf, x, y, theme::ROLE_BIG);
    y += 60;
  }

  if (visibility::shown("cal.title")) {
    const char *t = g_store.cal_next_event_title[0] ? g_store.cal_next_event_title : "(nothing scheduled)";
    tft.setTextColor(THEME_FG, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, t, x, y, theme::ROLE_VALUE);
    y += 36;
  }

  if (visibility::shown("cal.today_remaining")) {
    tft.setTextColor(THEME_DIM, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "today remaining", x, y, theme::ROLE_LABEL);
    if (g_store.cal_today_remaining >= 0)
      snprintf(buf, sizeof(buf), "%d", g_store.cal_today_remaining);
    else
      snprintf(buf, sizeof(buf), "--");
    tft.setTextColor(THEME_ACCENT, THEME_BG);
    tft.setTextDatum(TR_DATUM);
    draw_role(tft, buf, x + w, y - 4, theme::ROLE_BIG);
  }
}
