#include <Arduino.h>
#include <FS.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <esp_partition.h>
#include <string.h>

#include "lgfx_panel.h"

#include "default_pet.h"
#include "pet_widget.h"
#include "theme.h"
#include "usb_link.h"  // LOGW/LOGE/LOGI

// .dpet header layout (matches agent/dashd/pets/converter.py).
static constexpr uint8_t  DPET_MAGIC[4] = {'D', 'P', 'E', 'T'};
static constexpr uint8_t  DPET_VERSION  = 1;
static constexpr int      NAME_FIELD_LEN = 16;
static constexpr int      MAX_ANIMS = 16;
static constexpr int      MAX_FRAME_PX = 64 * 64;

struct AnimEntry {
  char     name[NAME_FIELD_LEN + 1];
  uint16_t first_frame;
  uint16_t frame_count;
};

// Active pet source. Frames live either in PROGMEM (embedded default) or on
// LittleFS (read on demand into a small frame buffer). Never both.
struct PetSource {
  bool ok = false;
  bool embedded = true;
  uint16_t frame_w = 0;
  uint16_t frame_h = 0;
  uint16_t frame_count = 0;
  uint16_t anim_count = 0;
  AnimEntry anims[MAX_ANIMS];

  // Embedded path.
  const uint16_t *embedded_pixels = nullptr;
  const uint8_t  *embedded_masks  = nullptr;
  size_t mask_bytes_per_frame = 0;

  // LittleFS path. Header + anim table parsed at load time; frames are
  // streamed in lazily, one at a time, into frame_pixels/frame_mask.
  char fs_path[64] = {0};
  size_t fs_frames_offset = 0;
  int cached_frame_id = -1;
  uint8_t *frame_pixels = nullptr;
  uint8_t *frame_mask   = nullptr;
};

static PetSource s_src;

// -------- State (NVS persisted) --------
static bool       s_enabled = true;
static PetCorner  s_corner  = PET_CORNER_BR;
static int        s_anim_idx = 0;
static int        s_frame   = 0;
static uint32_t   s_last_advance_ms = 0;
static int        s_last_x = -1;
static int        s_last_y = -1;
static char       s_active_slug[24] = "default";

// Transition override (NOT persisted). While active, corner placement is
// ignored and the pet renders at (s_tr_x, s_tr_y) using the explicit anim.
static bool s_tr_active = false;
static int  s_tr_x = 0;
static int  s_tr_y = 0;
static int  s_tr_saved_anim_idx = 0;

static Preferences s_prefs;
static bool s_prefs_open = false;
static void ensure_prefs() {
  if (!s_prefs_open) { s_prefs.begin("dashd-pet", false); s_prefs_open = true; }
}

constexpr uint32_t FRAME_INTERVAL_MS = 120;

// ------- LittleFS helpers -------
// Pet installs use a LittleFS partition for storage. There are TWO label
// conventions we have to support:
//   - v0.1.2+ partitions.csv labels the partition "littlefs"
//   - v0.1.1 (the original release) labeled it "spiffs"
// OTA only updates the app slots, never the partition table — so users
// who first USB-flashed at v0.1.1 and have been auto-updating ever since
// STILL have the "spiffs" label on their flash, even on the latest
// firmware. We probe partitions to detect which label is actually
// present, then mount with that label. This is the v0.1.10/v0.1.11 fix —
// pet installs were silently failing on every device since v0.1.2
// because Arduino-ESP32's LittleFS.begin() defaults to "spiffs" but
// modern installs labeled the partition "littlefs", and earlier "fixes"
// only papered over the symptom for one of the two cases.
static constexpr const char *kLittleFSBasePath = "/littlefs";
static bool s_fs_ready = false;
static const char *s_fs_active_label = nullptr;
// Cache the "permanently broken FS" verdict so we stop retrying mount on
// every install attempt — once explicit format+begin has failed too,
// further attempts just produce more "LittleFS mount failed" log noise.
// Cleared by an explicit reboot (the only realistic recovery path).
static bool s_fs_unrecoverable = false;

// Probe the partition table for the LittleFS partition under both
// supported labels. Returns the label that exists on this device, or
// nullptr if neither does (which means the device needs a USB re-flash
// to get a current partition table — OTA can't fix this).
static const char *detect_littlefs_label() {
  // Modern label first (v0.1.2+ flash).
  const esp_partition_t *p = esp_partition_find_first(
      ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, "littlefs");
  if (p != nullptr) return "littlefs";
  // Legacy label (v0.1.1 flash — Arduino's default looked for this name).
  p = esp_partition_find_first(
      ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, "spiffs");
  if (p != nullptr) return "spiffs";
  return nullptr;
}

