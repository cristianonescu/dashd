/**
 * OTA writer for dashd.
 *
 * Built on ESP-IDF's `esp_ota_*` API (which Arduino-ESP32 wraps and
 * exposes). We deliberately do NOT use the Arduino `Update` library
 * because it pulls in HTTP/WiFi paths we don't need and is harder to
 * fence around the rollback semantics we want.
 *
 * Streaming design:
 *
 *   - The agent has already downloaded the .bin from GitHub Releases
 *     into a temp file and verified its SHA256. It just chunks bytes
 *     over the active transport in ~2 KB pieces.
 *   - We write each chunk straight to the next OTA partition via
 *     `esp_ota_write`. There's no buffering on our end; the partition
 *     driver flushes a flash page at a time.
 *   - On `ota_end()` we re-verify the bytes we wrote (read back +
 *     hash) against the host-provided sha256 hex string. Only if it
 *     matches do we call `esp_ota_set_boot_partition` + reboot.
 *
 * Rollback:
 *
 *   - `esp_ota_set_boot_partition` writes the new selection into
 *     otadata. On next boot the bootloader picks the new slot.
 *   - The IDF bootloader runs each new app in "pending verification"
 *     mode. If we don't call `esp_ota_mark_app_valid_cancel_rollback()`
 *     before the next reset, the bootloader reverts.
 *   - Our main.cpp calls `ota_mark_running_ok()` right after emitting
 *     the `boot` event — at that point we've initialized the display,
 *     drained NVS, and are talking to the host, so we consider the
 *     boot good enough to commit.
 */
#include "ota_link.h"

#include "config.h"
#include "usb_link.h"

#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <esp_system.h>

#include <mbedtls/sha256.h>
#include <string.h>

namespace {

esp_ota_handle_t  s_handle = 0;
const esp_partition_t *s_partition = nullptr;
size_t            s_total = 0;
size_t            s_received = 0;
char              s_expected_sha[65] = {0};  // 64 hex + NUL
char              s_target_ver[32] = {0};
char              s_last_err[64] = {0};
bool              s_active = false;
bool              s_failed = false;

void set_err(const char *fmt, ...) {
  va_list ap; va_start(ap, fmt);
  vsnprintf(s_last_err, sizeof(s_last_err), fmt, ap);
  va_end(ap);
  s_failed = true;
}

int hex_to_nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
  if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
  return -1;
}

