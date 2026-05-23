import { useAtom, $agentRunning, $deviceConnected, $ipcConnected, $state, $transport } from "../store";

export default function StatusBar() {
  const agent = useAtom($agentRunning);
  const device = useAtom($deviceConnected);
  const ipc = useAtom($ipcConnected);
  const state = useAtom($state);
  const transport = useAtom($transport);
  const ts = state ? new Date(state.ts * 1000) : null;
  const transportLabel = transport === "ble" ? "BLE" : transport === "usb" ? "USB" : "";

  return (
    <div className="status-bar">
      <div
        className="status-pill"
        data-hint={
          agent
            ? "The background dashd-agent process is running. It collects metrics and bridges the computer to the device."
            : "The dashd-agent process is not running. Use Settings → General → Restart agent."
        }
      >
        <span className={`dot ${agent ? "good" : "crit"}`} />
        Agent
      </div>
      <div
        className="status-pill"
        data-hint={
          ipc
            ? "This window is connected to the agent over the local IPC socket (127.0.0.1:52317). Live data and commands flow through it."
            : "Waiting to connect to the agent's local IPC socket. The window reconnects automatically."
        }
      >
        <span className={`dot ${ipc ? "good" : "warn"}`} />
        IPC
      </div>
      <div
        className="status-pill"
        data-hint={
          device
            ? `The ESP32 desk device is connected${transportLabel ? ` over ${transportLabel}` : ""} and receiving state frames.`
            : "No ESP32 device connected. Plug in the cable or pair over Bluetooth (Settings → Connection)."
        }
      >
        <span className={`dot ${device ? "good" : "crit"}`} />
        Device{transportLabel ? ` · ${transportLabel}` : ""}
      </div>
      <div
        className="status-pill dim"
        style={{ marginLeft: "auto" }}
        data-hint="Timestamp of the most recent state frame received from the agent. Updates every ~2 s while active."
      >
        {ts ? `Updated ${ts.toLocaleTimeString()}` : "Waiting for state…"}
      </div>
    </div>
  );
}
