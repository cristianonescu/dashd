/**
 * Settings → Connection
 *
 * Three jobs:
 *   1. Pick the device transport — Cable / Bluetooth / Auto. The choice is
 *      saved to UI prefs and the agent is restarted so it re-reads it.
 *   2. Bluetooth discovery — scan for dashd devices and list them.
 *   3. Bluetooth pairing — connect to a device (it shows a 6-digit code on
 *      its screen), enter the code, and the device is remembered so future
 *      connects skip the code. Plus a list of trusted devices to forget.
 */
import { useEffect, useRef, useState } from "react";

type Mode = "cable" | "bluetooth" | "auto";
type ScanDev = { name: string; address: string; rssi: number | null };
type PairState = "idle" | "connecting" | "awaiting_code" | "paired" | "failed";

const MODE_HINTS: Record<Mode, string> = {
  cable: "Talk to the device only over the USB cable.",
  bluetooth: "Talk to the device only over Bluetooth LE. Needs the device paired (below).",
  auto: "Use the cable when a dashd device is plugged in, otherwise Bluetooth. Recommended.",
};

export default function ConnectionPane() {
  const [mode, setMode] = useState<Mode>("auto");
  const [scanning, setScanning] = useState(false);
  const [devices, setDevices] = useState<ScanDev[]>([]);
  const [trusted, setTrusted] = useState<string[]>([]);
  const [pair, setPair] = useState<{ state: PairState; address?: string; error?: string; code?: string }>(
    { state: "idle" });
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 2200); };
  // The Pairing card appears as a new card below the device list — on a
  // small window it can land below the scroll fold. Scroll it into view
  // automatically so the code + input are visible the moment they exist.
  const pairCardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (pair.state === "awaiting_code" || pair.state === "connecting") {
      pairCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [pair.state]);

  useEffect(() => {
    (async () => {
      const prefs = await window.dashd.getPrefs();
      setMode((prefs as any).transportMode ?? "auto");
    })();
    window.dashd.sendCmd({ name: "ble_trusted_list" });
    return window.dashd.onMessage((m: any) => {
      if (m.type !== "event") return;
      if (m.name === "ble_scan_result") {
        setScanning(false);
        setDevices(m.devices || []);
        if (m.error) flash(`Scan failed: ${m.error}`);
      } else if (m.name === "ble_pairing_state") {
        setPair({ state: m.state, address: m.address, error: m.error, code: m.code });
        if (m.state === "paired") { setCode(""); flash("Paired"); }
      } else if (m.name === "ble_trusted") {
        setTrusted(m.devices || []);
      }
    });
  }, []);

  const applyMode = async (m: Mode) => {
    setMode(m);
    await window.dashd.setPrefs({ transportMode: m } as any);
    await window.dashd.restartAgent();
    flash(`Transport: ${m} — agent restarted`);
  };

  const scan = () => {
    setScanning(true);
    setDevices([]);
    window.dashd.sendCmd({ name: "ble_scan" });
  };

  return (
    <>
      <div className="card">
        <h3 data-hint="How the agent talks to the device. Changing this restarts the agent.">Transport</h3>
        <div className="segmented" style={{ margin: "4px 0 0" }}>
          {(["cable", "bluetooth", "auto"] as const).map((m) => (
            <button key={m} className={`seg ${mode === m ? "active" : ""}`}
                    onClick={() => applyMode(m)} data-hint={MODE_HINTS[m]}>
              {m}
            </button>
          ))}
        </div>
        {msg && <p className="dim" style={{ fontSize: 12, marginBottom: 0 }}>{msg}</p>}
      </div>

      <div className="card">
        <h3 data-hint="Find dashd devices advertising over Bluetooth.">Bluetooth devices</h3>
        <button className="btn" onClick={scan} disabled={scanning}
                data-hint="Scan for nearby dashd devices over Bluetooth LE.">
          {scanning ? "Scanning…" : "Scan"}
        </button>
        {devices.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {devices.map((d) => (
              <div className="proc-row" key={d.address}>
                <span className="name">
                  {d.name}
                  {d.rssi != null && <span className="count">{d.rssi} dBm</span>}
                </span>
                <span className="actions">
                  <button
                    data-hint={`Pair with ${d.name}. It will show a 6-digit code on its screen.`}
                    onClick={() => window.dashd.sendCmd({ name: "ble_pair", address: d.address })}
                  >Pair</button>
                </span>
              </div>
            ))}
          </div>
        )}
        {!scanning && devices.length === 0 && (
          <p className="dim" style={{ fontSize: 12 }}>
            No devices yet — click Scan with the device powered and nearby.
          </p>
        )}
      </div>

      {(pair.state === "connecting" || pair.state === "awaiting_code"
        || pair.state === "failed") && (
        <div className="card" ref={pairCardRef}>
          <h3>Pairing{pair.address ? ` · ${pair.address}` : ""}</h3>
          {pair.state === "connecting" && (
            <p className="dim" style={{ fontSize: 13 }}>Connecting…</p>
          )}
          {pair.state === "awaiting_code" && (
            <>
              <p style={{ fontSize: 13 }}>
                Enter the 6-digit code shown on the device's screen
                {pair.code ? "" : " (also printed in the Logs tab)"}:
              </p>
              {pair.code && (
                <p style={{ fontSize: 13, margin: "0 0 8px" }}>
                  Code reported by the device:{" "}
                  <strong style={{ fontVariantNumeric: "tabular-nums",
                                   letterSpacing: "0.1em" }}>{pair.code}</strong>
                </p>
              )}
              <div style={{ display: "flex", gap: 8 }}>
                <input type="text" value={code} maxLength={6}
                       onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                       placeholder="000000"
                       data-hint="The 6-digit code currently on the device's display."
                       style={{ width: 120, fontVariantNumeric: "tabular-nums" }}/>
                <button className="btn primary"
                        disabled={code.length !== 6}
                        onClick={() => window.dashd.sendCmd({ name: "ble_pair_code", code })}
                        data-hint="Submit the code to finish pairing.">
                  Pair
                </button>
              </div>
            </>
          )}
          {pair.state === "failed" && (
            <p className="crit" style={{ fontSize: 13 }}>
              Pairing failed{pair.error ? `: ${pair.error}` : ""}.
            </p>
          )}
        </div>
      )}

      <div className="card">
        <h3 data-hint="Devices this computer has paired with. Forgetting one means re-entering the code next time.">
          Paired devices
        </h3>
        {trusted.length === 0 ? (
          <p className="dim" style={{ fontSize: 12 }}>No paired devices.</p>
        ) : (
          trusted.map((addr) => (
            <div className="proc-row" key={addr}>
              <span className="name">{addr}</span>
              <span className="actions">
                <button className="danger"
                        data-hint={`Forget ${addr} — pairing will be required again next time.`}
                        onClick={() => window.dashd.sendCmd({ name: "ble_untrust", address: addr })}
                >Forget</button>
              </span>
            </div>
          ))
        )}
      </div>
    </>
  );
}
