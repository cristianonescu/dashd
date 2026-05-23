# Wiring

ESP32-C3 SuperMini ↔ ST7789V 240×320 IPS (SPI), portrait orientation.

**Schematic — one row per net, no crossings:**

![dashd wiring schematic](wiring.svg)

**Physical board view — how it actually looks on the breadboard:**

![dashd physical board view](wiring_physical.svg)

## Bill of materials

- 1× ESP32-C3 SuperMini (any vendor with native USB on GPIO 18/19)
- 1× ST7789V 240×320 IPS SPI display (8-pin: VCC, GND, SCL, SDA, RES, DC, CS, BLK)
- 1× tactile push button (PCB or panel mount)
- USB-C cable (data, not charge-only)
- Hookup wire / Dupont leads

## Display (SPI)

| ESP32-C3 GPIO | ST7789V Pin | Purpose            |
|---------------|-------------|--------------------|
| 3V3           | VCC         | Power              |
| GND           | GND         | Ground             |
| GPIO 4        | SCL / SCK   | SPI clock          |
| GPIO 6        | SDA / MOSI  | SPI data           |
| GPIO 7        | CS          | Chip select        |
| GPIO 5        | DC          | Data/command       |
| GPIO 10       | RST         | Reset              |
| GPIO 3        | BLK         | Backlight (PWM)    |

The same pinout is encoded in [firmware/include/lgfx_panel.h](../firmware/include/lgfx_panel.h) and [firmware/include/config.h](../firmware/include/config.h). If you change pins here, change them there too.

## Button

| ESP32-C3 GPIO | Component                              |
|---------------|----------------------------------------|
| GPIO 2        | Tactile button → GND (internal pull-up)|

- **Short press** (< 800 ms): next page.
- **Long press** (≥ 800 ms): jump to Home.

Debounce is 25 ms, handled in firmware ([button.cpp](../firmware/src/button.cpp)).

## Notes

- Some ST7789V breakouts label MOSI as "SDA" and SCK as "SCL". The wiring table above uses both names.
- BLK can be tied to 3V3 if you don't need software brightness control; the firmware just stops being able to PWM it.
- Don't power the display from 5 V — it's a 3.3 V part.

## Untethered configuration — running on a battery

By default the device draws power and data over its single USB-C cable.
In **Bluetooth** transport mode the data is wireless, so the device only
needs *power* — which can come from any USB source, including a battery.
This makes a fully untethered desk widget. (Bluetooth gives untethered
data; the battery gives untethered power — they are independent options.)

The board has **no on-board battery or charger**, so you add them
externally. Two documented options:

### Option A — IP5306 power-bank module (recommended)

An IP5306-based "power bank" module is a complete power-path IC: it
charges a 1S LiPo, outputs a regulated 5 V, supports load-sharing
(charge-while-running), and has an on/off button.

```
 1S LiPo ──► IP5306 module ──► 5 V out ──► ESP32-C3 5V pin
   (B+/B-)     (charge via       │          GND ──────► GND
               its own USB)      └─ single 5 V source: no backfeed
```

- Pick a module with an **always-on / low-load-keep-alive mode** — many
  power-bank modules auto-shut-off under the device's light draw.
- The device's own USB-C stays unplugged in this mode (BLE carries data).
- Recharge through the IP5306 module's USB port.

### Option B — discrete TP4056 + MT3608 (advanced)

```
 1S LiPo ─► TP4056 (protected) ─► slide switch ─► MT3608 boost ─► 5V pin
  (B+/B-)    (charge via its USB)                 (trim to 5.0 V)   GND─►GND
```

- **Trim the MT3608 to exactly 5.0 V with a multimeter before connecting
  it** — it ships at an arbitrary, possibly high voltage.
- Use the **protected** TP4056 variant (over-discharge cutoff).
- The switch sits after the TP4056, before the boost, so it cuts the
  boost's quiescent draw while leaving charging/protection intact.
- If you want the device's USB-C **and** the battery both connectable,
  add a **Schottky-diode OR** (each 5 V source through its own diode into
  the 5V pin) so they can't backfeed each other. The ~0.3 V diode drop
  can make an AMS1117-class on-board LDO marginal — verify 3.3 V holds
  under full display + backlight load before relying on it.

### Important notes

- **Never feed a raw LiPo into the 3.3 V pin** — a 1S cell is up to 4.2 V.
  Always go through the 5V pin (the board's regulator handles it). Many
  SuperMini boards use an AMS1117-class LDO (~1.1 V dropout), which is
  why the battery must be **boosted to 5 V**, not fed in raw.
- "Turn the device off while charging" unless you are using a proper
  power-path module (Option A) — basic TP4056 load-sharing confuses
  charge termination and heats the charger.
- Mind **JST connector polarity**, match the charger current to the cell
  capacity, keep LiPo leads strain-relieved, and consider a polyfuse.
- Rough runtime: display backlight + BLE ≈ 100–150 mA, so a 1000 mAh
  cell gives **~6–9 h** — a rough figure, measure your own build.
- The System page's **Battery** reading is your *computer's* battery
  (from `psutil`), **not** the device's LiPo. On-device battery sensing
  would need an ADC voltage divider and is out of scope for v1.
