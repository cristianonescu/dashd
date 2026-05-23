#pragma once
#include <stdint.h>
#include <stddef.h>

class LGFX;

// Pet overlay: small animated sprite drawn on top of every page, in one of
// four corners. Frames come from either the firmware-bundled default
// (default_pet.h) or a user-installed .dpet stored in LittleFS.
//
// State (enable / corner / current animation / active pet slug) persists in
// NVS so it survives reboots.

enum PetCorner : uint8_t {
  PET_CORNER_TR = 0,   // top-right
  PET_CORNER_BR = 1,   // bottom-right (default)
  PET_CORNER_BL = 2,   // bottom-left
  PET_CORNER_TL = 3,   // top-left
};

void pet_begin();
void pet_tick(LGFX &tft);
bool pet_overlap(int x, int y, int w, int h);

// Cmd-driven state.
void pet_set_enabled(bool en);
void pet_set_corner(PetCorner c);
void pet_set_state(const char *name);
bool pet_enabled();
PetCorner pet_corner();
const char *pet_state_name();
const char *pet_active_slug();    // "default" or installed slug

// .dpet install. The host streams the binary in chunks; each chunk is
// concatenated into a LittleFS file at /pets/<slug>.dpet. After install_end()
// validates the header, the pet becomes the active source.
bool pet_install_start(const char *slug, size_t total_bytes);
bool pet_install_chunk(uint32_t seq, const uint8_t *data, size_t len);
bool pet_install_end();
// True while a host-driven install stream is in progress (between
// pet_install_start and pet_install_end). The main loop pauses
// auto-advance during installs so the display doesn't churn while
// chunks are being streamed.
bool pet_install_active();

// Switch to a previously-installed pet (by slug) or back to the default
// ("default" or "" → embedded Claw'd).
bool pet_set_active(const char *slug);

// Remove an installed pet's file (no-op if not present).
bool pet_remove(const char *slug);

// Transition override: while active, the pet is drawn at (x, y) instead of
// its normal corner. The transition module uses this so the pet "rides"
// the wipe between pages. clear() restores corner placement.
void pet_begin_transition_at(int x, int y, const char *anim_state);
void pet_set_transition_pos(int x, int y);
void pet_end_transition();
bool pet_in_transition();

// Renders the pet WITHOUT advancing its animation timer. Used by the
// transition compositor to paint the pet on every wipe-step regardless of
// how recently the main loop drew a frame.
void pet_render_now(LGFX &tft);

// Geometry / state accessors used by the transition compositor.
int  pet_frame_w();
int  pet_frame_h();