static bool fs_ensure() {
  if (s_fs_ready) return true;
  if (s_fs_unrecoverable) {
    LOGW("LittleFS still unrecoverable — power-cycle the device to retry");
    return false;
  }

  // Discover the actual partition label on this device's flash.
  const char *label = detect_littlefs_label();
  if (label == nullptr) {
    LOGE("LittleFS mount failed: no spiffs/littlefs partition in table — "
         "re-flash the device via USB to install the current partition layout");
    s_fs_unrecoverable = true;
    return false;
  }
  s_fs_active_label = label;
  LOGI("LittleFS using partition label '%s'", label);

  // First attempt: mount with format-on-fail. Handles a corrupt FS on a
  // present partition.
  if (LittleFS.begin(true /*format on fail*/, kLittleFSBasePath, 10, label)) {
    LittleFS.mkdir("/pets");
    s_fs_ready = true;
    return true;
  }
  LOGW("LittleFS mount failed (begin(true) on label '%s'); trying explicit format",
       label);

  // Second attempt: explicit format + begin(false). Catches the case
  // where Arduino's wrapped format-on-fail was a silent no-op. format()
  // uses the label cached by the most recent begin() call (which is
  // `label` since we just called begin with it).
  if (LittleFS.format() &&
      LittleFS.begin(false, kLittleFSBasePath, 10, label)) {
    LittleFS.mkdir("/pets");
    s_fs_ready = true;
    LOGI("LittleFS recovered via explicit format");
    return true;
  }

  // Diagnostic dump.
  const esp_partition_t *p = esp_partition_find_first(
      ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, label);
  if (p == nullptr) {
    LOGE("LittleFS mount failed: partition '%s' vanished mid-mount", label);
  } else {
    LOGE("LittleFS mount failed despite format: partition '%s' size=%u "
         "offset=0x%lx — flash may be bad", p->label, (unsigned)p->size,
         (unsigned long)p->address);
  }
  s_fs_unrecoverable = true;
  return false;
}

static void pet_path(const char *slug, char *out, size_t cap) {
  snprintf(out, cap, "/pets/%s.dpet", slug);
}

static void release_frame_buffers() {
  if (s_src.frame_pixels) { free(s_src.frame_pixels); s_src.frame_pixels = nullptr; }
  if (s_src.frame_mask)   { free(s_src.frame_mask);   s_src.frame_mask   = nullptr; }
  s_src.cached_frame_id = -1;
}

static void unload_source() {
  release_frame_buffers();
  s_src.ok = false;
  s_src.embedded_pixels = nullptr;
  s_src.embedded_masks = nullptr;
  s_src.frame_count = 0;
  s_src.anim_count = 0;
  s_src.fs_path[0] = 0;
  s_src.fs_frames_offset = 0;
}

static void load_embedded() {
  unload_source();
  s_src.ok = true;
  s_src.embedded = true;
  s_src.frame_w = PET_FRAME_W;
  s_src.frame_h = PET_FRAME_H;
  s_src.frame_count = PET_TOTAL_FRAMES;
  s_src.anim_count = (PET_ANIM_COUNT > MAX_ANIMS) ? MAX_ANIMS : PET_ANIM_COUNT;
  for (int i = 0; i < s_src.anim_count; i++) {
    strncpy(s_src.anims[i].name, PET_ANIMS[i].name, NAME_FIELD_LEN);
    s_src.anims[i].name[NAME_FIELD_LEN] = '\0';
    s_src.anims[i].first_frame = PET_ANIMS[i].first_frame;
    s_src.anims[i].frame_count = PET_ANIMS[i].frame_count;
  }
  s_src.embedded_pixels = PET_PIXELS;
  s_src.embedded_masks  = PET_MASKS;
  s_src.mask_bytes_per_frame = PET_MASK_BYTES;
}

