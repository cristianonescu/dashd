#pragma once

/**
 * dashd OTA — over-the-air firmware update over either the active USB or
 * BLE transport.
 *
 * Flow (mirrors the pet-install pattern the agent already speaks):
 *
 *   host → device : {"cmd":"fw_update_begin","size":N,"sha256":"...","version":"0.1.3"}
 *   device → host : {"event":"fw_update_started","ok":true}
 *
 *   host → device : {"cmd":"fw_update_chunk","seq":0,"data":"<base64>"}
 *   device → host : {"event":"fw_update_chunk_ack","seq":0,"ok":true}
 *                   {"event":"fw_update_progress","bytes":n,"total":N}
 *   ... (chunks 1..M, agent paces from ACKs) ...
 *
 *   host → device : {"cmd":"fw_update_end"}
 *   device → host : {"event":"fw_update_done","ok":true,"version":"0.1.3"}
 *                   <device reboots into the new slot>
 *
 * On boot the bootloader picks whichever OTA slot otadata says is active.
 * The new firmware MUST call `ota_mark_running_ok()` from setup() once it
 * has come up far enough to talk to the host (we use the moment we emit
 * the `boot` event); otherwise ESP-IDF auto-rolls back to the previous
 * slot on next reset. That's the brick-safety net.
 *
 * The display takes over with an OTA progress overlay (see ui/ota.h)
 * for the duration of the update — pages are paused, the pet hides, the
 * user sees a big progress bar + "do not unplug" message.
 */
#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>

// Lifecycle.
bool ota_begin(size_t total_size, const char *sha256_hex, const char *version);
bool ota_write_chunk(const uint8_t *data, size_t len);
bool ota_end();
void ota_abort(const char *reason);

// Inspection — used by the OTA overlay page to render progress.
bool        ota_active();
bool        ota_failed();
const char *ota_last_error();
size_t      ota_bytes_received();
size_t      ota_total_size();
const char *ota_target_version();

// Call once at startup (after the first successful boot event) to
// cancel the pending rollback. If we don't, the bootloader reverts on
// next reset. Idempotent.
void ota_mark_running_ok();
