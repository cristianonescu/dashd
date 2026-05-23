/**
 * Settings → Updates
 *
 * Two cards: the dashd Electron app (driven by electron-updater) and the
 * device firmware (driven by the agent's fw_update_* IPC). Both have the
 * same shape — current version, "Check now", "Install", progress.
 */
import { useEffect, useState } from "react";
import { useAtom, $deviceFwVersion, $fwUpdate, $deviceConnected, $transport } from "../store";


/**
 * Render release-notes content from electron-updater. The GitHub provider
 * returns it as an HTML fragment scraped from the Releases atom feed —
 * if we render it inside <pre> as plain text the user sees raw HTML
 * source. Detect HTML and inject it; otherwise treat as preformatted
 * markdown/plain text.
 *
 * Safety: the source is the project's own GitHub release body, which
 * the maintainer writes. We control it. The risk surface of using
 * dangerouslySetInnerHTML here is bounded to "the maintainer puts a
 * bad <script> in their own release notes" which is a non-attack.
 */
function ReleaseNotesBody({ html }: { html: string }) {
  // Heuristic: if it has a closing HTML tag or an angle bracket followed
  // by a known tag-name char, treat as HTML. Markdown rarely has these.
  const looksLikeHtml = /<\/[a-z]+>|<[a-z][^>]*>/i.test(html);
  const baseStyle = {
    fontSize: 12,
    lineHeight: 1.4,
    padding: 8,
    background: "var(--surface-2)",
    borderRadius: 6,
    margin: "6px 0 0",
    maxHeight: 200,
    overflow: "auto",
  } as const;
  if (looksLikeHtml) {
    return (
      <div
        className="release-notes-html"
        style={baseStyle}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  return (
    <pre style={{ ...baseStyle, whiteSpace: "pre-wrap" }}>{html}</pre>
  );
}

type UpdateState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "not-available"; currentVersion: string }
  | { phase: "available"; version: string; releaseNotes?: string; releaseDate?: string }
  | { phase: "downloading"; percent: number; bytesPerSecond: number; transferred: number; total: number }
  | { phase: "downloaded"; version: string; releaseNotes?: string }
  | { phase: "error"; message: string; fallbackUrl?: string }
  | { phase: "manual-required"; version?: string; reason: string };

type Snapshot = {
  state: UpdateState;
  lastChecked: number | null;
  enabled: boolean;
  currentVersion: string;
};

function formatRelative(ts: number | null): string {
  if (!ts) return "never";
  const dt = Date.now() - ts;
  if (dt < 60_000) return "just now";
  if (dt < 3_600_000) return `${Math.floor(dt / 60_000)} min ago`;
  if (dt < 86_400_000) return `${Math.floor(dt / 3_600_000)} h ago`;
  return new Date(ts).toLocaleDateString();
}

export default function UpdatesPane() {
  const [snap, setSnap] = useState<Snapshot>({
    state: { phase: "idle" },
    lastChecked: null,
    enabled: false,
    currentVersion: "?",
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const s = await window.dashd.getUpdateState();
      if (!cancelled) setSnap(s);
    })();
    const off = window.dashd.onUpdate((state: UpdateState) => {
      setSnap((prev) => ({
        ...prev,
        state,
        // The main process bumps lastChecked itself; pull it lazily on
        // every state push so the renderer stays in sync.
        lastChecked:
          state.phase === "checking" ? Date.now() : prev.lastChecked,
      }));
      setBusy(false);
    });
    return () => { cancelled = true; off(); };
  }, []);

  // Every IPC handler resets busy on its own. The natural reset path is
  // the next `update:state` push (handled in the effect above), but we
  // also catch + reset here so a thrown IPC doesn't strand the spinner.
  const onCheck = async () => {
    setBusy(true);
    try { await window.dashd.checkForUpdates(); } catch { setBusy(false); }
  };
  const onDownload = async () => {
    setBusy(true);
    try { await window.dashd.downloadUpdate(); } catch { setBusy(false); }
  };
  const onInstall = () => { try { window.dashd.installUpdate(); } catch { /* no-op */ } };
  const onOpenPage = () => { try { window.dashd.openReleasesPage(); } catch { /* no-op */ } };

  const { state, currentVersion, lastChecked, enabled } = snap;

  return (
    <>
      <div className="card">
        <h3 data-hint="Auto-update is checked every 6 hours; you can also trigger a check manually here.">
          dashd app
        </h3>
        <div className="proc-row">
          <span className="name">
            Current version
            <span className="count">{currentVersion}</span>
          </span>
          <span className="value dim" style={{ fontSize: 12 }}>
            Last checked: {formatRelative(lastChecked)}
          </span>
        </div>

        {!enabled && (
          <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>
            Auto-update is disabled in this build (likely a dev / unpackaged run).
          </p>
        )}

        {state.phase === "checking" && (
          <p className="dim" style={{ fontSize: 13, marginTop: 8 }}>Checking GitHub Releases…</p>
        )}

        {state.phase === "not-available" && (
          <p className="dim" style={{ fontSize: 13, marginTop: 8 }}>
            You're on the latest version.
          </p>
        )}

        {state.phase === "available" && (
          <div style={{ marginTop: 8 }}>
            <p style={{ fontSize: 13, margin: "0 0 4px" }}>
              <strong>{state.version}</strong> available
              {state.releaseDate && (
                <span className="dim" style={{ marginLeft: 8 }}>
                  ({new Date(state.releaseDate).toLocaleDateString()})
                </span>
              )}
            </p>
            {state.releaseNotes && (
              <details style={{ margin: "6px 0" }}>
                <summary className="dim" style={{ cursor: "pointer", fontSize: 12 }}>
                  Release notes
                </summary>
                <ReleaseNotesBody html={state.releaseNotes} />
              </details>
            )}
          </div>
        )}

        {state.phase === "downloading" && (
          <div style={{ marginTop: 8 }}>
            <progress
              value={Math.max(0, Math.min(100, state.percent))}
              max={100}
              style={{ width: "100%" }}
            />
            <p className="dim" style={{ fontSize: 12, margin: "4px 0 0" }}>
              {Math.round(state.percent)}% · {(state.bytesPerSecond / 1024 / 1024).toFixed(2)} MB/s
            </p>
          </div>
        )}

        {state.phase === "downloaded" && (
          <p style={{ fontSize: 13, marginTop: 8 }}>
            <strong>{state.version}</strong> downloaded — restart to install.
          </p>
        )}

        {state.phase === "error" && (
          <div className="crit" style={{ marginTop: 8, fontSize: 12 }}>
            <p style={{ margin: 0 }}>Update check failed: {state.message}</p>
            {state.fallbackUrl && (
              <p style={{ margin: "4px 0 0" }}>
                You can <a href="#" onClick={(e) => { e.preventDefault(); onOpenPage(); }}>download from GitHub</a> instead.
              </p>
            )}
          </div>
        )}

        {state.phase === "manual-required" && (
          <div className="dim" style={{ marginTop: 8, fontSize: 12 }}>
            <p style={{ margin: 0 }}>
              {state.version
                ? <>dashd <strong>{state.version}</strong> is available, but the macOS auto-installer can't run on unsigned builds.</>
                : <>An update is available, but the macOS auto-installer can't run on unsigned builds.</>}
            </p>
            <p style={{ margin: "4px 0 0" }}>
              Click <a href="#" onClick={(e) => { e.preventDefault(); onOpenPage(); }}>Download from GitHub</a> to install the new DMG manually — that's the supported path on macOS until dashd ships with an Apple Developer signature.
            </p>
          </div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <button
            className="btn"
            onClick={onCheck}
            disabled={busy || state.phase === "checking"}
            data-hint="Ask GitHub Releases for a newer version right now."
          >
            {state.phase === "checking" ? "Checking…" : "Check for updates"}
          </button>
          {state.phase === "available" && (
            <button
              className="btn primary"
              onClick={onDownload}
              disabled={busy}
              data-hint="Download the new version. You'll be prompted before it installs."
            >Download</button>
          )}
          {state.phase === "downloaded" && (
            <button
              className="btn primary"
              onClick={onInstall}
              data-hint="Restart dashd and install the new version."
            >Restart and install</button>
          )}
          <button
            className="btn"
            onClick={onOpenPage}
            data-hint="Open the GitHub Releases page in your browser — useful if auto-update can't reach GitHub."
          >Open release page</button>
        </div>
      </div>

      <FirmwareCard />
    </>
  );
}

/**
 * Device firmware update card. Mirrors the app card's shape but talks to
 * the agent (via sendCmd) rather than electron-updater. Phases:
 *
 *   idle / up_to_date  →  [Check for updates]
 *   available          →  [Update firmware]   (shows version + notes)
 *   downloading        →  spinner / bytes
 *   flashing           →  big progress bar (bytes / total)
 *   rebooting          →  "Device is restarting…" (link will drop briefly)
 *   done               →  "Now on v0.1.2" (sticky until next check)
 *   error              →  message + retry
 */
function FirmwareCard() {
  const deviceFw = useAtom($deviceFwVersion);
  const fw = useAtom($fwUpdate);
  const connected = useAtom($deviceConnected);
  const transport = useAtom($transport);
  const [busy, setBusy] = useState(false);

  // Whenever the device emits a new state event we leave busy mode —
  // the stream is the source of truth.
  useEffect(() => { setBusy(false); }, [fw]);

  // sendCmd is fire-and-forget — the response lands as a fw_update_state
  // event handled by the store, which resets busy via the effect above.
  // We still guard each call so a torn-down IPC (agent crashed mid-flight)
  // doesn't leave the spinner permanently on.
  const onCheck = () => {
    setBusy(true);
    try { window.dashd.sendCmd({ name: "fw_check_update" }); }
    catch { setBusy(false); }
  };
  const onUpdate = () => {
    if (!confirm(
      `Update the device firmware to ${(fw as any).latest ?? "the latest version"}?\n\n` +
      "The device will reboot when finished. Don't unplug it during the update.")) {
      return;
    }
    setBusy(true);
    try { window.dashd.sendCmd({ name: "fw_update_start" }); }
    catch { setBusy(false); }
  };
  const onAbort = () => {
    try { window.dashd.sendCmd({ name: "fw_update_abort" }); } catch { /* no-op */ }
  };

  const bytes = (fw as any).bytes ?? 0;
  const total = (fw as any).total ?? 0;
  const pct = total > 0 ? Math.round((bytes / total) * 100) : 0;

  return (
    <div className="card" style={{ marginTop: 12 }}>
      <h3 data-hint="Update the firmware on the connected dashd device. Streams the new image over the active USB or BLE link.">
        Device firmware
      </h3>

      <div className="proc-row">
        <span className="name">
          Current version
          <span className="count">{deviceFw || "—"}</span>
        </span>
        <span className="value dim" style={{ fontSize: 12 }}>
          {connected
            ? `connected via ${transport || "?"}`
            : "no device connected"}
        </span>
      </div>

      {!connected && (
        <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>
          Connect a dashd device (cable or Bluetooth) before checking for firmware updates.
        </p>
      )}

      {fw.state === "checking" && (
        <p className="dim" style={{ fontSize: 13, marginTop: 8 }}>Checking GitHub Releases…</p>
      )}
      {fw.state === "up_to_date" && (
        <p className="dim" style={{ fontSize: 13, marginTop: 8 }}>
          Device firmware is up to date ({fw.current}).
        </p>
      )}
      {fw.state === "available" && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: 13, margin: "0 0 4px" }}>
            <strong>{fw.latest}</strong> available — currently on <strong>{fw.current}</strong>.
          </p>
          {fw.notes && (
            <details style={{ margin: "6px 0" }}>
              <summary className="dim" style={{ cursor: "pointer", fontSize: 12 }}>Release notes</summary>
              <pre style={{
                whiteSpace: "pre-wrap", fontSize: 12, lineHeight: 1.4,
                padding: 8, background: "var(--surface-2)", borderRadius: 6,
                margin: "6px 0 0", maxHeight: 200, overflow: "auto",
              }}>{fw.notes}</pre>
            </details>
          )}
        </div>
      )}
      {fw.state === "downloading" && (
        <div style={{ marginTop: 8 }}>
          <p className="dim" style={{ fontSize: 12, margin: 0 }}>
            Downloading firmware from GitHub…
            {(total ?? 0) > 0 && ` ${Math.round((bytes / total) * 100)}%`}
          </p>
          {(total ?? 0) > 0 && (
            <progress value={bytes} max={total} style={{ width: "100%", marginTop: 4 }} />
          )}
        </div>
      )}
      {fw.state === "flashing" && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: 13, margin: "0 0 4px" }}>
            Flashing <strong>{fw.version}</strong> — do not unplug the device.
          </p>
          <progress value={bytes} max={total || 1} style={{ width: "100%" }} />
          <p className="dim" style={{ fontSize: 12, margin: "4px 0 0" }}>
            {Math.round(bytes / 1024)} / {Math.round((total || 0) / 1024)} KB ({pct}%)
          </p>
        </div>
      )}
      {fw.state === "rebooting" && (
        <p style={{ fontSize: 13, marginTop: 8 }}>
          Device is restarting into <strong>{fw.version}</strong>… the link will be back in a few seconds.
        </p>
      )}
      {fw.state === "done" && (
        <p style={{ fontSize: 13, marginTop: 8 }}>
          ✓ Firmware updated to <strong>{fw.version}</strong>.
        </p>
      )}
      {fw.state === "error" && (
        <div className="crit" style={{ marginTop: 8, fontSize: 12 }}>
          Update failed: {fw.error}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <button
          className="btn"
          onClick={onCheck}
          disabled={!connected || busy || fw.state === "checking"
                                       || fw.state === "downloading"
                                       || fw.state === "flashing"
                                       || fw.state === "rebooting"}
          data-hint="Ask GitHub Releases for a newer firmware version."
        >
          {fw.state === "checking" ? "Checking…" : "Check for updates"}
        </button>
        {fw.state === "available" && (
          <button
            className="btn primary"
            onClick={onUpdate}
            disabled={busy}
            data-hint="Download and flash the new firmware. The device reboots when done."
          >Update firmware</button>
        )}
        {(fw.state === "downloading" || fw.state === "flashing") && (
          <button
            className="btn"
            onClick={onAbort}
            data-hint="Stop the update. The current firmware keeps running on next reboot."
          >Cancel</button>
        )}
      </div>
    </div>
  );
}
