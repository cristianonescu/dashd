#pragma once
#include "lgfx_panel.h"

namespace widgets {

// Horizontal progress bar with a colored fill based on `pct` and thresholds.
void hbar(LGFX &tft, int x, int y, int w, int h, int pct,
          int warn_at = 70, int crit_at = 90);

// Small label/value pair (left-aligned label, right-aligned value).
void kv(LGFX &tft, int x, int y, int w, const char *label, const char *value);

}
