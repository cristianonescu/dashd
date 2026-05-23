"""Phase 1 de-risk spike — host side. NOT production code.

Pairs with firmware/src/spike/ble_spike.cpp. Run it with the spike firmware
flashed to the ESP32-C3:

    python agent/spike/ble_spike_host.py

It exercises the BLE unknowns the plan flagged:
  - discovery + connect via `bleak`,
  - ATT MTU actually negotiated on this OS/adapter (macOS CoreBluetooth may
    grant less than the 517 the device requests),
  - round-trip echo throughput at the negotiated chunk size,
  - notification reassembly across multiple packets,
  - a reconnect cycle.

First run triggers the OS Bluetooth-permission prompt — that prompt
attribution and the bundled-binary case are validated separately when this
is run from inside the PyInstaller bundle.
"""
from __future__ import annotations

import asyncio
import time

from bleak import BleakClient, BleakScanner

SVC = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # host → device (write)
TX  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device → host (notify)

DEVICE_NAME = "dashd-spike"


async def run_once() -> bool:
    print(f"scanning for '{DEVICE_NAME}' (15 s)…")
    dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
    if dev is None:
        print("  ✗ not found — is the spike firmware flashed and powered?")
        return False
    print(f"  ✓ found {dev.address}")

    async with BleakClient(dev) as client:
        mtu = client.mtu_size
        rx_char = client.services.get_characteristic(RX)
        # On macOS CoreBluetooth, `mtu_size` is NOT necessarily the usable
        # write-without-response payload — bleak exposes the real ceiling as
        # `max_write_without_response_size`. Prefer it, fall back to MTU-3.
        wwr = getattr(rx_char, "max_write_without_response_size", 0) or 0
        chunk = max(20, wwr if wwr else mtu - 3)
        print(f"  ✓ connected — MTU={mtu}, write-without-response size={wwr or 'n/a'} "
              f"→ chunking at {chunk} B")

        received = bytearray()
        done = asyncio.Event()
        expected = 0

        def on_notify(_h, data: bytearray) -> None:
            received.extend(data)
            if expected and len(received) >= expected:
                done.set()

        await client.start_notify(TX, on_notify)

        # Throughput test: echo a 16 KB blob (≈ 20× a real state frame).
        payload = bytes((i * 7) & 0xFF for i in range(16384))
        expected = len(payload)
        t0 = time.time()
        for i, off in enumerate(range(0, len(payload), chunk)):
            await client.write_gatt_char(rx_char, payload[off:off + chunk], response=False)
            # Fire-and-forget writes can overrun CoreBluetooth's internal
            # WWR queue. Every 16 chunks, yield + briefly pace so the OS
            # backend can drain. The real BleLink will instead honour the
            # backend's "ready to send" signal.
            if (i & 0x0F) == 0x0F:
                await asyncio.sleep(0.01)
        try:
            await asyncio.wait_for(done.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            print(f"  ✗ echo timed out — got {len(received)}/{expected} B")
            return False
        dt = time.time() - t0

        ok = bytes(received[:expected]) == payload
        kbps = (expected * 2) / dt / 1024  # *2: write + echo
        print(f"  ✓ echoed {expected} B round-trip in {dt:.2f}s "
              f"→ ~{kbps:.1f} KB/s, integrity={'OK' if ok else 'CORRUPT'}")
        await client.stop_notify(TX)
        return ok


async def main() -> None:
    print("=== dashd BLE Phase-1 spike (host) ===")
    ok1 = await run_once()
    if not ok1:
        return
    print("\nreconnect test — second connect cycle…")
    ok2 = await run_once()
    print(f"\nresult: throughput={'OK' if ok1 else 'FAIL'}, "
          f"reconnect={'OK' if ok2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
