#include <Arduino.h>
#include "config.h"
#include "button.h"

static uint32_t s_last_change = 0;
static int s_stable = HIGH;
static int s_raw = HIGH;
static uint32_t s_press_started = 0;
static bool s_long_fired = false;

void button_begin() {
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  s_stable = digitalRead(PIN_BUTTON);
  s_raw = s_stable;
}

ButtonEvent button_poll() {
  int r = digitalRead(PIN_BUTTON);
  uint32_t now = millis();
  if (r != s_raw) {
    s_raw = r;
    s_last_change = now;
  }
  if ((now - s_last_change) > BTN_DEBOUNCE_MS && r != s_stable) {
    s_stable = r;
    if (s_stable == LOW) {
      // Pressed.
      s_press_started = now;
      s_long_fired = false;
    } else {
      // Released. If long not yet fired, this is a short press.
      if (!s_long_fired) {
        return BTN_SHORT_PRESS;
      }
    }
  }
  // While held, fire long press exactly once after threshold.
  if (s_stable == LOW && !s_long_fired && (now - s_press_started) >= BTN_LONG_PRESS_MS) {
    s_long_fired = true;
    return BTN_LONG_PRESS;
  }
  return BTN_NONE;
}
