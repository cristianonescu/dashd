#include <Arduino.h>
#include "lgfx_panel.h"

#include "data_store.h"
#include "pages.h"
#include "pet_widget.h"
#include "theme.h"
#include "transitions.h"

// ------------- Internal state -------------

static bool          s_active = false;
static TransitionKind s_kind  = TRANSITION_FORWARD;
static int           s_from_slot = 0;
static int           s_to_slot   = 0;
static uint32_t      s_start_ms  = 0;
static int           s_last_wipe_x = -1;
static int           s_last_zoom_band = -1;

// Tunables.
static constexpr uint32_t DURATION_WIPE_MS = 600;
static constexpr uint32_t DURATION_ZOOM_MS = 700;

// ------------- API -------------

bool transition_active() { return s_active; }

void transition_begin(int from_slot, int to_slot, TransitionKind kind) {
  s_active = true;
  s_kind = kind;
  s_from_slot = from_slot;
  s_to_slot   = to_slot;
  s_start_ms  = millis();
  s_last_wipe_x = -1;
  s_last_zoom_band = -1;

  // Hand the pet a temporary anim that fits the transition.
  const char *anim = "run_right";
  if (kind == TRANSITION_BACKWARD)      anim = "run_left";
  else if (kind == TRANSITION_HOME)     anim = "jump";

  int pet_y = (theme::title_h() + 8);
  if (pet_frame_h() > 0) {
    pet_y = (theme::title_h() + (240 - theme::title_h() - theme::footer_h()) / 2)
            - pet_frame_h() / 2;
  }
  pet_begin_transition_at(kind == TRANSITION_BACKWARD ? 200 : 0, pet_y, anim);
}

// Forward / Backward wipe: a vertical column of THEME_BG sweeps across the
// content area, with the pet riding its leading edge. After the column
// crosses the screen, we paint the new page in its place.
static bool tick_horizontal_wipe(LGFX &tft, bool forward) {
  uint32_t now = millis();
  uint32_t elapsed = now - s_start_ms;
  if (elapsed > DURATION_WIPE_MS) elapsed = DURATION_WIPE_MS;
  float t = (float)elapsed / DURATION_WIPE_MS;

  const int top = theme::title_h();
  const int bottom = tft.height() - theme::footer_h();
  const int content_h = bottom - top;

  // Pet height for centering the wipe column.
  int pw = pet_frame_w();
  int ph = pet_frame_h();
  if (pw <= 0) pw = 48;
  if (ph <= 0) ph = 48;

  // Leading edge X, including the pet width so the BG column is wide enough.
  int screen_w = tft.width();
  int total_travel = screen_w + pw;
  int x_lead;
  if (forward) {
    // Move from -pw to screen_w.
    x_lead = (int)(-pw + t * total_travel);
  } else {
    // Move from screen_w to -pw.
    x_lead = (int)(screen_w - t * total_travel);
  }

  // Wipe column: BG strip from (x_lead - reveal_w/2) for some width. Choose
  // a band roughly the width of the pet so the leading edge "feels" tied
  // to the pet body.
  int band_w = pw + 8;
  int band_x = forward ? (x_lead - band_w) : x_lead;
  // Clamp so we don't bleed into the title bar or footer.
  if (band_x < 0) band_x = 0;
  if (band_x + band_w > screen_w) band_w = screen_w - band_x;

  // 1) Paint the BG band — erases the old page in front of the pet.
  if (band_w > 0) {
    tft.fillRect(band_x, top, band_w, content_h, THEME_BG);
  }

  // 2) Behind the leading edge, reveal the new page progressively. We can't
  //    truly partial-render a page renderer, so instead we paint THE WHOLE
  //    NEW PAGE on the last frame; during the wipe we only show BG behind
  //    the pet. This keeps the visual simple and the timing predictable.

  // 3) Pet at the leading edge, vertically centered in the content area.
  int pet_x = forward ? (x_lead - pw) : x_lead;
  if (pet_x < 0) pet_x = 0;
  if (pet_x + pw > screen_w) pet_x = screen_w - pw;
  int pet_y = top + (content_h - ph) / 2;
  pet_set_transition_pos(pet_x, pet_y);
  pet_render_now(tft);

  if (elapsed >= DURATION_WIPE_MS) {
    // Final: full-screen new page draw, then return pet to its corner.
    tft.fillRect(0, top, screen_w, content_h, THEME_BG);
    const PageEntry &p = kPages[s_to_slot];
    draw_title_bar(tft, p.name, g_store.ever_received,
                   !g_store.host_alive(now, /*stale*/ 10000));
    p.render(tft, true);
    draw_footer(tft, s_to_slot, PAGE_COUNT, g_store.last_state_ms);
    pet_end_transition();
    s_active = false;
    return true;
  }
  return false;
}

// Home transition: old page shrinks (centered), new page expands. Pet jumps
// in the centre. Visually a "we're going home" focus pull.
static bool tick_home_zoom(LGFX &tft) {
  uint32_t now = millis();
  uint32_t elapsed = now - s_start_ms;
  if (elapsed > DURATION_ZOOM_MS) elapsed = DURATION_ZOOM_MS;
  float t = (float)elapsed / DURATION_ZOOM_MS;

  const int top = theme::title_h();
  const int bottom = tft.height() - theme::footer_h();
  const int content_h = bottom - top;
  const int W = tft.width();

  // Two bands collapse toward the centre during the first half, then
  // expand outward during the second half (covering the new page area).
  // The pet jumps in the middle.
  float u = t < 0.5f ? (t * 2.0f) : (1.0f - (t - 0.5f) * 2.0f);
  int band_w = (int)(W * 0.5f * u);

  // Paint two bands, one on the left, one on the right, of THEME_BG.
  if (band_w > 0) {
    tft.fillRect(0, top, band_w, content_h, THEME_BG);
    tft.fillRect(W - band_w, top, band_w, content_h, THEME_BG);
  }

  // Pet at the centre, jumping with vertical bounce.
  int pw = pet_frame_w(); if (pw <= 0) pw = 48;
  int ph = pet_frame_h(); if (ph <= 0) ph = 48;
  int pet_y_centre = top + (content_h - ph) / 2;
  // Bounce: sin-ish parabola, dip at the midpoint.
  int bounce = (int)(-24.0f * (4.0f * t * (1.0f - t)));  // 0 at edges, -24 mid
  int pet_x = (W - pw) / 2;
  int pet_y = pet_y_centre + bounce;
  pet_set_transition_pos(pet_x, pet_y);
  pet_render_now(tft);

  if (elapsed >= DURATION_ZOOM_MS) {
    tft.fillRect(0, top, W, content_h, THEME_BG);
    const PageEntry &p = kPages[s_to_slot];
    draw_title_bar(tft, p.name, g_store.ever_received,
                   !g_store.host_alive(now, 10000));
    p.render(tft, true);
    draw_footer(tft, s_to_slot, PAGE_COUNT, g_store.last_state_ms);
    pet_end_transition();
    s_active = false;
    return true;
  }
  return false;
}

bool transition_tick(LGFX &tft) {
  if (!s_active) return false;
  switch (s_kind) {
    case TRANSITION_FORWARD:  return tick_horizontal_wipe(tft, /*forward=*/true);
    case TRANSITION_BACKWARD: return tick_horizontal_wipe(tft, /*forward=*/false);
    case TRANSITION_HOME:     return tick_home_zoom(tft);
  }
  s_active = false;
  return true;
}