bool hex_to_bytes(const char *hex, uint8_t *out, size_t out_len) {
  for (size_t i = 0; i < out_len; i++) {
    int hi = hex_to_nibble(hex[2 * i]);
    int lo = hex_to_nibble(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  return true;
}

bool verify_image_sha(const esp_partition_t *p, size_t len, const uint8_t expect[32]) {
  // Read back the freshly-written bytes and SHA them — confirms the flash
  // write actually landed (catches partial writes / brown-outs).
  static constexpr size_t kBlk = 1024;
  static uint8_t buf[kBlk];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  size_t off = 0;
  while (off < len) {
    size_t n = (len - off > kBlk) ? kBlk : (len - off);
    esp_err_t err = esp_partition_read(p, off, buf, n);
    if (err != ESP_OK) {
      mbedtls_sha256_free(&ctx);
      return false;
    }
    mbedtls_sha256_update(&ctx, buf, n);
    off += n;
  }
  uint8_t actual[32];
  mbedtls_sha256_finish(&ctx, actual);
  mbedtls_sha256_free(&ctx);
  return memcmp(actual, expect, 32) == 0;
}

}  // namespace

/**
 * Translate ESP-IDF's OTA error codes into a short human-readable tag
 * so the host (and the user) can see "validate_failed" instead of "-5379".
 * Covers every code esp_ota_write / esp_ota_begin / esp_ota_end can return.
 */
static const char *_ota_err_name(esp_err_t err) {
  switch (err) {
    case ESP_OK:                            return "ok";
    case ESP_ERR_INVALID_ARG:               return "invalid_arg";
    case ESP_ERR_NO_MEM:                    return "no_mem";
    case ESP_ERR_NOT_FOUND:                 return "not_found";
    case ESP_ERR_NOT_SUPPORTED:             return "not_supported";
    case ESP_ERR_TIMEOUT:                   return "timeout";
    case ESP_ERR_INVALID_STATE:             return "invalid_state";
    case ESP_ERR_FLASH_OP_TIMEOUT:          return "flash_op_timeout";
    case ESP_ERR_FLASH_OP_FAIL:             return "flash_op_fail";
    case ESP_ERR_OTA_BASE:                  return "ota_base";
    case ESP_ERR_OTA_PARTITION_CONFLICT:    return "partition_conflict";
    case ESP_ERR_OTA_SELECT_INFO_INVALID:   return "select_info_invalid";
    case ESP_ERR_OTA_VALIDATE_FAILED:       return "validate_failed";
    case ESP_ERR_OTA_SMALL_SEC_VER:         return "small_sec_ver";
    case ESP_ERR_OTA_ROLLBACK_FAILED:       return "rollback_failed";
    case ESP_ERR_OTA_ROLLBACK_INVALID_STATE:return "rollback_invalid_state";
    default:                                return "unknown";
  }
}

bool ota_begin(size_t total_size, const char *sha256_hex, const char *version) {
  if (s_active) {
    set_err("OTA already running");
    return false;
  }
  s_failed = false;
  s_last_err[0] = 0;

  if (!sha256_hex || strlen(sha256_hex) != 64) {
    set_err("bad sha256 (expected 64 hex chars)");
    return false;
  }
  if (total_size == 0 || total_size > 0x180000) {
    // Hard cap matches the OTA slot size in partitions.csv.
    set_err("bad size (%u)", (unsigned)total_size);
    return false;
  }

  s_partition = esp_ota_get_next_update_partition(nullptr);
  if (!s_partition) {
    set_err("no OTA partition");
    return false;
  }
  esp_err_t err = esp_ota_begin(s_partition, total_size, &s_handle);
  if (err != ESP_OK) {
    set_err("esp_ota_begin %s (%d), partition=%s size=%u",
            _ota_err_name(err), (int)err,
            s_partition->label, (unsigned)total_size);
    return false;
  }

  s_total = total_size;
  s_received = 0;
  strncpy(s_expected_sha, sha256_hex, sizeof(s_expected_sha) - 1);
  s_expected_sha[sizeof(s_expected_sha) - 1] = 0;
  if (version) {
    strncpy(s_target_ver, version, sizeof(s_target_ver) - 1);
    s_target_ver[sizeof(s_target_ver) - 1] = 0;
  } else {
    s_target_ver[0] = 0;
  }
  s_active = true;
  LOGI("OTA begin: %u bytes, target=%s, partition=%s",
       (unsigned)total_size, s_target_ver, s_partition->label);
  return true;
}

bool ota_write_chunk(const uint8_t *data, size_t len) {
  if (!s_active) {
    set_err("write before begin");
    return false;
  }
  if (s_received + len > s_total) {
    set_err("overrun (%u+%u>%u)",
            (unsigned)s_received, (unsigned)len, (unsigned)s_total);
    return false;
  }
  esp_err_t err = esp_ota_write(s_handle, data, len);
  if (err != ESP_OK) {
    // Include both the human name and the numeric code so the host can
    // diagnose without grepping ESP-IDF headers. Also include the byte
    // offset where the failure occurred — for ESP_ERR_OTA_VALIDATE_FAILED
    // it's almost always within the first ESP_IMAGE_HEADER_SIZE (24B),
    // indicating an image-header / chip-type mismatch.
    set_err("esp_ota_write %s (%d) at byte %u/%u",
            _ota_err_name(err), (int)err,
            (unsigned)s_received, (unsigned)s_total);
    esp_ota_abort(s_handle);
    s_active = false;
    return false;
  }
  s_received += len;
  return true;
}

bool ota_end() {
  if (!s_active) {
    set_err("end before begin");
    return false;
  }
  if (s_received != s_total) {
    set_err("short read %u/%u", (unsigned)s_received, (unsigned)s_total);
    esp_ota_abort(s_handle);
    s_active = false;
    return false;
  }
  esp_err_t err = esp_ota_end(s_handle);
  if (err != ESP_OK) {
    set_err("esp_ota_end %s (%d)", _ota_err_name(err), (int)err);
    s_active = false;
    return false;
  }

  // Verify the bytes we wrote actually match the host-provided SHA before
  // flipping the boot pointer. If verification fails, otadata is left as
  // it was — next boot still runs the current firmware.
  uint8_t expect[32];
  if (!hex_to_bytes(s_expected_sha, expect, 32)) {
    set_err("bad sha hex");
    s_active = false;
    return false;
  }
  if (!verify_image_sha(s_partition, s_total, expect)) {
    set_err("sha256 mismatch");
    s_active = false;
    return false;
  }

  err = esp_ota_set_boot_partition(s_partition);
  if (err != ESP_OK) {
    set_err("esp_ota_set_boot_partition %s (%d)", _ota_err_name(err), (int)err);
    s_active = false;
    return false;
  }
  LOGI("OTA committed: %s (%u bytes) → reboot in 500 ms", s_target_ver, (unsigned)s_total);
  s_active = false;

  // Let the host get the "done" event before we yank the link.
  delay(500);
  esp_restart();
  return true;  // unreachable
}

void ota_abort(const char *reason) {
  if (s_active) {
    esp_ota_abort(s_handle);
    s_active = false;
  }
  if (reason) set_err("aborted: %s", reason);
  else        set_err("aborted");
  LOGW("OTA aborted: %s", s_last_err);
}

bool        ota_active()           { return s_active; }
bool        ota_failed()           { return s_failed; }
const char *ota_last_error()       { return s_last_err; }
size_t      ota_bytes_received()   { return s_received; }
size_t      ota_total_size()       { return s_total; }
const char *ota_target_version()   { return s_target_ver; }

void ota_mark_running_ok() {
  // Idempotent — IDF tolerates this being called when there's no pending
  // verification. Just an ESP_ERR_NOT_FOUND we ignore.
  esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
  if (err == ESP_OK) {
    LOGI("OTA: marked running image valid (rollback cancelled)");
  } else if (err != ESP_ERR_NOT_FOUND && err != ESP_ERR_INVALID_STATE) {
    LOGW("OTA: mark_valid err=%d", (int)err);
  }
}