// Parse a .dpet header on LittleFS without loading any frames into RAM.
static bool load_from_littlefs(const char *slug) {
  if (!fs_ensure()) return false;
  char path[64]; pet_path(slug, path, sizeof(path));
  if (!LittleFS.exists(path)) { LOGW("pet: %s not installed", slug); return false; }
  File f = LittleFS.open(path, "r");
  if (!f) { LOGE("pet: open %s failed", path); return false; }
  size_t fsize = f.size();
  if (fsize < 16) { f.close(); LOGE("pet: too small"); return false; }

  uint8_t hdr[16];
  if (f.read(hdr, 16) != 16) { f.close(); LOGE("pet: header read"); return false; }
  if (memcmp(hdr, DPET_MAGIC, 4) != 0) { f.close(); LOGE("pet: bad magic"); return false; }
  if (hdr[4] != DPET_VERSION) { f.close(); LOGE("pet: bad version"); return false; }

  uint16_t fw, fh, fcount, acount;
  memcpy(&fw,     hdr + 8,  2);
  memcpy(&fh,     hdr + 10, 2);
  memcpy(&fcount, hdr + 12, 2);
  memcpy(&acount, hdr + 14, 2);
  if (fw == 0 || fh == 0 || (size_t)fw * fh > MAX_FRAME_PX ||
      fcount == 0 || acount == 0 || acount > MAX_ANIMS) {
    f.close();
    LOGE("pet: bad dims (%ux%u, %u frames, %u anims)",
         (unsigned)fw, (unsigned)fh, (unsigned)fcount, (unsigned)acount);
    return false;
  }

  size_t mask_bytes = ((size_t)fw * fh + 7) / 8;
  size_t per_frame = (size_t)fw * fh * 2 + mask_bytes;
  size_t header_bytes = 16 + (size_t)acount * (NAME_FIELD_LEN + 4);
  size_t expected = header_bytes + (size_t)fcount * per_frame;
  if (fsize != expected) {
    f.close();
    LOGE("pet: size mismatch (got %u, expected %u)",
         (unsigned)fsize, (unsigned)expected);
    return false;
  }

  unload_source();
  s_src.embedded = false;
  s_src.frame_w = fw;
  s_src.frame_h = fh;
  s_src.frame_count = fcount;
  s_src.anim_count = acount;
  s_src.mask_bytes_per_frame = mask_bytes;
  s_src.fs_frames_offset = header_bytes;
  strncpy(s_src.fs_path, path, sizeof(s_src.fs_path) - 1);

  for (int i = 0; i < acount; i++) {
    uint8_t entry[NAME_FIELD_LEN + 4];
    if (f.read(entry, sizeof(entry)) != (int)sizeof(entry)) {
      f.close(); unload_source();
      LOGE("pet: anim table short read");
      return false;
    }
    memcpy(s_src.anims[i].name, entry, NAME_FIELD_LEN);
    s_src.anims[i].name[NAME_FIELD_LEN] = '\0';
    memcpy(&s_src.anims[i].first_frame, entry + NAME_FIELD_LEN, 2);
    memcpy(&s_src.anims[i].frame_count, entry + NAME_FIELD_LEN + 2, 2);
  }
  f.close();

  s_src.frame_pixels = (uint8_t *)malloc((size_t)fw * fh * 2);
  s_src.frame_mask   = (uint8_t *)malloc(mask_bytes);
  if (!s_src.frame_pixels || !s_src.frame_mask) {
    release_frame_buffers();
    unload_source();
    LOGE("pet: frame buffer malloc failed");
    return false;
  }
  s_src.cached_frame_id = -1;
  s_src.ok = true;
  LOGI("pet: loaded %s (%ux%u, %u frames, %u anims) from LittleFS",
       slug, (unsigned)fw, (unsigned)fh, (unsigned)fcount, (unsigned)acount);
  return true;
}

// Lazy-load a single frame's pixels + mask into the cache buffer.
static bool ensure_frame_cached(uint16_t frame_id) {
  if (s_src.embedded) return true;
  if (s_src.cached_frame_id == (int)frame_id) return true;
  if (!s_src.frame_pixels || !s_src.frame_mask) return false;

  File f = LittleFS.open(s_src.fs_path, "r");
  if (!f) { LOGW("pet: frame fopen"); return false; }
  size_t per_frame = (size_t)s_src.frame_w * s_src.frame_h * 2 + s_src.mask_bytes_per_frame;
  size_t offset = s_src.fs_frames_offset + (size_t)frame_id * per_frame;
  if (!f.seek(offset)) { f.close(); LOGW("pet: frame seek"); return false; }
  size_t pix_bytes = (size_t)s_src.frame_w * s_src.frame_h * 2;
  if (f.read(s_src.frame_pixels, pix_bytes) != (int)pix_bytes) {
    f.close(); LOGW("pet: frame pix read"); return false;
  }
  if (f.read(s_src.frame_mask, s_src.mask_bytes_per_frame) != (int)s_src.mask_bytes_per_frame) {
    f.close(); LOGW("pet: frame mask read"); return false;
  }
  f.close();
  s_src.cached_frame_id = frame_id;
  return true;
}

