/**
 * Codexbar-style usage card.
 *
 * Renders a single Anthropic gauge (session / weekly / sonnet / extra):
 *   - title + plan name (optional)
 *   - progress bar (themed by % used)
 *   - "X% used  Resets in Yh Zm"
 *   - pace line, when computable
 *
 * Shared by both the tray popover (`UsagePopover.tsx`) and the main-
 * window Usage tab (`UsageView.tsx`). The visual is intentionally close
 * to the codexbar screenshot the user referenced.
 */
import type { AnthropicUsageWindow } from "../types";

type Props = {
  title: string;
  /** Subtitle right of the title — e.g. "Max" for plan name. */
  subtitle?: string;
  /** The usage window; null/undefined renders an empty card with `—`. */
  window?: AnthropicUsageWindow | null;
  /** When true, the pace line below the bar is rendered. */
  showPace?: boolean;
  /** Compact mode: smaller font + tighter padding (for the popover). */
  compact?: boolean;
};

function fmtTimeShort(min?: number | null): string {
  if (min == null) return "—";
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h < 24) return `${h}h ${m.toString().padStart(2, "0")}m`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return `${d}d ${rh}h`;
}

function paceLabel(window: AnthropicUsageWindow): string | null {
  const status = window.pace_status;
  if (!status || status === "warming_up") return null;
  // The classifier returns: on_track | slightly_ahead | ahead | far_ahead
  // | slightly_behind | behind | far_behind. Surface verbatim with
  // a leading-cap pretty-print and the delta value in parentheses.
  const display = status.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
  const delta = window.pace_delta_pct;
  if (delta == null) return display;
  const sign = delta < 0 ? "" : "+";
  return `Pace: ${display} (${sign}${delta.toFixed(0)}%)`;
}

function paceTail(window: AnthropicUsageWindow): string | null {
  if (window.will_last_to_reset === false && window.eta_to_cap_min != null) {
    return `Cap in ${fmtTimeShort(window.eta_to_cap_min)}`;
  }
  if (window.will_last_to_reset === true && window.pace_status &&
      window.pace_status !== "warming_up") {
    return "Lasts to reset";
  }
  return null;
}

export function UsageCard({ title, subtitle, window, showPace = false, compact = false }: Props) {
  const used = window?.used_pct ?? null;
  const resets = window?.resets_in_min ?? null;
  const pct = used == null ? 0 : Math.max(0, Math.min(100, Math.round(used)));
  const variant =
    pct >= 90 ? "crit" :
    pct >= 75 ? "warn" :
    pct >= 50 ? "ok" : "low";

  return (
    <div className={`usage-card ${compact ? "compact" : ""}`}>
      <div className="usage-card-head">
        <h4 className="usage-card-title">{title}</h4>
        {subtitle && <span className="usage-card-subtitle">{subtitle}</span>}
      </div>
      <div className="usage-bar" data-variant={variant}>
        <div className="usage-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="usage-card-meta">
        <span>{used == null ? "—" : `${used.toFixed(used < 1 && used > 0 ? 1 : 0)}% used`}</span>
        {resets != null && (
          <span className="dim">Resets in {fmtTimeShort(resets)}</span>
        )}
      </div>
      {showPace && window && (() => {
        const label = paceLabel(window);
        const tail = paceTail(window);
        if (!label && !tail) return null;
        return (
          <div className="usage-card-pace dim">
            {label}
            {label && tail && " · "}
            {tail}
          </div>
        );
      })()}
    </div>
  );
}
