/**
 * Electron main process.
 *
 * Responsibilities:
 *   - Spawn + supervise the bundled `dashd-agent` binary (auto-restart on
 *     crash with exponential backoff capped at 30 s, mirroring the agent's
 *     own USB reconnect policy).
 *   - Open a connection to the agent's local IPC (TCP 127.0.0.1) and forward
 *     state + log frames to the renderer over Electron IPC.
 *   - Persist a small UI prefs file at ~/.config/dashd/ui.json (separate from
 *     the agent config.toml).
 */
import { app, BrowserWindow, ipcMain, dialog } from "electron";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { AgentSupervisor } from "./service";
import { AgentClient } from "./ipc";
import { applyAutostart, getAutostartState } from "./autostart";
import { AppTray } from "./tray";
import { AppUpdater, UpdateState } from "./updater";
import { UsagePopover } from "./usagePopover";

const isDev = !app.isPackaged;
const UI_PREFS_PATH = path.join(os.homedir(), ".config", "dashd", "ui.json");

type UIPrefs = {
  firstRunComplete?: boolean;
  autostart?: boolean;
  ipcPort?: number;
  transportMode?: "cable" | "bluetooth" | "auto";
  // Opt-in for the Anthropic OAuth usage API. Default off — user must
  // explicitly enable in Settings → Privacy. Translated to the agent
  // via the DASHD_ANTHROPIC_OAUTH env var.
  anthropicOAuth?: boolean;
  // Auto-advance: device cycles through enabled pages on a timer with
  // no button press. Default ON (firmware default also true; the UI
  // re-pushes the user's last choice on every agent connect so NVS
  // matches what the user sees in Settings).
  autoAdvanceEnabled?: boolean;       // default true
  autoAdvanceIntervalS?: number;      // default 8, clamped [3, 300] device-side
  autoAdvanceMode?: "sequential" | "random";   // default "sequential"
};

function readUIPrefs(): UIPrefs {
  try {
    return JSON.parse(fs.readFileSync(UI_PREFS_PATH, "utf-8"));
  } catch {
    return {};
  }
}

function writeUIPrefs(prefs: UIPrefs): void {
  fs.mkdirSync(path.dirname(UI_PREFS_PATH), { recursive: true });
  fs.writeFileSync(UI_PREFS_PATH, JSON.stringify(prefs, null, 2));
}

function agentBinaryPath(): string {
  // In dev, point at the PyInstaller output from `pyinstaller dashd-agent.spec`.
  if (isDev) {
    return path.resolve(__dirname, "..", "..", "agent", "dist", "dashd-agent");
  }
  // In a packaged build, electron-builder ships the binary at
  // resources/agent/dashd-agent (see electron-builder.yml).
  const ext = process.platform === "win32" ? ".exe" : "";
  return path.join(process.resourcesPath, "agent", `dashd-agent${ext}`);
}