// ------- public API -------

static int find_anim(const char *name) {
  for (int i = 0; i < s_src.anim_count; i++) {
    if (strcmp(s_src.anims[i].name, name) == 0) return i;
  }
  return -1;
}

void pet_begin() {
  ensure_prefs();
  s_enabled = s_prefs.getBool("en", true);
  s_corner  = (PetCorner)s_prefs.getUChar("corner", PET_CORNER_BR);
  char slug[24] = "default";
  s_prefs.getString("active", slug, sizeof(slug));
  strncpy(s_active_slug, slug, sizeof(s_active_slug) - 1);
  s_active_slug[sizeof(s_active_slug) - 1] = '\0';

  bool loaded = false;
  if (strcmp(s_active_slug, "default") != 0 && strlen(s_active_slug) > 0) {
    loaded = load_from_littlefs(s_active_slug);
  }
  if (!loaded) {
    strncpy(s_active_slug, "default", sizeof(s_active_slug));
    load_embedded();
  }

  char state_buf[NAME_FIELD_LEN + 1] = "idle";
  s_prefs.getString("state", state_buf, sizeof(state_buf));
  int idx = find_anim(state_buf);
  s_anim_idx = (idx >= 0) ? idx : 0;
  s_frame = 0;
  s_last_advance_ms = 0;
  s_last_x = s_last_y = -1;
}

bool pet_enabled() { return s_enabled; }
PetCorner pet_corner() { return s_corner; }
const char *pet_state_name() {
  if (!s_src.ok || s_anim_idx < 0 || s_anim_idx >= s_src.anim_count) return "?";
  return s_src.anims[s_anim_idx].name;
}
const char *pet_active_slug() { return s_active_slug; }

void pet_set_enabled(bool en) {
  s_enabled = en;
  ensure_prefs(); s_prefs.putBool("en", en);
  s_last_x = -1; s_last_y = -1;
}

void pet_set_corner(PetCorner c) {
  if (c == s_corner) return;
  s_corner = c;
  ensure_prefs(); s_prefs.putUChar("corner", (uint8_t)c);
  s_last_x = -1; s_last_y = -1;
}

void pet_set_state(const char *name) {
  int idx = find_anim(name);
  if (idx < 0 || idx == s_anim_idx) return;
  s_anim_idx = idx;
  s_frame = 0;
  s_last_advance_ms = 0;
  ensure_prefs(); s_prefs.putString("state", name);
}

// ------- install lifecycle -------

static File s_install_file;
static size_t s_install_total = 0;
static size_t s_install_received = 0;
static char   s_install_slug[24] = {0};
static uint32_t s_install_expected_seq = 0;
static bool   s_install_active = false;

bool pet_install_start(const char *slug, size_t total_bytes) {
  if (!fs_ensure()) return false;
  if (!slug || !*slug) return false;
  if (s_install_active && s_install_file) s_install_file.close();

  strncpy(s_install_slug, slug, sizeof(s_install_slug) - 1);
  s_install_slug[sizeof(s_install_slug) - 1] = '\0';
  s_install_total = total_bytes;
  s_install_received = 0;
  s_install_expected_seq = 0;

  char path[64]; snprintf(path, sizeof(path), "/pets/%s.tmp", slug);
  s_install_file = LittleFS.open(path, "w");
  if (!s_install_file) { LOGE("pet_install: open %s failed", path); return false; }
  s_install_active = true;
  LOGI("pet_install: start %s (%u bytes)", slug, (unsigned)total_bytes);
  return true;
}

bool pet_install_chunk(uint32_t seq, const uint8_t *data, size_t len) {
  if (!s_install_active) { LOGW("pet_install: chunk without start"); return false; }
  if (seq != s_install_expected_seq) {
    LOGE("pet_install: out-of-order chunk %u expected %u", seq, s_install_expected_seq);
    s_install_file.close();
    s_install_active = false;
    return false;
  }
  size_t written = s_install_file.write(data, len);
  if (written != len) {
    LOGE("pet_install: write short (%u/%u)", (unsigned)written, (unsigned)len);
    s_install_file.close();
    s_install_active = false;
    return false;
  }
  s_install_received += len;
  s_install_expected_seq++;
  return true;
}

