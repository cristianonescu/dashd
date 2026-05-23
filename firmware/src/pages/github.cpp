#include <Arduino.h>
#include "lgfx_panel.h"

#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

static void big_count(LGFX &tft, int x, int y, int v, const char *label, uint16_t accent) {
  char buf[16];
  if (v < 0) snprintf(buf, sizeof(buf), "--");
  else       snprintf(buf, sizeof(buf), "%d", v);

  tft.setTextColor(THEME_DIM, THEME_BG);
  tft.setTextDatum(TL_DATUM);
  draw_role(tft, label, x, y, theme::ROLE_LABEL);
  tft.setTextColor(v > 0 ? accent : THEME_DIM, THEME_BG);
  draw_role(tft, buf, x, y + 18, theme::ROLE_BIG);
}

void render_github(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }
  const int x = 12;
  int y = theme::title_h() + 8;

  if (visibility::shown("github.prs")) {
    big_count(tft, x, y, g_store.gh_prs_awaiting_review, "PRs awaiting review", THEME_ACCENT);
    y += 80;
  }
  if (visibility::shown("github.ci")) {
    big_count(tft, x, y, g_store.gh_ci_failures_24h, "CI failures (24h)", THEME_CRIT);
    y += 80;
  }
  if (visibility::shown("github.notifs")) {
    big_count(tft, x, y, g_store.gh_unread_notifications, "Notifications", THEME_WARN);
  }
}
