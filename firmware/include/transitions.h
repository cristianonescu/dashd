#pragma once
#include <stdint.h>

class LGFX;

// Cool page-to-page transition effects. The pet rides the wipe so each
// transition feels driven by the pet, not just a mechanical swap.
//
//   FORWARD : new page coming from the right; pet runs left→right.
//   BACKWARD: new page coming from the left;  pet runs right→left.
//   HOME    : zoom out — old page collapses to centre, then the new page
//             expands. Pet jumps in the middle.

enum TransitionKind : uint8_t {
  TRANSITION_FORWARD = 0,
  TRANSITION_BACKWARD,
  TRANSITION_HOME,
};

// Begin a transition. The page contents are drawn directly to the display
// step by step — we never need a second framebuffer.
void transition_begin(int from_slot, int to_slot, TransitionKind kind);

// Returns true while a transition is animating. main.cpp gates its normal
// page redraw on this so we don't fight the compositor.
bool transition_active();

// Advance one frame of the transition. Returns true when the transition
// finished on this call. Safe to call repeatedly.
bool transition_tick(LGFX &tft);