bool pet_install_active() { return s_install_active; }

bool pet_install_end() {
  if (!s_install_active) return false;
  s_install_file.close();
  s_install_active = false;
  if (s_install_total && s_install_received != s_install_total) {
    LOGE("pet_install: short stream (%u/%u)", (unsigned)s_install_received,
         (unsigned)s_install_total);
    return false;
  }

  char tmp_path[64], final_path[64];
  snprintf(tmp_path,   sizeof(tmp_path),   "/pets/%s.tmp",  s_install_slug);
  snprintf(final_path, sizeof(final_path), "/pets/%s.dpet", s_install_slug);

  File f = LittleFS.open(tmp_path, "r");
  if (!f) { LOGE("pet_install: tmp gone"); return false; }
  uint8_t hdr[16];
  if (f.read(hdr, 16) != 16 || memcmp(hdr, DPET_MAGIC, 4) != 0) {
    f.close(); LittleFS.remove(tmp_path);
    LOGE("pet_install: not a .dpet");
    return false;
  }
  f.close();

  LittleFS.remove(final_path);
  if (!LittleFS.rename(tmp_path, final_path)) {
    LOGE("pet_install: rename failed");
    return false;
  }
  LOGI("pet_install: %s installed", s_install_slug);
  pet_set_active(s_install_slug);
  return true;
}

bool pet_set_active(const char *slug) {
  if (!slug) slug = "default";
  bool loaded = false;
  if (strcmp(slug, "default") == 0 || strlen(slug) == 0) {
    load_embedded();
    loaded = true;
    strncpy(s_active_slug, "default", sizeof(s_active_slug));
  } else if (load_from_littlefs(slug)) {
    strncpy(s_active_slug, slug, sizeof(s_active_slug) - 1);
    s_active_slug[sizeof(s_active_slug) - 1] = '\0';
    loaded = true;
  }
  if (!loaded) return false;
  ensure_prefs(); s_prefs.putString("active", s_active_slug);
  s_anim_idx = 0;
  s_frame = 0;
  s_last_advance_ms = 0;
  s_last_x = s_last_y = -1;
  LOGI("pet: active=%s anims=%d frames=%d", s_active_slug, s_src.anim_count, s_src.frame_count);
  return true;
}

int pet_frame_w() { return s_src.ok ? s_src.frame_w : 0; }
int pet_frame_h() { return s_src.ok ? s_src.frame_h : 0; }
bool pet_in_transition() { return s_tr_active; }

void pet_begin_transition_at(int x, int y, const char *anim_state) {
  if (!s_src.ok) return;
  s_tr_saved_anim_idx = s_anim_idx;
  if (anim_state && *anim_state) {
    int idx = find_anim(anim_state);
    if (idx >= 0) {
      s_anim_idx = idx;
      s_frame = 0;
    }
  }
  s_tr_x = x; s_tr_y = y;
  s_tr_active = true;
  // Make the next render clear-and-redraw at the new position.
  s_last_x = -1; s_last_y = -1;
}

void pet_set_transition_pos(int x, int y) {
  s_tr_x = x; s_tr_y = y;
}

void pet_end_transition() {
  if (!s_tr_active) return;
  s_tr_active = false;
  s_anim_idx = s_tr_saved_anim_idx;
  s_frame = 0;
  s_last_advance_ms = 0;
  s_last_x = -1; s_last_y = -1;
}

bool pet_remove(const char *slug) {
  if (!slug || !*slug) return false;
  if (!fs_ensure()) return false;
  char p[48]; pet_path(slug, p, sizeof(p));
  LittleFS.remove(p);
  if (strcmp(s_active_slug, slug) == 0) pet_set_active("default");
  return true;
}

bool pet_overlap(int, int, int, int) { return false; }

// ------- rendering -------

static void corner_xy(LGFX &tft, int &x, int &y) {
  if (s_tr_active) {
    x = s_tr_x;
    y = s_tr_y;
    return;
  }
  int margin = 4;
  int top  = theme::title_h() + margin;
  int bot  = tft.height() - theme::footer_h() - margin - s_src.frame_h;
  int left = margin;
  int right = tft.width() - margin - s_src.frame_w;
  switch (s_corner) {
    case PET_CORNER_TR: x = right; y = top;   break;
    case PET_CORNER_BR: x = right; y = bot;   break;
    case PET_CORNER_BL: x = left;  y = bot;   break;
    case PET_CORNER_TL: x = left;  y = top;   break;
  }
}

