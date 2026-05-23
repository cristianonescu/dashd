#include <Arduino.h>
#include "lgfx_panel.h"
#include <math.h>

#include "data_store.h"
#include "pages.h"
#include "theme.h"
#include "visibility.h"
#include "widgets.h"

static void fmt_tokens(char *buf, size_t n, long t) {
  if (t < 0)              snprintf(buf, n, "--");
  else if (t < 1000)      snprintf(buf, n, "%ld", t);
  else if (t < 1000000)   snprintf(buf, n, "%.1fk", t / 1000.0);
  else                    snprintf(buf, n, "%.2fM", t / 1000000.0);
}

static void fmt_hm(char *buf, size_t n, int minutes) {
  if (minutes < 0)        snprintf(buf, n, "--");
  else if (minutes < 60)  snprintf(buf, n, "%dm", minutes);
  else                    snprintf(buf, n, "%dh %02dm", minutes / 60, minutes % 60);
}

void render_ai_spend(LGFX &tft, bool full) {
  if (full) {
    tft.fillRect(0, theme::title_h(), tft.width(),
                 tft.height() - theme::title_h() - theme::footer_h(), THEME_BG);
  }

  const int x = 8;
  const int w = tft.width() - x * 2;
  int y = theme::title_h() + 6;
  char buf[64];

  if (visibility::shown("ai.claude")) {
    tft.setTextColor(THEME_ACCENT, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, "Claude Code", x, y, theme::ROLE_LABEL);
    y += 18;

    if (!isnan(g_store.cc_cost_today_usd))
      snprintf(buf, sizeof(buf), "$%.2f", g_store.cc_cost_today_usd);
    else snprintf(buf, sizeof(buf), "$--");
    tft.setTextColor(THEME_FG, THEME_BG);
    tft.setTextDatum(TL_DATUM);
    draw_role(tft, buf, x, y, theme::ROLE_BIG);
    y += 50;

    fmt_tokens(buf, sizeof(buf), g_store.cc_tokens_today);
    char line[48]; snprintf(line, sizeof(line), "%s tok today", buf);
    tft.setTextColor(THEME_DIM, THEME_BG);
    draw_role(tft, line, x, y, theme::ROLE_LABEL);
    y += 18;

    char op[16], so[16], ha[16];
    fmt_tokens(op, sizeof(op), g_store.cc_tokens_opus);
    fmt_tokens(so, sizeof(so), g_store.cc_tokens_sonnet);
    fmt_tokens(ha, sizeof(ha), g_store.cc_tokens_haiku);
    snprintf(buf, sizeof(buf), "O %s  S %s  H %s", op, so, ha);
    tft.setTextColor(THEME_DIM, THEME_BG);
    draw_role(tft, buf, x, y, theme::ROLE_LABEL);
    y += 14;

    // ─ 5h rate-limit block. Two metrics when both are available:
    //    1. Time elapsed (block_elapsed_pct) — always present once we
    //       see activity in this window. Labelled "elapsed" to avoid
    //       misreading it as "% of tokens used".
    //    2. Token quota used (block_used_pct) — only when the user has
    //       configured DASHD_CLAUDE_BLOCK_BUDGET. Labelled "used".
    tft.setTextColor(THEME_FG, THEME_BG);
    draw_role(tft, "5h block", x, y, theme::ROLE_LABEL);
    if (g_store.cc_block_resets_in_min >= 0) {
      fmt_hm(buf, sizeof(buf), g_store.cc_block_resets_in_min);
      char tail[40]; snprintf(tail, sizeof(tail), "resets %s", buf);
      tft.setTextDatum(TR_DATUM);
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, tail, x + w, y, theme::ROLE_LABEL);
      tft.setTextDatum(TL_DATUM);
    }
    y += 18;

    // Time-elapsed row.
    if (g_store.cc_block_elapsed_pct >= 0) {
      snprintf(buf, sizeof(buf), "elapsed %d%%", g_store.cc_block_elapsed_pct);
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, buf, x, y, theme::ROLE_LABEL);
    }
    y += 14;
    widgets::hbar(tft, x, y, w, 8, g_store.cc_block_elapsed_pct);
    y += 12;

    // Token-quota row — only when a budget is configured.
    if (g_store.cc_block_used_pct >= 0) {
      snprintf(buf, sizeof(buf), "used %d%%", g_store.cc_block_used_pct);
      tft.setTextColor(THEME_DIM, THEME_BG);
      draw_role(tft, buf, x, y, theme::ROLE_LABEL);
      y += 14;
      widgets::hbar(tft, x, y, w, 8, g_store.cc_block_used_pct);
      y += 12;

      // Burn-rate projection — "hits cap in 23m" / "hits cap in 2h 14m".
      if (g_store.cc_burn_projected_cap_min >= 0) {
        char hm[24]; fmt_hm(hm, sizeof(hm), g_store.cc_burn_projected_cap_min);
        snprintf(buf, sizeof(buf), "hits cap in %s", hm);
        tft.setTextColor(THEME_WARN, THEME_BG);
        draw_role(tft, buf, x, y, theme::ROLE_LABEL);
        y += 16;
      }
    }
  }

  if (visibility::shown("ai.codex")) {
    if (visibility::shown("ai.claude")) {
      tft.drawFastHLine(x, y, w, THEME_DIM);
      y += 4;
    }
    tft.setTextColor(THEME_ACCENT, THEME_BG);
    draw_role(tft, "Codex", x, y, theme::ROLE_LABEL);
    if (g_store.cx_session_active == 1) {
      tft.fillCircle(x + w - 6, y + 8, 4, THEME_GOOD);
    } else if (g_store.cx_session_active == 0) {
      tft.drawCircle(x + w - 6, y + 8, 4, THEME_DIM);
    }
    y += 20;

    // tokens_today is finally real for Codex thanks to the cumulative
    // → delta diff in agent/dashd/collectors/codex.py.
    fmt_tokens(buf, sizeof(buf), g_store.cx_tokens_today);
    char line[48]; snprintf(line, sizeof(line), "%s tok today", buf);
    tft.setTextColor(THEME_DIM, THEME_BG);
    draw_role(tft, line, x, y, theme::ROLE_LABEL);
    y += 18;

    // Codex's used_percent IS the actual quota usage (not time-elapsed),
    // so we label it "used" instead of "block".
    if (g_store.cx_block_used_pct >= 0)
      snprintf(buf, sizeof(buf), "used %d%%", g_store.cx_block_used_pct);
    else snprintf(buf, sizeof(buf), "used --");
    tft.setTextColor(THEME_FG, THEME_BG);
    draw_role(tft, buf, x, y, theme::ROLE_LABEL);
    y += 18;
    widgets::hbar(tft, x, y, w, 10, g_store.cx_block_used_pct);
    y += 14;
    if (g_store.cx_block_resets_in_min >= 0) {
      char hm[24]; fmt_hm(hm, sizeof(hm), g_store.cx_block_resets_in_min);
      snprintf(buf, sizeof(buf), "resets in %s", hm);
    } else snprintf(buf, sizeof(buf), "--");
    tft.setTextColor(THEME_DIM, THEME_BG);
    draw_role(tft, buf, x, y, theme::ROLE_LABEL);
  }
}
