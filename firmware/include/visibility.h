#pragma once
#include <stdint.h>
#include <stddef.h>

// Per-element show/hide registry. Pages wrap each major info block in
// `if (visibility::shown("…"))` so users can declutter pages from the
// Electron app without touching the page table.
//
// IDs are short strings of the form "page.element" (e.g. "system.battery").
// Internally we only store FNV-1a hashes so the RAM footprint is tiny
// regardless of how many hidden items there are.

namespace visibility {

// True if the element is currently shown (i.e. not in the hidden set).
// Anything we haven't been told to hide is visible by default.
bool shown(const char *id);

// Toggle one element. `false` adds the hash to the hidden set, `true`
// removes it. Persists to NVS on every change.
void set_hidden(const char *id, bool hidden);

// Clear every override → everything visible again.
void clear_all();

// Internals so usb_link can stream the current state back to the host
// when the UI asks for it.
uint32_t fnv1a(const char *s);
size_t   hidden_count();
uint32_t hidden_at(size_t i);

// Called once on boot; restores the hidden set from NVS.
void begin();

}  // namespace visibility
