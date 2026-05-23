/**
 * Floating "update available" / "update ready" banner.
 *
 * Shown in the top-right corner of the app whenever the updater state is
 * `available`, `downloading`, or `downloaded`. Dismissible per-state — but
 * a fresh state push re-shows it (e.g. download finishes while dismissed →
 * the "Restart and install" version re-appears, which is the desired
 * behaviour: we want the user to know the install is one click away).
 */
import { useEffect, useState } from "react";

type UpdateState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "not-available"; currentVersion: string }
  | { phase: "available"; version: string; releaseNotes?: string; releaseDate?: string }
  | { phase: "downloading"; percent: number; bytesPerSecond: number; transferred: number; total: number }
  | { phase: "downloaded"; version: string; releaseNotes?: string }
  | { phase: "error"; message: string; fallbackUrl?: string }
  | { phase: "manual-required"; version?: string; reason: string };

export default function UpdateBanner() {
  const [state, setState] = useState<UpdateState>({ phase: "idle" });
  const [dismissedPhase, setDismissedPhase] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const s = await window.dashd.getUpdateState();
      setState(s.state);
    })();
    return window.dashd.onUpdate((s: UpdateState) => {
      setState(s);
      // A new phase resurfaces the banner.
      setDismissedPhase((prev) => (prev === s.phase ? prev : null));
    });
  }, []);

  // Only certain phases are bannered. Errors stay quiet — they're surfaced
  // in Settings → Updates instead, so we don't yell at users who happen to
  // be offline. `manual-required` (the mac unsigned-update case) IS
  // bannered because it represents a real update the user should know
  // about; it's just installed manually rather than auto.
  const visible = ["available", "downloading", "downloaded", "manual-required"]
    .includes(state.phase) && dismissedPhase !== state.phase;
  if (!visible) return null;

  return (
    <div className="update-banner" role="status" aria-live="polite">
      {state.phase === "available" && (
        <>
          <span className="update-banner-text">
            <strong>dashd {state.version}</strong> available
          </span>
          <button
            className="btn primary"
            onClick={() => window.dashd.downloadUpdate()}
            data-hint="Download in the background; you'll be prompted before it installs."
          >Download</button>
          <button
            className="btn"
            onClick={() => setDismissedPhase(state.phase)}
            data-hint="Hide this notice. It'll come back if a new state arrives."
          >Later</button>
        </>
      )}
      {state.phase === "downloading" && (
        <>
          <span className="update-banner-text">
            Downloading update… {Math.round(state.percent)}%
          </span>
          <progress
            value={state.percent}
            max={100}
            style={{ width: 120 }}
          />
        </>
      )}
      {state.phase === "downloaded" && (
        <>
          <span className="update-banner-text">
            <strong>{state.version}</strong> ready to install
          </span>
          <button
            className="btn primary"
            onClick={() => window.dashd.installUpdate()}
            data-hint="Restart dashd now and install the new version."
          >Restart & install</button>
          <button
            className="btn"
            onClick={() => setDismissedPhase(state.phase)}
            data-hint="Install on the next quit instead — keep working for now."
          >Later</button>
        </>
      )}
      {state.phase === "manual-required" && (
        <>
          <span className="update-banner-text">
            {state.version
              ? <><strong>dashd {state.version}</strong> available — manual install on macOS</>
              : <>Update available — manual install on macOS</>}
          </span>
          <button
            className="btn primary"
            onClick={() => window.dashd.openReleasesPage()}
            data-hint="Open the Releases page so you can grab the new DMG. macOS in-app auto-install isn't possible on unsigned builds."
          >Open Releases</button>
          <button
            className="btn"
            onClick={() => setDismissedPhase(state.phase)}
            data-hint="Hide this notice. It'll come back the next time an update is detected."
          >Later</button>
        </>
      )}
    </div>
  );
}
