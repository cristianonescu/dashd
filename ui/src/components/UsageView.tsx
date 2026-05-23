/**
 * Settings → Usage tab (the deep-dive view).
 *
 * Mirrors the popover layout at the top, then adds dashd-only sections
 * below (top projects, per-model breakdown, cost summary). All powered
 * by the same $anthropic + $state atoms — no new IPC required.
 *
 * When the user hasn't opted into the OAuth API, this view degrades
 * gracefully: shows a one-time enable prompt at the top and the
 * JSONL-derived stats below.
 */
import { useAtom, $anthropic, $state } from "../store";
import { UsageCard } from "./UsageCard";

function fmtTokens(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(1) + "k";
  return (n / 1_000_000).toFixed(2) + "M";
}

export default function UsageView() {
  const anth = useAtom($anthropic);
  const state = useAtom($state);
  const cc: any = state?.ai?.claude_code ?? {};
  const cx: any = state?.ai?.codex ?? {};

  const enabled = anth?.available;

  return (
    <div className="pane usage-view">
      {!enabled && (
        <div className="card usage-enable-card">
          <h3>Anthropic Usage API</h3>
          <p className="dim" style={{ fontSize: 13 }}>
            For Session / Weekly / Sonnet gauges that match Claude.ai
            exactly, dashd needs read access to your local Claude Code
            OAuth token. It stays in memory, is never logged, never
            written to disk. Opt in via Settings → Privacy.
          </p>
          {anth && (
            <p className="dim" style={{ fontSize: 12 }}>
              Current reason: <code>{anth.reason ?? "—"}</code>
            </p>
          )}
        </div>
      )}

      {/* Codexbar-style top section — gauges */}
      <div className="usage-grid">
        <UsageCard title="Session" subtitle="5h"
                   window={anth?.session} showPace />
        <UsageCard title="Weekly" subtitle="7d"
                   window={anth?.weekly} showPace />
        <UsageCard title="Sonnet" subtitle="7d"
                   window={anth?.sonnet_weekly} />
        {anth?.extra_usage?.enabled && (
          <div className="usage-card">
            <div className="usage-card-head">
              <h4 className="usage-card-title">Extra usage</h4>
              <span className="usage-card-subtitle">monthly</span>
            </div>
            <div className="usage-bar" data-variant={
              anth.extra_usage.used_pct >= 90 ? "crit" :
              anth.extra_usage.used_pct >= 75 ? "warn" :
              anth.extra_usage.used_pct >= 50 ? "ok" : "low"
            }>
              <div className="usage-bar-fill"
                   style={{ width: `${Math.round(anth.extra_usage.used_pct)}%` }} />
            </div>
            <div className="usage-card-meta">
              <span>${anth.extra_usage.used_usd.toFixed(2)} / ${anth.extra_usage.limit_usd.toFixed(2)}</span>
              <span className="dim">{anth.extra_usage.used_pct.toFixed(0)}% used</span>
            </div>
          </div>
        )}
      </div>

      {/* dashd-only deep-dive sections */}
      <div className="card">
        <h3>Cost &amp; tokens (local JSONL)</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          From <code>~/.claude/projects</code>. Includes cache reads —
          large here even when Anthropic's gauges show low % used,
          because Claude.ai measures plan-units, not tokens.
        </p>
        <div className="proc-row">
          <span className="name">Today</span>
          <span className="value">
            {fmtTokens(cc.tokens_today)} tok ·
            ${cc.cost_today_usd?.toFixed(2) ?? "—"}
          </span>
        </div>
        <div className="proc-row">
          <span className="name">This block</span>
          <span className="value">
            {fmtTokens(cc.tokens_block)} tok ·
            ${cc.cost_block_usd?.toFixed(2) ?? "—"}
          </span>
        </div>
        <div className="proc-row">
          <span className="name">Last 7 days</span>
          <span className="value">
            {fmtTokens(cc.tokens_this_week)} tok ·
            ${cc.cost_this_week_usd?.toFixed(2) ?? "—"}
          </span>
        </div>
        {cc.burn_tokens_per_min != null && cc.burn_tokens_per_min > 0 && (
          <div className="proc-row">
            <span className="name">Burn rate</span>
            <span className="value">{fmtTokens(cc.burn_tokens_per_min)} tok/min</span>
          </div>
        )}
      </div>

      {cc.top_projects && cc.top_projects.length > 0 && (
        <div className="card">
          <h3>Top projects today</h3>
          {cc.top_projects.map((p: any) => (
            <div className="proc-row" key={p.name}>
              <span className="name">{p.name}</span>
              <span className="value dim">{fmtTokens(p.tokens)} tok</span>
            </div>
          ))}
        </div>
      )}

      {cc.models && Object.keys(cc.models).length > 0 && (
        <div className="card">
          <h3>Per-model breakdown</h3>
          {Object.entries(cc.models).map(([k, v]: any) => (
            <div className="proc-row" key={k}>
              <span className="name">{k}</span>
              <span className="value dim">{fmtTokens(v)} tok</span>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>Codex</h3>
        <div className="proc-row">
          <span className="name">Tokens today</span>
          <span className="value">{fmtTokens(cx.tokens_today)}</span>
        </div>
        {cx.block_used_pct != null && (
          <div className="proc-row">
            <span className="name">5h block · used</span>
            <span className="value">{cx.block_used_pct}%</span>
          </div>
        )}
      </div>
    </div>
  );
}
