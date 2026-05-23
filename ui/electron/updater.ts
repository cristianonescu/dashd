/**
 * Auto-update controller around `electron-updater`.
 *
 * Two responsibilities:
 *   1. Listen to the autoUpdater lifecycle and forward every event to the
 *      renderer (so the UI popup + tray menu can drive off one stream).
 *   2. Run periodic checks (every 6 h while the app is open) plus an
 *      immediate check on app-ready, so the user is offered fresh builds
 *      without having to think about it.
 *
 * Why a separate module?
 *   - Keeps the (heavily Mac-signing-quirky) auto-update code testable in
 *     isolation, away from the agent supervisor and window plumbing.
 *   - Allows the rest of main.ts to stay agnostic of whether updates are
 *     even available (dev / unpackaged / unsigned-mac builds).
 *
 * macOS caveat:
 *   electron-updater's Squirrel.Mac path requires the app to be code-signed
 *   by a Developer ID Application authority. We currently ship ad-hoc-signed
 *   builds — Squirrel rejects those. To still give Mac users a one-click
 *   experience, we fall back to `shell.openExternal()` on the GitHub release
 *   page whenever Squirrel reports a code-signature error. On Win + Linux
 *   the full silent update path is the default.
 */
import { app, BrowserWindow, shell, dialog } from "electron";

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // every 6 hours
const RELEASES_URL = "https://github.com/cristianonescu/dashd/releases/latest";

// electron-updater is optional at runtime — if missing (dev / unpackaged
// build), the whole controller becomes a graceful no-op.
let autoUpdater: any = null;
try { autoUpdater = require("electron-updater").autoUpdater; } catch {}

export type UpdateState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "not-available"; currentVersion: string }
  | { phase: "available"; version: string; releaseNotes?: string; releaseDate?: string }
  | { phase: "downloading"; percent: number; bytesPerSecond: number; transferred: number; total: number }
  | { phase: "downloaded"; version: string; releaseNotes?: string }
  | { phase: "error"; message: string; fallbackUrl?: string }
  // macOS-only: an update is available, but Squirrel.Mac refuses to
  // install it because our builds aren't Apple-Developer signed. Not
  // really an error — the user just has to manually download the DMG.
  | { phase: "manual-required"; version?: string; reason: string };

type Sink = (s: UpdateState) => void;

export class AppUpdater {
  private state: UpdateState = { phase: "idle" };
  private timer: NodeJS.Timeout | null = null;
  private sinks: Sink[] = [];
  private lastCheckedAt: number | null = null;
  private wired = false;

  /** Whether auto-update is even possible in this build. */
  get enabled(): boolean {
    return !!autoUpdater && app.isPackaged;
  }

  /** Latest seen state — useful to prime newly-opened windows. */
  get current(): UpdateState { return this.state; }
  get lastChecked(): number | null { return this.lastCheckedAt; }

  /** Add a listener. Returns an unsubscribe. */
  subscribe(sink: Sink): () => void {
    this.sinks.push(sink);
    // Replay current state so late subscribers get the right initial view.
    sink(this.state);
    return () => { this.sinks = this.sinks.filter((s) => s !== sink); };
  }

