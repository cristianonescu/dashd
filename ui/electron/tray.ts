/**
 * Menu-bar / system-tray controller.
 *
 * Renders a tiny dashd icon next to the OS clock with a right-click menu:
 *   - status header (agent + device connection)
 *   - Show / Hide window
 *   - Restart agent
 *   - Reload config
 *   - Start at login (checkbox)
 *   - Quit dashd
 *
 * The icon is shipped as a template image (trayTemplate*.png) — on macOS
 * the framework auto-inverts it for the current menu-bar theme. On
 * Windows / Linux the same image renders as a normal 16/32 px icon.
 */
import { Tray, Menu, app, BrowserWindow, nativeImage } from "electron";
import path from "node:path";
import type { UpdateState } from "./updater";

export type TrayHandlers = {
  showWindow: () => void;
  hideWindow: () => void;
  restartAgent: () => void;
  reloadConfig: () => void;
  getAutostart: () => Promise<boolean>;
  setAutostart: (v: boolean) => Promise<boolean>;
  quitApp: () => void;
  // Update flow — all wired to the AppUpdater controller in main.
  checkForUpdates: () => Promise<void>;
  downloadUpdate: () => Promise<void>;
  installUpdate: () => void;
  openReleasesPage: () => Promise<void>;
  // Usage popover toggle. The handler decides whether to show / hide /
  // reposition the popover BrowserWindow.
  toggleUsagePopover: () => void;
};

export type TrayStatus = {
  agentRunning: boolean;
  deviceConnected: boolean;
};

export class AppTray {
  private tray: Tray | null = null;
  private status: TrayStatus = { agentRunning: false, deviceConnected: false };
  private autostart = false;
  private update: UpdateState = { phase: "idle" };

  constructor(
    private resourcesDir: string,
    private handlers: TrayHandlers,
    private isWindowVisible: () => boolean,
  ) {}

  async init(): Promise<void> {
    const icon = this.loadIcon();
    this.tray = new Tray(icon);
    this.tray.setToolTip("dashd");
    this.autostart = await this.handlers.getAutostart();
    this.refreshMenu();

    // Left-click on any OS: open the usage popover. Right-click is
    // still the full menu (set via tray.setContextMenu in refreshMenu).
    // This matches codexbar / Linear / Loom — click = quick view,
    // right-click = full controls.
    this.tray.on("click", () => {
      this.handlers.toggleUsagePopover();
    });
  }

  private loadIcon(): Electron.NativeImage {
    // electron-builder ships build/* into Resources/build/ when included via
    // electron-builder.yml `files`. In dev we read straight from build/.
    const baseDev = path.resolve(__dirname, "..", "build");
    const basePkg = path.join(this.resourcesDir, "build");
    const tryDirs = [baseDev, basePkg];
    for (const dir of tryDirs) {
      const p = path.join(dir, "trayTemplate.png");
      const img = nativeImage.createFromPath(p);
      if (!img.isEmpty()) {
        if (process.platform === "darwin") img.setTemplateImage(true);
        return img;
      }
    }
    // Fallback: empty image avoids a crash, the icon just won't appear.
    return nativeImage.createEmpty();
  }

  updateStatus(status: Partial<TrayStatus>): void {
    this.status = { ...this.status, ...status };
    this.refreshMenu();
  }

  /** Called by the AppUpdater on every state change. */
  updateUpdate(state: UpdateState): void {
    this.update = state;
    this.refreshMenu();
  }

  async refreshAutostart(): Promise<void> {
    this.autostart = await this.handlers.getAutostart();
    this.refreshMenu();
  }

  private refreshMenu(): void {
    if (!this.tray) return;
    const agentDot = this.status.agentRunning ? "●" : "○";
    const deviceDot = this.status.deviceConnected ? "●" : "○";
    const visible = this.isWindowVisible();

    const menu = Menu.buildFromTemplate([
      { label: "dashd", enabled: false },
      { type: "separator" },
      { label: `${agentDot}  Agent ${this.status.agentRunning ? "running" : "stopped"}`, enabled: false },
      { label: `${deviceDot}  Device ${this.status.deviceConnected ? "connected" : "disconnected"}`, enabled: false },
      { type: "separator" },
      visible
        ? { label: "Hide window", click: () => this.handlers.hideWindow() }
        : { label: "Show window", click: () => this.handlers.showWindow() },
      { label: "Restart agent", click: () => this.handlers.restartAgent() },
      { label: "Reload config", click: () => this.handlers.reloadConfig() },
      { type: "separator" },
      ...this.updateMenuItems(),
      { type: "separator" },
      {
        label: "Start at login",
        type: "checkbox",
        checked: this.autostart,
        click: async (item) => {
          await this.handlers.setAutostart(item.checked);
          this.autostart = item.checked;
          this.refreshMenu();
        },
      },
      { type: "separator" },
      { label: `dashd ${app.getVersion()}`, enabled: false },
      { label: "Quit dashd", click: () => this.handlers.quitApp() },
    ]);
    this.tray.setContextMenu(menu);
  }

  /**
   * Build the dynamic update menu entries based on the current phase.
   * One line of state ("checking…", "v0.1.2 ready") + one or two action
   * lines that change depending on what the user can do right now.
   */
  private updateMenuItems(): Electron.MenuItemConstructorOptions[] {
    const u = this.update;
    const items: Electron.MenuItemConstructorOptions[] = [];

    switch (u.phase) {
      case "idle":
      case "not-available":
        items.push({
          label: "Check for updates…",
          click: () => { this.handlers.checkForUpdates().catch(() => { /* errors flow through state */ }); },
        });
        break;
      case "checking":
        items.push({ label: "Checking for updates…", enabled: false });
        break;
      case "available":
        items.push({ label: `▾  ${u.version} available`, enabled: false });
        items.push({
          label: "Download update",
          click: () => { this.handlers.downloadUpdate().catch(() => { /* */ }); },
        });
        items.push({
          label: "Open release page",
          click: () => { this.handlers.openReleasesPage().catch(() => { /* */ }); },
        });
        break;
      case "downloading": {
        const pct = Math.round(u.percent ?? 0);
        items.push({ label: `Downloading update…  ${pct}%`, enabled: false });
        break;
      }
      case "downloaded":
        items.push({ label: `▾  ${u.version} ready`, enabled: false });
        items.push({
          label: "Restart and install",
          click: () => this.handlers.installUpdate(),
        });
        break;
      case "error":
        items.push({ label: "Update check failed", enabled: false });
        if (u.fallbackUrl) {
          items.push({
            label: "Open release page",
            click: () => { this.handlers.openReleasesPage().catch(() => { /* */ }); },
          });
        } else {
          items.push({
            label: "Try again",
            click: () => { this.handlers.checkForUpdates().catch(() => { /* */ }); },
          });
        }
        break;
      case "manual-required":
        items.push({
          label: u.version
            ? `▾  ${u.version} available (manual install)`
            : "Update available (manual install)",
          enabled: false,
        });
        items.push({
          label: "Open release page",
          click: () => { this.handlers.openReleasesPage().catch(() => { /* */ }); },
        });
        break;
    }
    return items;
  }

  destroy(): void {
    this.tray?.destroy();
    this.tray = null;
  }
}
