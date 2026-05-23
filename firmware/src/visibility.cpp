#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

#include "visibility.h"

// Cap on simultaneously-hidden elements. Each element costs 4 bytes (a
// uint32 hash). 64 is plenty — the UI catalog has ~33 elements total.
static constexpr int MAX_HIDDEN = 64;

static uint32_t s_hidden[MAX_HIDDEN];
static int      s_hidden_n = 0;

static Preferences s_prefs;
static bool s_open = false;

static void ensure_prefs() {
  if (!s_open) { s_prefs.begin("dashd-vis", false); s_open = true; }
}

static void persist() {
  ensure_prefs();
  s_prefs.putBytes("h", s_hidden, sizeof(uint32_t) * s_hidden_n);
  s_prefs.putUChar("n", (uint8_t)s_hidden_n);
}

namespace visibility {

uint32_t fnv1a(const char *s) {
  uint32_t h = 2166136261u;
  while (*s) {
    h ^= (uint8_t)*s++;
    h *= 16777619u;
  }
  return h;
}

static int find_index(uint32_t hash) {
  for (int i = 0; i < s_hidden_n; i++) if (s_hidden[i] == hash) return i;
  return -1;
}

bool shown(const char *id) {
  if (!id || !*id) return true;
  return find_index(fnv1a(id)) < 0;
}

void set_hidden(const char *id, bool hidden) {
  if (!id || !*id) return;
  uint32_t h = fnv1a(id);
  int idx = find_index(h);
  if (hidden) {
    if (idx >= 0) return;             // already hidden
    if (s_hidden_n >= MAX_HIDDEN) return;
    s_hidden[s_hidden_n++] = h;
  } else {
    if (idx < 0) return;              // already visible
    // O(N) shrink by swapping the last element into the freed slot.
    s_hidden[idx] = s_hidden[s_hidden_n - 1];
    s_hidden_n--;
  }
  persist();
}

void clear_all() {
  s_hidden_n = 0;
  persist();
}

size_t hidden_count() { return (size_t)s_hidden_n; }

uint32_t hidden_at(size_t i) {
  return (i < (size_t)s_hidden_n) ? s_hidden[i] : 0;
}

void begin() {
  ensure_prefs();
  uint8_t n = s_prefs.getUChar("n", 0);
  if (n > MAX_HIDDEN) n = MAX_HIDDEN;
  s_hidden_n = (int)n;
  if (n > 0) {
    s_prefs.getBytes("h", s_hidden, sizeof(uint32_t) * n);
  }
}

}  // namespace visibility
