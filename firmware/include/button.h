#pragma once

enum ButtonEvent {
  BTN_NONE = 0,
  BTN_SHORT_PRESS,
  BTN_LONG_PRESS,
};

void button_begin();
ButtonEvent button_poll();
