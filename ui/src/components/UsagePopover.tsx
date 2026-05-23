/**
 * Tray popover view — opens when the user clicks the dashd tray icon.
 *
 * Mirrors codexbar's at-a-glance layout: header → Session bar →
 * Weekly bar (+ pace) → Sonnet bar → Extra usage bar → Cost summary
 * → footer actions. Renders inside a frameless BrowserWindow opened
 * by `usagePopover.ts` in the main process.
 *
 * The data comes from the same atoms the main window uses ($anthropic
 * + $state), so dim states (token expired, agent down) propagate
 * automatically.
 */
import { useAtom, $anthropic, $state, $agentRunning } from "../store";
import { wireAgentBridge } from "../store";
import { useEffect } from "react";
import { UsageCard } from "./UsageCard";

function fmtTokens(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(1) + "k";
  return (n / 1_000_000).toFixed(2) + "M";
}

export default function UsagePopoverApp() {
  // The popover is its own BrowserWindow — it needs its own bridge wire.
  useEffect(() => { wireAgentBridge(); }, []);

  const anth = useAtom($anthropic);
  const state = useAtom($state);
  const agentUp = useAtom($agentRunning);
  const cc: any = state?.ai?.claude_code ?? {};

  return (
    <div className="popover">
      <header className="popover-head">
        <div className="popover-title">
          <strong>Claude</strong>
          <span className="dim popover-sub">
            {agentUp ? "Live" : "Agent down"}
          </span>
        </div>
        {anth?.available === false && (
          <div className="popover-warn dim">
            {anth.reason === "disabled"
              ? "OAuth API disabled — using local-only metrics."
              : anth.reason === "401"
                ? "Token expired — run `claude login`."
                : anth.reason === "no_token"
                  ? "No Claude Code token found."
                  : `Unavailable: ${anth.reason}`}
          </div>
        )}
      </header>

      <UsageCard title="Session" subtitle="5h"
                 window={anth?.session} showPace compact />
      <UsageCard title="Weekly" subtitle="7d"
                 window={anth?.weekly} showPace compact />
      <UsageCard title="Sonnet" subtitle="7d"
                 window={anth?.sonnet_weekly} compact />

      {anth?.extra_usage?.enabled && (
        <div className="usage-card compact">
          <div className="usage-card-head">
            <h4 className="usage-card-title">Extra usage</h4>
            <span className="usage-card-subtitle">monthly</span>
          </div>
          <div className="usage-bar"
               data-variant={anth.extra_usage.used_pct >= 75 ? "warn" : "low"}>
            <div className="usage-bar-fill"
                 style={{ width: `${Math.round(anth.extra_usage.used_pct)}%` }} />
          </div>
          <div className="usage-card-meta">
            <span>
              ${anth.extra_usage.used_usd.toFixed(2)} /
              ${anth.extra_usage.limit_usd.toFixed(2)}
            </span>
            <span className="dim">
              {anth.extra_usage.used_pct.toFixed(0)}% used
            </span>
          </div>
        </div>
      )}

      <div className="popover-cost">
        <div className="popover-cost-row">
          <span className="dim">Today</span>
          <span>${cc.cost_today_usd?.toFixed(2) ?? "0.00"} · {fmtTokens(cc.tokens_today)} tokens</span>
        </div>
        <div className="popover-cost-row">
          <span className="dim">Last 7 days</span>
          <span>${cc.cost_this_week_usd?.toFixed(2) ?? "0.00"} · {fmtTokens(cc.tokens_this_week)} tokens</span>
        </div>
      </div>

      <footer className="popover-foot">
        <button className="popover-link"
                onClick={() => window.dashd.openUsageDashboard?.()}>
          Usage Dashboard
        </button>
        <button className="popover-link"
                onClick={() => window.dashd.openReleasesPage()}>
          Releases
        </button>
      </footer>
    </div>
  );
}