  /** Wire up autoUpdater listeners + start the periodic timer. Idempotent. */
  start(): void {
    if (!this.enabled || this.wired) return;
    this.wired = true;

    // We control the download moment via the UI button; default would
    // auto-download which surprises users with surprise bandwidth use.
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;

    autoUpdater.on("checking-for-update", () => {
      this.lastCheckedAt = Date.now();
      this.set({ phase: "checking" });
    });
    autoUpdater.on("update-available", (info: any) => {
      this.set({
        phase: "available",
        version: info?.version ?? "?",
        releaseNotes: typeof info?.releaseNotes === "string" ? info.releaseNotes : undefined,
        releaseDate: info?.releaseDate,
      });
    });
    autoUpdater.on("update-not-available", () => {
      this.set({ phase: "not-available", currentVersion: app.getVersion() });
    });
    autoUpdater.on("download-progress", (p: any) => {
      this.set({
        phase: "downloading",
        percent: p?.percent ?? 0,
        bytesPerSecond: p?.bytesPerSecond ?? 0,
        transferred: p?.transferred ?? 0,
        total: p?.total ?? 0,
      });
    });
    autoUpdater.on("update-downloaded", (info: any) => {
      this.set({
        phase: "downloaded",
        version: info?.version ?? "?",
        releaseNotes: typeof info?.releaseNotes === "string" ? info.releaseNotes : undefined,
      });
    });
    autoUpdater.on("error", (err: Error) => {
      const msg = err?.message ?? String(err);
      // Squirrel.Mac rejects ad-hoc-signed bundles because the new
      // bundle's designated requirement (a fresh ad-hoc hash) doesn't
      // match the installed bundle's. This is the EXPECTED behavior on
      // macOS for unsigned-by-Apple builds — surface it as a different
      // phase so the UI doesn't shout "FAILED" when there's nothing
      // genuinely wrong, just a missing $99/yr Apple Developer cert.
      const isMacSigError = process.platform === "darwin" &&
        /code sign|signature|signing|did not pass validation|requirement/i.test(msg);
      if (isMacSigError) {
        // Try to preserve the in-flight version if we had announced one.
        const prevVer = this.state.phase === "available"
                        || this.state.phase === "downloaded"
                        ? (this.state as any).version
                        : undefined;
        this.set({
          phase: "manual-required",
          version: prevVer,
          reason: "macOS auto-update requires an Apple Developer Code Signing certificate, which dashd doesn't ship with. You can install the new version manually from the Releases page.",
        });
        return;
      }
      // Other errors keep the "error" phase + fallback link.
      this.set({
        phase: "error",
        message: msg,
        fallbackUrl: process.platform === "darwin" ? RELEASES_URL : undefined,
      });
    });

    // Fire an immediate check + every 6 h thereafter.
    this.checkNow().catch(() => { /* errors already flow through the listener */ });
    this.timer = setInterval(() => {
      this.checkNow().catch(() => { /* ditto */ });
    }, CHECK_INTERVAL_MS);
  }

  /** Manual check (tray menu, Settings → About "Check now"). */
  async checkNow(): Promise<void> {
    if (!this.enabled) {
      // In dev mode we still tell the renderer something so the UI can show
      // "you're running an unpackaged build" rather than spinning forever.
      this.set({ phase: "not-available", currentVersion: app.getVersion() });
      return;
    }
    await autoUpdater.checkForUpdates();
  }

  /** Begin downloading the previously-discovered update. */
  async download(): Promise<void> {
    if (!this.enabled) return;
    try {
      await autoUpdater.downloadUpdate();
    } catch (e: any) {
      this.set({
        phase: "error",
        message: e?.message ?? String(e),
        fallbackUrl: process.platform === "darwin" ? RELEASES_URL : undefined,
      });
    }
  }

  /** Quit + install. Returns immediately; the app will exit. */
  installAndRestart(): void {
    if (!this.enabled) return;
    // electron-updater's quitAndInstall fires before-quit listeners, so the
    // agent supervisor will get a chance to shut down cleanly.
    autoUpdater.quitAndInstall();
  }

  /** Open the GitHub Releases page (mac fallback for unsigned builds). */
  openReleasesPage(): Promise<void> {
    return shell.openExternal(RELEASES_URL);
  }

  /**
   * Show the OS-level "Update ready" prompt. Used when the user clicked
   * "Later" earlier and the update finished downloading in the background.
   */
  async showInstallPrompt(parent: BrowserWindow | null): Promise<void> {
    if (this.state.phase !== "downloaded") return;
    const res = await dialog.showMessageBox(parent ?? undefined as any, {
      type: "info",
      buttons: ["Restart now", "On next quit"],
      defaultId: 0,
      title: `dashd ${this.state.version} ready`,
      message: `dashd ${this.state.version} is ready to install.`,
      detail: "Restart now to apply the update, or keep using the current version and install on next quit.",
    });
    if (res.response === 0) this.installAndRestart();
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private set(s: UpdateState): void {
    this.state = s;
    for (const sink of this.sinks) {
      try { sink(s); } catch { /* swallow listener errors */ }
    }
  }
}