let mainWindow: BrowserWindow | null = null;
let supervisor: AgentSupervisor | null = null;
let client: AgentClient | null = null;
let tray: AppTray | null = null;
let updater: AppUpdater | null = null;
let usagePopover: UsagePopover | null = null;
let isQuitting = false;
// Latest state frame seen from the agent. Held in main while the window is
// hidden so the renderer can be primed with fresh data the moment it shows,
// without waiting for the next 2 s agent tick.
let lastState: unknown = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 880,
    minHeight: 560,
    title: "dashd",
    backgroundColor: "#0B0B0F",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }

  // Hide instead of quit when the user closes the window — the tray icon
  // keeps the app live in the background. Real quit goes through the tray
  // "Quit dashd" item or the app menu.
  mainWindow.on("close", (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow?.hide();
      tray?.updateStatus({});  // forces menu refresh so "Show window" appears
    }
  });
  mainWindow.on("show", () => {
    tray?.updateStatus({});
    // Tell the agent there's now an active consumer so it bumps the
    // collector tick rate back to the fast cadence.
    client?.sendCmd({ name: "set_active", active: true });
    // Renderer was dark while hidden — prime it with the most recent state
    // so the user sees fresh numbers immediately instead of waiting up to
    // 2 s for the next agent tick.
    if (lastState) mainWindow?.webContents.send("agent:message", lastState);
  });
  mainWindow.on("hide", () => {
    tray?.updateStatus({});
    // Drop our "active" vote; if nothing else is consuming state, the
    // agent will throttle its collectors down to the idle interval.
    client?.sendCmd({ name: "set_active", active: false });
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function isWindowVisible(): boolean {
  return !!(mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible());
}

function showWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function hideWindow(): void {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide();
}

async function maybeFirstRunWizard(prefs: UIPrefs) {
  if (prefs.firstRunComplete) return;
  // Ask once whether to start dashd at login.
  const result = await dialog.showMessageBox({
    type: "question",
    buttons: ["Yes, start at login", "Not now"],
    defaultId: 0,
    cancelId: 1,
    title: "Start dashd at login?",
    message: "Start dashd automatically when you sign in?",
    detail:
      "dashd needs to be running in the background to push data to the device. " +
      "You can change this any time in Settings → General.",
  });
  const enable = result.response === 0;
  await applyAutostart(enable, agentBinaryPath());
  writeUIPrefs({ ...prefs, firstRunComplete: true, autostart: enable });
}

async function bootAgent(prefs: UIPrefs) {
  const port = prefs.ipcPort ?? 52317;
  // Coalesce agent stdio lines into a small batch so a burst (e.g. a Rich
  // traceback) doesn't fire one cross-process IPC + React update per line.
  // Lines are flushed every ~80 ms or as soon as they reach 32 entries —
  // whichever comes first. When the window is hidden, drop them entirely.
  let stdioBuf: string[] = [];
  let stdioTimer: NodeJS.Timeout | null = null;
  const flushStdio = () => {
    stdioTimer = null;
    if (stdioBuf.length === 0) return;
    const batch = stdioBuf;
    stdioBuf = [];
    if (isWindowVisible()) {
      mainWindow?.webContents.send("agent:stdio", batch);
    }
  };

  supervisor = new AgentSupervisor(agentBinaryPath(), {
    args: ["--ipc-port", String(port)],
    ipcPort: port,
    // Pick the device transport (cable/bluetooth/auto). Changing it in the
    // UI rewrites this pref and restarts the agent, so it re-reads it.
    env: {
      DASHD_TRANSPORT: prefs.transportMode ?? "auto",
      DASHD_ANTHROPIC_OAUTH: prefs.anthropicOAuth ? "1" : "0",
    },
    onLog: (line) => {
      if (!isWindowVisible() && stdioBuf.length === 0) return;
      stdioBuf.push(line);
      if (stdioBuf.length >= 32) flushStdio();
      else if (!stdioTimer) stdioTimer = setTimeout(flushStdio, 80);
    },
    onStatusChange: (status) => {
      mainWindow?.webContents.send("agent:status", status);
      tray?.updateStatus({ agentRunning: status.running });
    },
  });
  await supervisor.start();

  // Wait briefly for the agent's IPC listener to be ready, then connect.
  client = new AgentClient(port);
  client.on("message", (msg) => {
    // Always intercept the tray-relevant status event regardless of window
    // visibility — the tray icon stays even when the window is hidden.
    if (msg && msg.type === "event" && msg.name === "agent_status") {
      tray?.updateStatus({ deviceConnected: !!msg.connected });
    }
    // When the window is hidden (tray-mode), the renderer can't paint
    // anything anyway, so dropping high-frequency state frames here saves a
    // structured-clone + cross-process IPC + React reconciliation every 2 s.
    // We cache the latest state in main and replay it the moment the window
    // becomes visible again, so the user never sees the *previous* frame.
    if (msg && msg.type === "state") {
      lastState = msg;
      if (!isWindowVisible()) return;
    }
    mainWindow?.webContents.send("agent:message", msg);
  });
  client.on("connected", () => {
    mainWindow?.webContents.send("agent:ipc", { connected: true });
    // Re-assert visibility on every (re)connect so the agent's idle
    // throttle reflects reality even after a transient disconnect or an
    // attach-to-existing-peer scenario where show/hide events have
    // already fired before the socket came up.
    client?.sendCmd({ name: "set_active", active: isWindowVisible() });
    // Push the user's auto-advance preference so the device's NVS
    // matches what the user sees in Settings. On a fresh install, the
    // pref is `undefined` ⇒ the firmware default (enabled, 8 s,
    // sequential) wins — we deliberately don't push in that case so
    // we don't accidentally overwrite a future firmware default.
    const p = readUIPrefs();
    if (p.autoAdvanceEnabled !== undefined ||
        p.autoAdvanceIntervalS !== undefined ||
        p.autoAdvanceMode !== undefined) {
      client?.sendCmd({
        name: "set_auto_advance",
        enabled: p.autoAdvanceEnabled ?? true,
        interval_s: p.autoAdvanceIntervalS ?? 8,
        mode: p.autoAdvanceMode ?? "sequential",
      });
    }
  });
  client.on("disconnected", () =>
    mainWindow?.webContents.send("agent:ipc", { connected: false })
  );
  await client.connectWithRetry();
}

function registerIPC() {
  ipcMain.handle("ui:getPrefs", () => readUIPrefs());
  ipcMain.handle("ui:setPrefs", (_e, prefs: UIPrefs) => {
    const merged = { ...readUIPrefs(), ...prefs };
    writeUIPrefs(merged);
    return merged;
  });
  ipcMain.handle("agent:sendCmd", (_e, cmd: object) => {
    client?.sendCmd(cmd);
  });
  ipcMain.handle("agent:restart", async () => {
    await supervisor?.restart();
  });
  ipcMain.handle("autostart:get", async () => {
    return getAutostartState();
  });
  ipcMain.handle("autostart:set", async (_e, enable: boolean) => {
    await applyAutostart(enable, agentBinaryPath());
    const prefs = readUIPrefs();
    writeUIPrefs({ ...prefs, autostart: enable });
    return enable;
  });

  // ── App updater ──────────────────────────────────────────────────────
  ipcMain.handle("update:getState", () => ({
    state: updater?.current ?? { phase: "idle" },
    lastChecked: updater?.lastChecked ?? null,
    enabled: !!updater?.enabled,
    currentVersion: app.getVersion(),
  }));
  ipcMain.handle("update:checkNow", () => updater?.checkNow());
  ipcMain.handle("update:download", () => updater?.download());
  ipcMain.handle("update:install", () => updater?.installAndRestart());
  ipcMain.handle("update:openReleases", () => updater?.openReleasesPage());

  // Surface the main window's Usage tab from the popover's "Usage
  // Dashboard" link.
  ipcMain.handle("usage:openDashboard", () => {
    showWindow();
    mainWindow?.webContents.send("nav:to", "usage");
  });
}

app.whenReady().then(async () => {
  registerIPC();
  createWindow();

  // Build the tray BEFORE the agent so the user has a control surface even
  // if the agent fails to spawn.
  tray = new AppTray(process.resourcesPath, {
    showWindow,
    hideWindow,
    restartAgent: () => supervisor?.restart(),
    reloadConfig: () => client?.sendCmd({ name: "reload_config" }),
    getAutostart: () => getAutostartState(),
    setAutostart: async (v) => {
      await applyAutostart(v, agentBinaryPath());
      writeUIPrefs({ ...readUIPrefs(), autostart: v });
      return v;
    },
    quitApp: () => {
      isQuitting = true;
      app.quit();
    },
    checkForUpdates: async () => { await updater?.checkNow(); },
    downloadUpdate: async () => { await updater?.download(); },
    installUpdate: () => { updater?.installAndRestart(); },
    openReleasesPage: async () => { await updater?.openReleasesPage(); },
    toggleUsagePopover: () => {
      if (!usagePopover || !tray) return;
      const sysTray = (tray as any).tray as Electron.Tray | null;
      if (sysTray) usagePopover.toggle(sysTray);
    },
  }, isWindowVisible);
  await tray.init();
  // Create the keep-alive popover BrowserWindow once. Subsequent tray
  // clicks just toggle visibility — no per-click instantiation cost.
  usagePopover = new UsagePopover(isDev, process.env.VITE_DEV_SERVER_URL);

  await maybeFirstRunWizard(readUIPrefs());
  await bootAgent(readUIPrefs());

  // App self-update controller. Wires its event stream into a single
  // renderer channel so the popup + tray menu both react to the same
  // state, and exposes a `current` view so a re-shown window can prime
  // its UI without waiting for the next 6-hour tick.
  updater = new AppUpdater();
  updater.subscribe((state: UpdateState) => {
    mainWindow?.webContents.send("update:state", state);
    tray?.updateUpdate(state);
  });
  updater.start();
});

app.on("window-all-closed", () => {
  // Do NOT quit when the last window closes — the tray icon keeps the app
  // running so the user can still control the agent. Real exit goes through
  // the tray "Quit dashd" item (which sets isQuitting and calls app.quit()).
});

// macOS-specific: Cmd-H hides the app via NSApplication.hide:, which does
// NOT fire BrowserWindow's 'hide' event. We also have to listen at the app
// level so the agent's idle throttle kicks in when the user just Cmd-H's
// instead of explicitly closing the window.
if (process.platform === "darwin") {
  // The Electron typings don't include the macOS-only NSApplication
  // 'hide' / 'show' events on `app`, but the runtime emits them. Cast
  // to bypass the overload signature.
  const macApp = app as unknown as NodeJS.EventEmitter;
  macApp.on("hide", () => {
    client?.sendCmd({ name: "set_active", active: false });
  });
  macApp.on("show", () => {
    client?.sendCmd({ name: "set_active", active: true });
  });
}

app.on("activate", () => {
  // Dock click on macOS, or the user re-opening the app from the tray.
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  } else {
    showWindow();
  }
});

app.on("before-quit", async () => {
  isQuitting = true;
  updater?.stop();
  usagePopover?.destroy();
  client?.close();
  await supervisor?.stop();
  tray?.destroy();
});
