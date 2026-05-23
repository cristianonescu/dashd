#pragma once
#include <Arduino.h>

// Device transport interface.
//
// The firmware talks to the host over a transport. Today the only transport
// is USB-CDC; Phase 3 adds a NimBLE BLE transport. Both speak the identical
// newline-delimited JSON protocol — only the byte pipe differs.
//
// `usb_link.cpp` is the USB implementation; `ble_transport.cpp` provides
// `BleTransport : ITransport`. Both feed the shared parser via
// `dashd_apply_line`, and every outbound write routes through
// `transport_emit` to whichever transport owns the session.
//
// Session-ownership rule: the device accepts inbound frames from exactly
// ONE transport at a time — the one that sent the first frame / `hello` —
// and routes all replies (events, logs, ACKs) back to it (see s_owner in
// usb_link.cpp). USB is exclusive; BLE is a single session slot in v1,
// expandable for the future multi-computer feature.

// Which transport currently owns the host session. The device accepts
// inbound frames from exactly ONE transport at a time and routes all
// outbound replies (events, logs, ACKs) back to it — so USB and BLE can
// never interleave commands/state into g_store.
enum TransportId { TRANSPORT_NONE, TRANSPORT_USB, TRANSPORT_BLE };

// Current session owner. TRANSPORT_NONE until the first frame arrives.
TransportId transport_owner();

// Release ownership if `who` currently holds it (called on disconnect).
void transport_release(TransportId who);


class ITransport {
 public:
  virtual ~ITransport() {}

  // Bring the transport up (open the port / start advertising).
  virtual void begin() = 0;

  // Drain inbound bytes, parse complete newline-delimited JSON lines, and
  // apply them. Returns true if a state message was applied this tick.
  // Lines longer than the RX line cap are dropped (resync at next '\n').
  virtual bool poll() = 0;

  // Write one already-encoded JSON object as a single '\n'-terminated line.
  // The transport fragments to its own MTU as needed.
  virtual void writeLine(const char *json) = 0;

  // True when a host session is established on this transport.
  virtual bool connected() const = 0;
};
