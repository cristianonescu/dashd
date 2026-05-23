import { useEffect, useState } from "react";
import StatusBar from "./components/StatusBar";
import LiveView from "./components/LiveView";
import LogsPanel from "./components/LogsPanel";
import Settings from "./components/Settings";
import HintLayer from "./components/HintLayer";
import UpdateBanner from "./components/UpdateBanner";
import UsageView from "./components/UsageView";
import UsagePopoverApp from "./components/UsagePopover";
import { wireAgentBridge } from "./store";

type Tab = "live" | "usage" | "settings";

const TAB_HINTS: Record<Tab, string> = {
  live: "Live dashboard — real-time system, AI, git, GitHub and calendar metrics, plus top processes and tuning suggestions.",
  usage: "Claude + Codex usage gauges (Session / Weekly / Sonnet) sourced from Anthropic's OAuth API when opted in, plus local token totals and project breakdown.",
  settings: "Configure the device: collectors, theme, page order, screen elements, layout, the pet overlay and button actions.",
};

export default function App() {
  // The popover mode is selected by the BrowserWindow URL hash so we
  // can ship one Vite bundle that renders either the full tabbed UI
  // or the compact tray popover. Route once at mount.
  if (typeof window !== "undefined" && window.location.hash === "#/popover") {
    return <UsagePopoverApp />;
  }
  const [tab, setTab] = useState<Tab>("live");
  // VSCode-style logs dock — togglable, overlays the bottom of whichever
  // tab is active, so the user can watch logs while looking at Live or
  // Settings without losing their place.
  const [logsOpen, setLogsOpen] = useState(false);
  useEffect(() => { wireAgentBridge(); }, []);
  // Honor nav requests from the main process (e.g. popover → Usage tab).
  useEffect(() => {
    const off = window.dashd.onNav?.((target) => {
      if (target === "usage" || target === "live" || target === "settings") {
        setTab(target);
      }
    });
    return () => { off?.(); };
  }, []);

  return (
    <div className={`app ${logsOpen ? "logs-open" : ""}`}>
      <div className="chrome">
        <StatusBar />
        <div className="segmented" role="tablist">
          {(["live", "usage", "settings"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`seg ${tab === t ? "active" : ""}`}
              onClick={() => setTab(t)}
              data-hint={TAB_HINTS[t]}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {tab === "live"     && <LiveView />}
      {tab === "usage"    && <UsageView />}
      {tab === "settings" && <Settings />}

      {logsOpen && (
        <div className="logs-dock">
          <LogsPanel onClose={() => setLogsOpen(false)} />
        </div>
      )}

      <button
        className={`logs-toggle ${logsOpen ? "active" : ""}`}
        onClick={() => setLogsOpen((v) => !v)}
        aria-pressed={logsOpen}
        data-hint={logsOpen
          ? "Hide the logs panel."
          : "Show the agent + firmware log stream at the bottom — toggleable over any tab."}
      >
        <span className="logs-toggle-chev">{logsOpen ? "▾" : "▴"}</span>
        Logs
      </button>

      <UpdateBanner />
      <HintLayer />
    </div>
  );
}
