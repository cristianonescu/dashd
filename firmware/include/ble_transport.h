#pragma once
// NimBLE GATT transport — compiled only when DASHD_ENABLE_BLE is defined
// (the production `dashd` env is USB-only; the `dashd_ble` env opts in).
// Same newline-delimited JSON protocol as USB-CDC; only the byte pipe
// differs. See firmware/include/transport.h for the interface contract.
#ifdef DASHD_ENABLE_BLE

#include <Arduino.h>
#include "transport.h"

class BleTransport : public ITransport {
 public:
  void begin() override;        // init NimBLE, start the GATT service + advertising
  bool poll() override;         // main-loop: drain received lines into the parser
  void writeLine(const char *json) override;  // notify, fragmented to the MTU
  bool connected() const override;            // a central is connected + subscribed

  // Negotiated ATT MTU (23 until a central negotiates higher).
  uint16_t mtu() const;

  // Pairing: the 6-digit code for this boot, and whether a connected
  // central still needs to authorize (drives the Pair-Mode screen).
  const char *pairing_code() const;
  bool needs_pairing() const;
};

extern BleTransport g_ble;

#endif  // DASHD_ENABLE_BLE
