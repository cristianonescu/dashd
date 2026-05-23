#pragma once
#include <stdint.h>

// Default 16-bit (RGB565) palette. UI-driven overrides land in DataStore::theme_*
// and are read via theme::get_*() so any color used in a draw call reflects
// the latest server-pushed value (with a sensible compile-time fallback).

namespace theme {

namespace defaults {
  // Apple-inspired dark palette (RGB565). Pure-black BG looks great on IPS
  // and gives the colored accents max pop.
  constexpr uint16_t BG       = 0x0000;  // #000000 true black
  constexpr uint16_t SURFACE  = 0x18E3;  // #1C1C1E elevated surface (tiles)
  constexpr uint16_t HAIRLINE = 0x39C7;  // #38383A 1-pt separator
  constexpr uint16_t FG       = 0xF79E;  // #F2F2F7 near-white system label
  constexpr uint16_t DIM      = 0x8C72;  // #8E8E93 system secondary
  constexpr uint16_t GOOD     = 0x368B;  // #30D158 system green
  constexpr uint16_t WARN     = 0xFCE1;  // #FF9F0A system orange
  constexpr uint16_t CRIT     = 0xFA27;  // #FF453A system red
  constexpr uint16_t ACCENT   = 0x0C3F;  // #0A84FF system blue

  constexpr int TITLE_H_DEFAULT  = 22;
  constexpr int FOOTER_H_DEFAULT = 14;
}

// Color accessors — return the override (>0) or the compile-time default.
uint16_t bg();
uint16_t fg();
uint16_t dim();
uint16_t good();
uint16_t warn();
uint16_t crit();
uint16_t accent();
uint16_t surface();   // elevated tile fill
uint16_t hairline();  // subtle separator

// Thresholds.
int cpu_warn_pct();
int cpu_crit_pct();
int ram_warn_pct();
int ram_crit_pct();
int calendar_soon_min();
int commit_fresh_min();

// Title bar + footer.
bool title_visible();
bool footer_visible();
int  title_h();    // 0 when hidden
int  footer_h();   // 0 when hidden

// Semantic text roles. Each role has a fixed BASE font number (matching the
// LovyanGFX numbered fonts) plus a 1..4 scale the user can adjust live.
enum Role { ROLE_TITLE, ROLE_LABEL, ROLE_VALUE, ROLE_BIG };

uint8_t text_scale(Role r);   // clamped 1..4
uint8_t base_font(Role r);    // fixed
uint8_t rotation();           // clamped 0..3

// Convenience macros so existing draw sites stay compact.
#define THEME_BG       (theme::bg())
#define THEME_FG       (theme::fg())
#define THEME_DIM      (theme::dim())
#define THEME_GOOD     (theme::good())
#define THEME_WARN     (theme::warn())
#define THEME_CRIT     (theme::crit())
#define THEME_ACCENT   (theme::accent())
#define THEME_SURFACE  (theme::surface())
#define THEME_HAIRLINE (theme::hairline())

} // namespace theme

// One-call wrapper: applies the user's scale for the role, draws the text
// using its base font, and resets the scale to 1. Pages should funnel almost
// all text through this so it stays customizable.
class LGFX;
void draw_role(LGFX &tft, const char *text, int x, int y, theme::Role role);
