/**
 * Phase 1 de-risk spike — NOT production code.
 *
 * A minimal NimBLE GATT server modelled on the planned dashd "Link" service:
 *   - one service, RX (write) + TX (notify) characteristics,
 *   - MTU 517 requested,
 *   - echoes whatever is written to RX back out on TX.
 *
 * Purpose: prove NimBLE-Arduino compiles for the ESP32-C3 with our
 * PlatformIO/Arduino toolchain and measure its flash/RAM footprint, and
 * give the host-side `ble_spike_host.py` something real to connect to for
 * the throughput / MTU / permission / reconnection measurements.
 *
 * Built only by the `spike_ble` PlatformIO environment — the production
 * `dashd` env never sees this file.
 */
#include <Arduino.h>
#include <NimBLEDevice.h>

// UUIDs mirror the Nordic UART Service layout the real BleTransport will use.
static const char *SVC_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
static const char *RX_UUID  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";  // host → device
static const char *TX_UUID  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";  // device → host (notify)

static NimBLECharacteristic *g_tx = nullptr;
static volatile bool g_connected = false;
static uint32_t g_rx_bytes = 0;

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *, NimBLEConnInfo &info) override {
    g_connected = true;
    Serial.printf("spike: central connected, mtu=%u\n", info.getMTU());
  }
  void onDisconnect(NimBLEServer *srv, NimBLEConnInfo &, int reason) override {
    g_connected = false;
    Serial.printf("spike: central disconnected (reason=%d), re-advertising\n", reason);
    NimBLEDevice::getAdvertising()->start();
  }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo &) override {
    Serial.printf("spike: MTU negotiated = %u\n", mtu);
  }
};

class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *c, NimBLEConnInfo &) override {
    // Echo straight back on TX — the host script times the round trip.
    NimBLEAttValue v = c->getValue();
    g_rx_bytes += v.length();
    if (g_tx) {
      g_tx->setValue(v);
      g_tx->notify();
    }
  }
};

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("dashd BLE spike starting");

  NimBLEDevice::init("dashd-spike");
  NimBLEDevice::setMTU(517);                       // request the max; host may grant less
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  NimBLEServer *server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  NimBLEService *svc = server->createService(SVC_UUID);
  NimBLECharacteristic *rx = svc->createCharacteristic(
      RX_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  rx->setCallbacks(new RxCallbacks());
  g_tx = svc->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::NOTIFY);
  svc->start();

  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(SVC_UUID);
  adv->setName("dashd-spike");
  adv->start();

  Serial.printf("spike: advertising as 'dashd-spike', free heap=%u\n",
                ESP.getFreeHeap());
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last > 5000) {
    last = millis();
    Serial.printf("spike: connected=%d rx_total=%u free_heap=%u\n",
                  (int)g_connected, g_rx_bytes, ESP.getFreeHeap());
  }
  delay(50);
}
