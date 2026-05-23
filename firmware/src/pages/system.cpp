// Deprecated page kept ONLY for back-compat with the v0.1.11 PageId
// numbering. PAGE_SYSTEM (id=1) is now masked out by page_enabled()
// (see firmware/src/main.cpp + PAGES_DEPRECATED_MASK in pages.h), so
// this render function is never invoked at runtime. The body stays as
// a no-op rather than being deleted so the kPages[] table layout
// doesn't shift — a v0.1.11 pmask/porder restored from NVS would
// otherwise resurrect a stub page in the wrong place.
//
// Content moved to: pages/cpu.cpp, pages/memory.cpp, pages/network.cpp.
// (Battery + CPU temperature now live on the Home page.)
#include <Arduino.h>
#include "lgfx_panel.h"
#include "pages.h"

void render_system(LGFX & /*tft*/, bool /*full*/) {
  // intentional no-op — page is hidden via PAGES_DEPRECATED_MASK.
}
