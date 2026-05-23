/**
 * Tiny global store. Avoids pulling in zustand/redux for what is essentially
 * three pieces of state.
 */
import { useEffect, useState } from "react";
import type { StateFrame, LogEvent, AgentMessage } from "./types";

type Listener<T> = (v: T) => void;
class Atom<T> {
  private v: T;
  private subs = new Set<Listener<T>>();
  constructor(initial: T) { this.v = initial; }
  get(): T { return this.v; }
  set(next: T): void { this.v = next; this.subs.forEach((s) => s(next)); }
  update(fn: (prev: T) => T): void { this.set(fn(this.v)); }
  subscribe(l: Listener<T>): () => void {
    this.subs.add(l);
    return () => { this.subs.delete(l); };
  }
}

export function useAtom<T>(atom: Atom<T>): T {
  const [v, setV] = useState(atom.get());
  useEffect(() => atom.subscribe(setV), [atom]);
  return v;
}

export const $state = new Atom<StateFrame | null>(null);
export const $logs = new Atom<LogEvent[]>([]);
export const $agentRunning = new Atom<boolean>(false);
export const $ipcConnected = new Atom<boolean>(false);
export const $deviceConnected = new Atom<boolean>(false);
export const $transport = new Atom<string>("");   // "usb" | "ble" | ""
// Device firmware version, as reported by the most recent boot/hello_ack
// event. Used by Settings → Updates to compare against the latest GitHub
// release tag. Empty string until the agent receives the first event.
export const $deviceFwVersion = new Atom<string>("");
// Most recent `fw_update_state` event from the agent. The UI drives the
// firmware-update card off this — phases include "available",
// "downloading", "flashing", "rebooting", "done", "error", "up_to_date".
export type FwUpdateState =
  | { state: "idle" }
  | { state: "checking" }
  | { state: "up_to_date"; current: string; latest: string }
  | { state: "available"; current: string; latest: string; notes?: string; size_ble?: number; size_usb?: number }
  | { state: "downloading"; version: string; bytes?: number; total?: number | null }
  | { state: "flashing"; version: string; bytes?: number; total?: number }
  | { state: "rebooting"; version: string }
  | { state: "done"; version: string }
  | { state: "error"; error: string };
export const $fwUpdate = new Atom<FwUpdateState>({ state: "idle" });

// Anthropic OAuth usage block — populated from the agent's 60s OAuth
// poll. Null until the first state frame after the user opts in.
import type { AnthropicState } from "./types";
export const $anthropic = new Atom<AnthropicState | null>(null);

const MAX_LOGS = 1000;

export function wireAgentBridge(): void {
  if (typeof window === "undefined" || !window.dashd) {
    // Browser-mode preview (no Electron preload). Inject mock state so the
    // UI is testable in `vite dev` without the real agent.
    installMockBridge();
    return;
  }
  const off1 = window.dashd.onMessage((msg: AgentMessage) => {
    if (msg.type === "state") {
      $state.set(msg);
      // The anthropic block is updated at a 60s cadence on the agent
      // side, so it can be null on most state frames. Only update the
      // atom when present so the popover doesn't flicker.
      if (msg.anthropic) $anthropic.set(msg.anthropic);
    }
    else if (msg.type === "event" && msg.name === "log") {
      $logs.update((prev) => {
        const next = [...prev, msg as LogEvent];
        return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next;
      });
    } else if (msg.type === "event" && msg.name === "agent_status") {
      $deviceConnected.set(!!(msg as any).connected);
      const t = (msg as any).transport;
      if (typeof t === "string") $transport.set(t);
    } else if (msg.type === "event" && (msg.name === "boot" || msg.name === "hello_ack")) {
      const fw = (msg as any).fw_version;
      if (typeof fw === "string" && fw) $deviceFwVersion.set(fw);
    } else if (msg.type === "event" && msg.name === "fw_update_state") {
      $fwUpdate.set(msg as any);
    }
  });
  const off2 = window.dashd.onStdio((line) => {
    // Treat stdio as a debug-level log line so the user can still see it.
    $logs.update((prev) => {
      const next = [...prev, { type: "event", name: "log", level: "debug",
                               logger: "stdio", msg: line } as LogEvent];
      return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next;
    });
  });
  const off3 = window.dashd.onStatus((s) => $agentRunning.set(!!s.running));
  const off4 = window.dashd.onIpcState((s) => $ipcConnected.set(s.connected));
  // Cleanup is window-lifetime; no need to call off* unless we ever HMR.
  void off1; void off2; void off3; void off4;
}

