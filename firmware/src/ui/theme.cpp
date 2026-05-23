#include "lgfx_panel.h"
#include "theme.h"
#include "pages.h"
#include "data_store.h"

namespace theme {

uint16_t bg()     { return g_store.theme_bg     ? g_store.theme_bg     : defaults::BG; }
uint16_t fg()     { return g_store.theme_fg     ? g_store.theme_fg     : defaults::FG; }
uint16_t dim()    { return g_store.theme_dim    ? g_store.theme_dim    : defaults::DIM; }
uint16_t good()   { return g_store.theme_good   ? g_store.theme_good   : defaults::GOOD; }
uint16_t warn()   { return g_store.theme_warn   ? g_store.theme_warn   : defaults::WARN; }
uint16_t crit()   { return g_store.theme_crit   ? g_store.theme_crit   : defaults::CRIT; }
uint16_t accent() { return g_store.theme_accent ? g_store.theme_accent : defaults::ACCENT; }
uint16_t surface()  { return defaults::SURFACE;  }   // not user-overridable yet
uint16_t hairline() { return defaults::HAIRLINE; }   // not user-overridable yet

int cpu_warn_pct()      { return g_store.thr_cpu_warn  >= 0 ? g_store.thr_cpu_warn  : 70; }
int cpu_crit_pct()      { return g_store.thr_cpu_crit  >= 0 ? g_store.thr_cpu_crit  : 90; }
int ram_warn_pct()      { return g_store.thr_ram_warn  >= 0 ? g_store.thr_ram_warn  : 80; }
int ram_crit_pct()      { return g_store.thr_ram_crit  >= 0 ? g_store.thr_ram_crit  : 95; }
int calendar_soon_min() { return g_store.thr_calendar_soon_min >= 0 ? g_store.thr_calendar_soon_min : 15; }
int commit_fresh_min()  { return g_store.thr_commit_fresh_min  >= 0 ? g_store.thr_commit_fresh_min  : 30; }

bool title_visible()  { return g_store.show_title  != 0; }   // -1 (default) or 1 → visible
bool footer_visible() { return g_store.show_footer != 0; }
int  title_h()        { return title_visible()  ? defaults::TITLE_H_DEFAULT  : 0; }
int  footer_h()       { return footer_visible() ? defaults::FOOTER_H_DEFAULT : 0; }

uint8_t text_scale(Role r) {
  int8_t v = 1;
  switch (r) {
    case ROLE_TITLE: v = g_store.scale_title; break;
    case ROLE_LABEL: v = g_store.scale_label; break;
    case ROLE_VALUE: v = g_store.scale_value; break;
    case ROLE_BIG:   v = g_store.scale_big;   break;
  }
  if (v <= 0) v = 1;
  if (v > 4)  v = 4;
  return (uint8_t)v;
}

uint8_t base_font(Role r) {
  switch (r) {
    case ROLE_TITLE: return 2;
    case ROLE_LABEL: return 1;
    case ROLE_VALUE: return 2;   // tile values, "kv" rows
    case ROLE_BIG:   return 6;   // big numbers
  }
  return 2;
}

uint8_t rotation() {
  int8_t v = g_store.rotation;
  if (v < 0 || v > 3) v = 0;
  return (uint8_t)v;
}

} // namespace theme

void draw_role(LGFX &tft, const char *text, int x, int y, theme::Role role) {
  tft.setTextSize(theme::text_scale(role));
  tft.drawString(text, x, y, theme::base_font(role));
  tft.setTextSize(1);
}

// ---------- chrome ----------

void draw_title_bar(LGFX &tft, const char *name, bool host_ok, bool stale) {
  if (!theme::title_visible()) return;
  // Title rendered as a subtle elevated band — no harsh underline, just a
  // 1-px hairline like macOS title bars.
  tft.fillRect(0, 0, tft.width(), theme::title_h(), THEME_BG);
  tft.setTextColor(THEME_FG, THEME_BG);
  tft.setTextDatum(TL_DATUM);
  draw_role(tft, name, 8, 4, theme::ROLE_TITLE);

  // Connection indicator — flat colored pill in the top-right.
  int cx = tft.width() - 12;
  int cy = theme::title_h() / 2;
  if (host_ok && !stale) {
    tft.fillCircle(cx, cy, 3, THEME_GOOD);
  } else if (host_ok && stale) {
    tft.fillCircle(cx, cy, 3, THEME_WARN);
  } else {
    tft.fillCircle(cx, cy, 3, THEME_CRIT);
  }
  tft.drawFastHLine(0, theme::title_h() - 1, tft.width(), THEME_HAIRLINE);
}

void draw_footer(LGFX &tft, int page_idx, int page_count, uint32_t last_state_ms) {
  if (!theme::footer_visible()) return;
  int y = tft.height() - theme::footer_h();
  tft.fillRect(0, y, tft.width(), theme::footer_h(), THEME_BG);
  tft.drawFastHLine(0, y, tft.width(), THEME_HAIRLINE);
  tft.setTextColor(THEME_DIM, THEME_BG);
  tft.setTextDatum(TL_DATUM);

  char buf[32];
  if (last_state_ms == 0) {
    snprintf(buf, sizeof(buf), "no host");
  } else {
    uint32_t age = (millis() - last_state_ms) / 1000;
    snprintf(buf, sizeof(buf), "upd %lus ago", (unsigned long)age);
  }
  tft.drawString(buf, 4, y + 2, 1);

  snprintf(buf, sizeof(buf), "%d/%d", page_idx + 1, page_count);
  tft.setTextDatum(TR_DATUM);
  tft.drawString(buf, tft.width() - 4, y + 2, 1);
}
