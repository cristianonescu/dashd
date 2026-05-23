/**
 * Tray-positioned usage popover.
 *
 * Keep-alive frameless BrowserWindow (per Codex review: avoids the
 * ~150-300 ms white-flash latency a per-click create would have on
 * macOS). The window is created once at app-ready, hidden by default,
 * and shown/repositioned at the tray icon on click. Closes on blur
 * (matches codexbar / Linear / Loom convention).
 *
 * The popover renders `UsagePopover.tsx` from the same Vite bundle as
 * the main window, routed via a hash so we can drop into the right
 * React tree without spinning a separate build pipeline.
 */
import { app, BrowserWindow, Tray, screen } from "electron";
import path from "node:path";

const POPOVER_WIDTH = 360;
const POPOVER_HEIGHT = 620;

export class UsagePopover {
  private win: BrowserWindow | null = null;

  /**
   * @param isDev   Dev-mode flag — controls whether we load from the
   *                Vite dev server or the packed renderer bundle.
   * @param viteUrl Dev-server URL when isDev (e.g. http://localhost:5173).
   */
  constructor(private isDev: boolean, private viteUrl: string | undefined) {}

  /** Lazy-create the window so app startup isn't slowed. */
  private ensureWindow(): BrowserWindow {
    if (this.win && !this.win.isDestroyed()) return this.win;
    this.win = new BrowserWindow({
      width: POPOVER_WIDTH,
      height: POPOVER_HEIGHT,
      show: false,
      frame: false,
      transparent: process.platform === "darwin",
      resizable: false,
      skipTaskbar: true,
      // type:'panel' on macOS keeps the window from stealing focus from
      // whatever the user was just in (per Codex). Other OSes ignore it.
      ...(process.platform === "darwin" ? { type: "panel" as const } : {}),
      alwaysOnTop: true,
      backgroundColor: "#0b0b0f",
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    // Route to the popover-mode React entry via URL hash.
    if (this.isDev && this.viteUrl) {
      this.win.loadURL(`${this.viteUrl}#/popover`);
    } else {
      this.win.loadFile(path.join(__dirname, "..", "dist", "index.html"),
                        { hash: "/popover" });
    }

    // Hide instead of close so the keep-alive instance stays around.
    this.win.on("close", (e) => {
      if (this.win) {
        e.preventDefault();
        this.win.hide();
      }
    });
    this.win.on("blur", () => this.win?.hide());

    return this.win;
  }

  /**
   * Show the popover positioned at the tray icon, or hide it if it's
   * currently visible (toggle behavior on tray click).
   */
  toggle(tray: Tray): void {
    const win = this.ensureWindow();
    if (win.isVisible()) {
      win.hide();
      return;
    }
    const bounds = tray.getBounds();
    const display = screen.getDisplayMatching(bounds);
    let x = Math.round(bounds.x + bounds.width / 2 - POPOVER_WIDTH / 2);
    let y: number;
    if (process.platform === "darwin") {
      // macOS menu-bar is at the top; popover hangs below the icon.
      y = Math.round(bounds.y + bounds.height + 4);
    } else {
      // Windows / Linux taskbar is usually at the bottom; popover sits
      // above the icon.
      y = Math.round(bounds.y - POPOVER_HEIGHT - 4);
    }
    // Clamp to the display so the popover doesn't half-clip on tray
    // icons near the screen edge.
    const work = display.workArea;
    if (x + POPOVER_WIDTH > work.x + work.width) {
      x = work.x + work.width - POPOVER_WIDTH - 4;
    }
    if (x < work.x) x = work.x + 4;
    win.setBounds({ x, y, width: POPOVER_WIDTH, height: POPOVER_HEIGHT });
    win.show();
    win.focus();
  }

  destroy(): void {
    if (this.win && !this.win.isDestroyed()) {
      // Tear down listeners that would block the close.
      this.win.removeAllListeners("close");
      this.win.close();
    }
    this.win = null;
  }
}