function installMockBridge(): void {
  $agentRunning.set(true);
  $ipcConnected.set(true);
  $deviceConnected.set(true);

  // Provide a no-op `window.dashd` so every component (incl. the Settings
  // sub-panes, which call sendCmd / onMessage at mount) renders without
  // throwing in plain-browser `vite dev` mode.
  if (typeof window !== "undefined" && !window.dashd) {
    const noopOff = () => {};
    (window as any).dashd = {
      onMessage: () => noopOff,
      onStdio: () => noopOff,
      onStatus: () => noopOff,
      onIpcState: () => noopOff,
      sendCmd: () => {},
      restartAgent: async () => {},
      getPrefs: async () => ({}),
      setPrefs: async (p: object) => p,
      getAutostart: async () => false,
      setAutostart: async (v: boolean) => v,
    };
  }
  const baseState: any = {
    type: "state",
    ts: Math.floor(Date.now() / 1000),
    system: {
      cpu_pct: [42, 18, 11, 67, 22, 8, 14, 31],
      ram_pct: 62,
      ram_pressure_pct: 48,
      ram_used_gb: 25.6,
      ram_total_gb: 64,
      disk_pct: 73,
      net_up_kbps: 12,
      net_down_kbps: 142,
      battery_pct: 84,
      battery_charging: true,
      temp_cpu_c: 64,
      top_ram: [
        { name: "Google Chrome", procs: 23, ram_mb: 10366, cpu_pct: 42 },
        { name: "VS Code",       procs: 8,  ram_mb: 4120,  cpu_pct: 7  },
        { name: "VM (Claude)",   procs: 1,  ram_mb: 2771,  cpu_pct: 2  },
      ],
      top_cpu: [
        { name: "Google Chrome", procs: 23, ram_mb: 10366, cpu_pct: 42 },
        { name: "Cursor",        procs: 4,  ram_mb: 1820,  cpu_pct: 18 },
        { name: "kernel_task",   procs: 1,  ram_mb: 540,   cpu_pct: 9  },
      ],
      memory_leak: { name: "Google Chrome", delta_mb: 540, window_min: 5 },
    },
    ai: {
      claude_code: {
        tokens_today: 15489679,
        cost_today_usd: 37.11,
        block_pct: 26,
        block_resets_in_min: 221,
        models: { opus: 15489679, sonnet: 0, haiku: 0 },
      },
      codex: { block_pct: 1, block_resets_in_min: 142, session_active: false },
    },
    git: { branch: "main", commits_today: 4, loc_added: 312, loc_removed: 88,
           minutes_since_last_commit: 17 },
    github: { prs_awaiting_review: 3, ci_failures_24h: 1, unread_notifications: 5 },
    calendar: { next_event_title: "Sprint Planning", next_event_in_min: 17, today_remaining: 3 },
    messages: { email: { unread: 12 }, slack: null, teams: null, imessage: { unread: 2 }, whatsapp: null },
    suggestions: [
      { severity: "warn", text: "Chrome +540 MB / 5m" },
      { severity: "info", text: "3 PRs need review" },
      { severity: "info", text: "In 17m: Sprint Planning" },
    ],
  };
  $state.set(baseState);
  // Tick the timestamp so the "Updated" status shows movement.
  setInterval(() => {
    $state.set({ ...baseState, ts: Math.floor(Date.now() / 1000) });
  }, 2000);
}