static bool resolve_frame(uint16_t frame_id, const uint16_t *&pix, const uint8_t *&msk) {
  if (s_src.embedded) {
    pix = s_src.embedded_pixels + frame_id * (size_t)s_src.frame_w * s_src.frame_h;
    msk = s_src.embedded_masks  + frame_id * s_src.mask_bytes_per_frame;
    return true;
  }
  if (!ensure_frame_cached(frame_id)) return false;
  pix = (const uint16_t *)s_src.frame_pixels;
  msk = s_src.frame_mask;
  return true;
}

void pet_tick(LGFX &tft) {
  if (!s_src.ok) return;

  int x, y; corner_xy(tft, x, y);

  if (s_last_x >= 0 && (s_last_x != x || s_last_y != y)) {
    tft.fillRect(s_last_x, s_last_y, s_src.frame_w, s_src.frame_h, THEME_BG);
  }
  if (!s_enabled) {
    s_last_x = s_last_y = -1;
    return;
  }

  uint32_t now = millis();
  if (now - s_last_advance_ms >= FRAME_INTERVAL_MS) {
    s_last_advance_ms = now;
    if (s_anim_idx >= 0 && s_anim_idx < s_src.anim_count) {
      uint16_t fc = s_src.anims[s_anim_idx].frame_count;
      if (fc > 0) s_frame = (s_frame + 1) % fc;
    }
  }

  if (s_anim_idx < 0 || s_anim_idx >= s_src.anim_count) return;
  uint16_t fc = s_src.anims[s_anim_idx].frame_count;
  if (fc == 0) return;
  uint16_t frame_id = s_src.anims[s_anim_idx].first_frame + s_frame;
  if (frame_id >= s_src.frame_count) return;

  const uint16_t *pix; const uint8_t *msk;
  if (!resolve_frame(frame_id, pix, msk)) return;

  // Composite the frame into a small RAM buffer first, then push it to the
  // display in one SPI transaction. Doing fillRect + per-pixel drawPixel
  // directly to the screen used to show a brief "background-only" flash
  // between the fill and the slow drawPixel loop completing — especially
  // noticeable when switching animations.
  static uint16_t blit_buf[MAX_FRAME_PX];   // 64×64 = 8 KB worst case
  const uint16_t bg = THEME_BG;
  int n = s_src.frame_w * s_src.frame_h;
  for (int i = 0; i < n; i++) {
    uint8_t mbyte = msk[i >> 3];
    bool opaque = (mbyte & (1 << (7 - (i & 7)))) != 0;
    blit_buf[i] = opaque ? pix[i] : bg;
  }
  tft.pushImage(x, y, s_src.frame_w, s_src.frame_h, blit_buf);
  s_last_x = x; s_last_y = y;
}

// Same as pet_tick() except we never advance the animation timer here —
// the transition compositor calls this once per wipe step so the pet's
// frame stays under its own clock while the body of pet_tick is gated by
// the main loop's redraw cadence.
void pet_render_now(LGFX &tft) {
  if (!s_src.ok || !s_enabled) return;
  int x, y; corner_xy(tft, x, y);
  if (s_anim_idx < 0 || s_anim_idx >= s_src.anim_count) return;
  uint16_t fc = s_src.anims[s_anim_idx].frame_count;
  if (fc == 0) return;
  uint16_t frame_id = s_src.anims[s_anim_idx].first_frame + s_frame;
  if (frame_id >= s_src.frame_count) return;

  const uint16_t *pix; const uint8_t *msk;
  if (!resolve_frame(frame_id, pix, msk)) return;

  static uint16_t blit_buf[MAX_FRAME_PX];
  const uint16_t bg = THEME_BG;
  int n = s_src.frame_w * s_src.frame_h;
  for (int i = 0; i < n; i++) {
    uint8_t mbyte = msk[i >> 3];
    bool opaque = (mbyte & (1 << (7 - (i & 7)))) != 0;
    blit_buf[i] = opaque ? pix[i] : bg;
  }
  tft.pushImage(x, y, s_src.frame_w, s_src.frame_h, blit_buf);

  // Advance the frame on its own cadence (independent of redraw timing).
  uint32_t now = millis();
  if (now - s_last_advance_ms >= 60) {  // crisper anim during transitions
    s_last_advance_ms = now;
    s_frame = (s_frame + 1) % fc;
  }
  s_last_x = x; s_last_y = y;
}
