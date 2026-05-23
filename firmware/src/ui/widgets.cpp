#include "widgets.h"
#include "theme.h"

namespace widgets {

void hbar(LGFX &tft, int x, int y, int w, int h, int pct, int warn_at, int crit_at) {
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  uint16_t fg = THEME_GOOD;
  if (pct >= crit_at) fg = THEME_CRIT;
  else if (pct >= warn_at) fg = THEME_WARN;
  // Track: rounded surface, no outline (Apple look).
  int r = h / 2;
  tft.fillRoundRect(x, y, w, h, r, THEME_SURFACE);
  int fill = (w * pct) / 100;
  if (fill > 0) {
    // Ensure at least 2*r pixels wide for the rounded fill to render right.
    int fw = fill < (r * 2) ? r * 2 : fill;
    if (fw > w) fw = w;
    tft.fillRoundRect(x, y, fw, h, r, fg);
  }
}

void kv(LGFX &tft, int x, int y, int w, const char *label, const char *value) {
  tft.setTextColor(THEME_DIM, THEME_BG);
  tft.setTextDatum(TL_DATUM);
  tft.drawString(label, x, y, 2);
  tft.setTextColor(THEME_FG, THEME_BG);
  tft.setTextDatum(TR_DATUM);
  tft.drawString(value, x + w, y, 2);
}

}
