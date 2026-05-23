#include <Arduino.h>
#include "lgfx_panel.h"

#include "config.h"
#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

// Home is a 2-column tile grid. Hidden tiles leave gaps in their original
// slots — the grid stays predictable rather than reflowing into a different
// shape on every toggle. Other pages (System / Tips) do flow.

static void tile(LGFX &tft, int x, int y, int w, int h, const char *label, const char *value, uint16_t accent) {
  // Apple-style elevated card: filled surface + soft hairline border, no
  // hard 1-px outline. A small colored bullet to the left of the label
  // identifies the category without the heavy left stripe of the older look.
  tft.fillRoundRect(x, y, w, h, 10, THEME_SURFACE);
  tft.drawRoundRect(x, y, w, h, 10, THEME_HAIRLINE);

  // Category bullet
  tft.fillCircle(x + 10, y + 10, 3, accent);

  tft.setTextColor(THEME_DIM, THEME_SURFACE);
  tft.setTextDatum(TL_DATUM);
  draw_role(tft, label, x + 20, y + 6, theme::ROLE_LABEL);
  tft.setTextColor(THEME_FG, THEME_SURFACE);
  draw_role(tft, value, x + 10, y + 22, theme::ROLE_VALUE);
}

void render_home(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }

  const int margin = 8;
  const int tile_w = (tft.width() - margin * 3) / 2;
  const int tile_h = 62;
  int y0 = theme::title_h() + margin;

  char buf[24];

  if (visibility::shown("home.cpu")) {
    int cpu_avg = 0;
    if (g_store.cpu_count > 0) {
      int sum = 0;
      for (int i = 0; i < g_store.cpu_count; i++) sum += g_store.cpu_pct[i];
      cpu_avg = sum / g_store.cpu_count;
    }
    snprintf(buf, sizeof(buf), "%d%%", cpu_avg);
    tile(tft, margin,                 y0,                  tile_w, tile_h, "CPU", buf, THEME_ACCENT);
  }

  if (visibility::shown("home.ram")) {
    if (g_store.ram_pct >= 0) snprintf(buf, sizeof(buf), "%d%%", g_store.ram_pct);
    else                      snprintf(buf, sizeof(buf), "--");
    tile(tft, margin * 2 + tile_w,    y0,                  tile_w, tile_h, "RAM", buf, THEME_ACCENT);
  }

  if (visibility::shown("home.ai")) {
    if (!isnan(g_store.cc_cost_today_usd))
      snprintf(buf, sizeof(buf), "$%.0f", g_store.cc_cost_today_usd);
    else snprintf(buf, sizeof(buf), "$--");
    tile(tft, margin,                 y0 + tile_h + margin, tile_w, tile_h, "AI $",  buf, THEME_WARN);
  }

  if (visibility::shown("home.git")) {
    if (g_store.git_commits_today >= 0)
      snprintf(buf, sizeof(buf), "%dc", g_store.git_commits_today);
    else snprintf(buf, sizeof(buf), "--");
    tile(tft, margin * 2 + tile_w,    y0 + tile_h + margin, tile_w, tile_h, "Git",   buf, THEME_GOOD);
  }

  if (visibility::shown("home.prs")) {
    if (g_store.gh_prs_awaiting_review >= 0)
      snprintf(buf, sizeof(buf), "%d", g_store.gh_prs_awaiting_review);
    else snprintf(buf, sizeof(buf), "--");
    tile(tft, margin,                 y0 + (tile_h + margin) * 2, tile_w, tile_h, "PRs", buf, THEME_ACCENT);
  }

  if (visibility::shown("home.msgs")) {
    int total_msgs = 0;
    bool any = false;
    if (g_store.msg_email_unread    >= 0) { total_msgs += g_store.msg_email_unread;    any = true; }
    if (g_store.msg_imessage_unread >= 0) { total_msgs += g_store.msg_imessage_unread; any = true; }
    if (any) snprintf(buf, sizeof(buf), "%d", total_msgs);
    else     snprintf(buf, sizeof(buf), "--");
    tile(tft, margin * 2 + tile_w,    y0 + (tile_h + margin) * 2, tile_w, tile_h, "Msgs", buf, THEME_CRIT);
  }
}
