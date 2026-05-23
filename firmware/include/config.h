#pragma once

// Build-wide constants for the dashd firmware.
// Pinout matches docs/wiring.md and User_Setups/Setup_Dashd.h.

#define DASHD_FW_VERSION "0.1.12"

// Wire-protocol version. Must match agent/dashd/protocol.py PROTOCOL_VERSION.
// Reported in `boot` and `hello_ack` events so the host can negotiate.
#define DASHD_PROTOCOL_VERSION 1

#define PIN_BUTTON       2
#define PIN_BACKLIGHT    3

// Button timings (ms).
#define BTN_DEBOUNCE_MS   25
#define BTN_LONG_PRESS_MS 800

// USB-CDC framing. Generous so frames carrying top-processes + suggestions
// always fit even with future fields piled on.
#define USB_RX_LINE_MAX 4096

// Watchdog: if no state msg in this many ms, mark the host link stale.
#define HOST_STALE_MS 10000

// Display refresh.
#define DISPLAY_REDRAW_MS 250
