#pragma once
#include <Arduino.h>
#include "transport.h"

void usb_link_begin();
void usb_link_restore_prefs();  // restore theme + thresholds + pages mask from NVS

// Drain incoming USB-CDC bytes, parse complete lines as JSON, and apply
// state messages into g_store. Returns true if a state message was applied.
bool usb_link_poll();

// Feed one complete, newline-stripped JSON line into the shared protocol
// parser. `src` identifies the transport it arrived on — the parser's
// owner gate ignores frames from a non-owner transport. MUST be called
// from the main loop — it mutates g_store.
void dashd_apply_line(const char *line, size_t len, TransportId src);

// Public so main.cpp can read what page the host wants us to show.
extern int g_pending_show_page;       // -1 = none, otherwise PageId index
extern int g_pending_show_page_id;    // alias for clarity (same value)

// Send a single event message (e.g. boot, page_changed) to the host.
void usb_send_event(const char *name);
void usb_send_event_page(const char *name, const char *page);

// Send a log line as a JSON event over the same USB-CDC link. The agent
// prints these at the matching log level. Levels: "debug", "info", "warn", "error".
void usb_log(const char *level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

#define LOGI(...) usb_log("info",  __VA_ARGS__)
#define LOGW(...) usb_log("warn",  __VA_ARGS__)
#define LOGE(...) usb_log("error", __VA_ARGS__)
#define LOGD(...) usb_log("debug", __VA_ARGS__)
