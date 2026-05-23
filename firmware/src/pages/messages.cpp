#include <Arduino.h>
#include "lgfx_panel.h"

#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"

// Row layout: a 2-column grid of channels. Each cell shows label + count.
// Disabled channels render with DIM color. Mentions (Slack) when present are
// shown as a small accent badge in the corner of the Slack cell.

static void cell(LGFX &tft, int x, int y, int w, int h,
                 const char *label, int count, uint16_t accent,
                 int badge = -1) {
  tft.drawRoundRect(x, y, w, h, 6, THEME_DIM);
  tft.fillRect(x + 1, y + 1, 4, h - 2, accent);

  tft.setTextColor(THEME_DIM, THEME_BG);
  tft.setTextDatum(TL_DATUM);
  draw_role(tft, label, x + 10, y + 4, theme::ROLE_LABEL);

  char buf[16];
  if (count < 0) snprintf(buf, sizeof(buf), "--");
  else           snprintf(buf, sizeof(buf), "%d", count);
  tft.setTextColor(count > 0 ? THEME_FG : THEME_DIM, THEME_BG);
  draw_role(tft, buf, x + 10, y + 16, theme::ROLE_BIG);

  if (badge > 0) {
    char b[8];
    snprintf(b, sizeof(b), "@%d", badge);
    tft.fillRoundRect(x + w - 28, y + 4, 24, 14, 4, THEME_CRIT);
    tft.setTextColor(THEME_FG, THEME_CRIT);
    tft.setTextDatum(MC_DATUM);
    draw_role(tft, b, x + w - 16, y + 11, theme::ROLE_LABEL);
    tft.setTextDatum(TL_DATUM);
  }
}

void render_messages(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }

  const int margin = 6;
  const int cell_w = (tft.width() - margin * 3) / 2;
  const int cell_h = 56;
  int y = theme::title_h() + margin;

  if (visibility::shown("msgs.email"))
    cell(tft, margin,              y,                       cell_w, cell_h, "Email",    g_store.msg_email_unread,    THEME_ACCENT);
  if (visibility::shown("msgs.imessage"))
    cell(tft, margin * 2 + cell_w, y,                       cell_w, cell_h, "iMessage", g_store.msg_imessage_unread, THEME_GOOD);

  if (visibility::shown("msgs.slack"))
    cell(tft, margin,              y + (cell_h + margin),   cell_w, cell_h, "Slack",    g_store.msg_slack_unread,    THEME_ACCENT,
         g_store.msg_slack_mentions > 0 ? g_store.msg_slack_mentions : -1);
  if (visibility::shown("msgs.teams"))
    cell(tft, margin * 2 + cell_w, y + (cell_h + margin),   cell_w, cell_h, "Teams",    g_store.msg_teams_unread,    THEME_WARN);

  if (visibility::shown("msgs.whatsapp"))
    cell(tft, margin,              y + (cell_h + margin) * 2, cell_w, cell_h, "WhatsApp", g_store.msg_whatsapp_unread, THEME_GOOD);
}
