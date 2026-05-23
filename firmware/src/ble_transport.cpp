// NimBLE GATT transport. Compiled only for the `dashd_ble` env (the whole
// translation unit is behind DASHD_ENABLE_BLE, so the production `dashd`
// build — which has no NimBLE dependency — sees an empty file).
#ifdef DASHD_ENABLE_BLE

#include <atomic>
#include <esp_random.h>

#include <NimBLEDevice.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/stream_buffer.h>

#include "ble_transport.h"
#include "config.h"
#include "usb_link.h"   // dashd_apply_line

// Nordic-UART-style layout — RX is host→device (write), TX is device→host
// (notify), AUTH is the pairing channel. Identical RX/TX UUIDs to the spike.
static const char *SVC_UUID  = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
static const char *RX_UUID   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
static const char *TX_UUID   = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
static const char *AUTH_UUID = "6e400004-b5a3-f393-e0a9-e50e24dcca9e";

// ---- Pairing / trust ----------------------------------------------------
// App-level pairing (not BLE SMP): a central is unauthorized until it
// writes either the 6-digit code shown on the device screen, or a trust
// token the device minted on a previous successful pairing. Until then,
// BLE frames are dropped. Up to 4 trust tokens persist in NVS.
static std::atomic<bool> s_authorized{false};
static char s_pair_code[7] = "------";          // 6 digits, set in begin()
static Preferences s_auth_prefs;
static constexpr int TRUST_SLOTS = 4;
// A token minted this session, pending emission to the host by poll().
static std::atomic<bool> s_token_pending{false};
static char s_new_token[12] = {0};
// Set on a fresh connection — poll() then announces the 6-digit pairing
// code over BLE (as a `ble_pair_code` event) so the host can surface it
// in its logs / pairing UI. Note: this puts the code on the BLE link, so
// it is a convenience aid, not a secret — pairing becomes tap-to-confirm.
static std::atomic<bool> s_announce_pending{false};

static bool auth_token_known(const char *v) {
  for (int i = 0; i < TRUST_SLOTS; i++) {
    char key[6];
    snprintf(key, sizeof(key), "tk%d", i);
    String stored = s_auth_prefs.getString(key, "");
    if (stored.length() > 0 && stored == v) return true;
  }
  return false;
}

static void auth_mint_token() {
  // 32-bit random token, hex. Not a security boundary (mirrors ipc.token) —
  // just a "this host paired before" marker. Rotates across 4 NVS slots.
  snprintf(s_new_token, sizeof(s_new_token), "%08lx",
           (unsigned long)esp_random());
  int idx = s_auth_prefs.getInt("tki", 0) % TRUST_SLOTS;
  char key[6];
  snprintf(key, sizeof(key), "tk%d", idx);
  s_auth_prefs.putString(key, s_new_token);
  s_auth_prefs.putInt("tki", idx + 1);
  s_token_pending = true;
}

static NimBLECharacteristic *s_tx = nullptr;
// Written from NimBLE callbacks, read from the main loop — std::atomic, not
// bare `volatile`, for a race-free memory model across the two tasks.
static std::atomic<bool> s_connected{false};
static std::atomic<uint16_t> s_mtu{23};
// Set by onDisconnect (NimBLE task); consumed + cleared by poll() (main
// loop). Defers the reassembly/stream cleanup to the main loop so it never
// races poll()'s own use of that state.
static std::atomic<bool> s_reset_pending{false};

// Raw bytes arrive in the NimBLE stack task; they must NOT be parsed there
// (parsing mutates g_store, which the render loop reads). The RX callback
// only copies bytes into this FreeRTOS stream buffer; poll() drains it on
// the main loop and does the newline reassembly + parse. This is the
// callback-thread → main-loop handoff Codex flagged as mandatory.
static StreamBufferHandle_t s_rx_stream = nullptr;
static constexpr size_t RX_STREAM_BYTES = 8192;

// Line reassembly state — touched ONLY by poll() (main loop). The disconnect
// reset is deferred via s_reset_pending so the NimBLE task never touches it.
static char s_line[USB_RX_LINE_MAX];
static size_t s_line_len = 0;

// ---- NimBLE callbacks (BLE stack task context) --------------------------

class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *c, NimBLEConnInfo &) override {
    NimBLEAttValue v = c->getValue();
    if (s_rx_stream && v.length() > 0) {
      // Non-blocking: if the main loop has fallen behind and the buffer is
      // full, the surplus is dropped and the parser resyncs at the next
      // newline — same resilience as the USB overlong-line path.
      xStreamBufferSend(s_rx_stream, v.data(), v.length(), 0);
    }
  }
};

// Auth characteristic: the central writes the 6-digit pairing code or a
// known trust token. NVS reads/writes here run in the NimBLE task — the
// NVS layer is internally locked, so that's safe.
class AuthCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *c, NimBLEConnInfo &) override {
    NimBLEAttValue v = c->getValue();
    char val[24] = {0};
    size_t n = v.length() < sizeof(val) - 1 ? v.length() : sizeof(val) - 1;
    memcpy(val, v.data(), n);
    if (strcmp(val, s_pair_code) == 0) {
      auth_mint_token();          // first pairing → mint + persist a token
      s_authorized = true;
    } else if (auth_token_known(val)) {
      s_authorized = true;        // returning trusted host
    }
    // Wrong code/token: stay unauthorized; the central may retry.
  }
};

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *, NimBLEConnInfo &info) override {
    s_connected = true;
    s_mtu = info.getMTU();
    s_announce_pending = true;   // poll() announces the pairing code
  }
  void onDisconnect(NimBLEServer *, NimBLEConnInfo &, int) override {
    s_connected = false;
    s_mtu = 23;
    s_authorized = false;         // every new session must re-authorize
    s_token_pending = false;
    // Don't touch the reassembly state or the stream buffer here — that
    // would race poll() on the main loop. Flag it; poll() does the reset
    // on its next tick so a half-line can't bleed into the next session.
    s_reset_pending = true;
    // Release the session so USB (or a new central) can claim it.
    transport_release(TRANSPORT_BLE);
    NimBLEDevice::getAdvertising()->start();
  }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo &) override {
    s_mtu = mtu;
  }
};

// ---- BleTransport -------------------------------------------------------

void BleTransport::begin() {
  s_rx_stream = xStreamBufferCreate(RX_STREAM_BYTES, 1);

  // Pairing: a fresh random 6-digit code per boot, shown on the device's
  // Pair-Mode screen. Trust tokens persist in their own NVS namespace.
  s_auth_prefs.begin("dashd_ble", false);
  snprintf(s_pair_code, sizeof(s_pair_code), "%06lu",
           (unsigned long)(esp_random() % 1000000UL));

  // Advertise a device name that's unique per board (last 2 MAC bytes),
  // so multiple dashd devices are distinguishable in a scan.
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_BT);
  char name[16];
  snprintf(name, sizeof(name), "dashd-%02X%02X", mac[4], mac[5]);

  NimBLEDevice::init(name);
  NimBLEDevice::setMTU(517);                 // request the max; central may grant less
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  NimBLEServer *server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  NimBLEService *svc = server->createService(SVC_UUID);
  NimBLECharacteristic *rx = svc->createCharacteristic(
      RX_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  rx->setCallbacks(new RxCallbacks());
  s_tx = svc->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::NOTIFY);
  NimBLECharacteristic *auth = svc->createCharacteristic(
      AUTH_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  auth->setCallbacks(new AuthCallbacks());
  svc->start();

  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(SVC_UUID);
  adv->setName(name);
  adv->start();
}

bool BleTransport::poll() {
  if (!s_rx_stream) return false;
  // Apply a deferred disconnect cleanup here, on the main loop — never from
  // the NimBLE task, which would race the reassembly state below.
  if (s_reset_pending.exchange(false)) {
    s_line_len = 0;
    xStreamBufferReset(s_rx_stream);
  }
  // Fresh connection from a still-unauthenticated central — announce the
  // 6-digit pairing code so the host can show it in its logs / pairing UI
  // (the same code is on the device's Pair-Mode screen). Skipped once the
  // central has authenticated (a trusted host re-pairing with a token).
  if (s_announce_pending.exchange(false) && !s_authorized.load()) {
    char ev[80];
    snprintf(ev, sizeof(ev),
             "{\"type\":\"event\",\"name\":\"ble_pair_code\",\"code\":\"%s\"}",
             s_pair_code);
    writeLine(ev);
  }
  // A pairing just succeeded — hand the freshly minted trust token to the
  // host so it can skip the code on future connects. Sent from the main
  // loop, not the auth callback.
  if (s_token_pending.exchange(false)) {
    char ev[64];
    snprintf(ev, sizeof(ev),
             "{\"type\":\"event\",\"name\":\"ble_paired\",\"token\":\"%s\"}",
             s_new_token);
    writeLine(ev);
  }
  bool applied = false;
  uint8_t chunk[256];
  size_t n;
  // Drain everything currently buffered, reassembling newline-delimited
  // lines exactly like usb_link_poll does for the serial byte stream.
  while ((n = xStreamBufferReceive(s_rx_stream, chunk, sizeof(chunk), 0)) > 0) {
    for (size_t i = 0; i < n; i++) {
      char b = (char)chunk[i];
      if (b == '\n') {
        if (s_line_len > 0 && s_authorized.load()) {
          // Frames are applied only once the central has paired —
          // unauthenticated centrals can't drive the device.
          s_line[s_line_len] = 0;
          dashd_apply_line(s_line, s_line_len, TRANSPORT_BLE);
          applied = true;
        }
        s_line_len = 0;
      } else if (b == '\r') {
        // ignore
      } else if (s_line_len < USB_RX_LINE_MAX - 1) {
        s_line[s_line_len++] = b;
      } else {
        // Overlong line — drop and resync at the next newline.
        s_line_len = 0;
      }
    }
  }
  return applied;
}

void BleTransport::writeLine(const char *json) {
  if (!s_connected || s_tx == nullptr || json == nullptr) return;
  size_t len = strlen(json);
  // One notification carries ATT_MTU-3 bytes of payload.
  uint16_t mtu = s_mtu.load();
  size_t chunk = (size_t)(mtu > 23 ? mtu : 23) - 3;
  if (chunk < 20) chunk = 20;

  size_t off = 0;
  while (off < len) {
    // Bail cleanly if the central drops mid-frame.
    if (!s_connected) return;
    size_t n = (len - off < chunk) ? (len - off) : chunk;
    s_tx->setValue((const uint8_t *)(json + off), n);
    s_tx->notify();
    off += n;
  }
  if (!s_connected) return;
  // Terminate the frame with a newline so the host reassembles correctly.
  const uint8_t nl = '\n';
  s_tx->setValue(&nl, 1);
  s_tx->notify();
}

bool BleTransport::connected() const { return s_connected; }

uint16_t BleTransport::mtu() const { return s_mtu; }

const char *BleTransport::pairing_code() const { return s_pair_code; }

bool BleTransport::needs_pairing() const {
  // A central is connected but hasn't authorized yet — the main loop draws
  // the Pair-Mode screen with the code while this is true.
  return s_connected.load() && !s_authorized.load();
}

BleTransport g_ble;

#endif  // DASHD_ENABLE_BLE
