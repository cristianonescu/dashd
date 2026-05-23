import { useEffect, useRef, useState } from "react";
import { useAtom, $logs } from "../store";

const LOG_FILTER_HINTS: Record<"all" | "info" | "warn" | "error", string> = {
  all:   "Show every log line — agent modules and forwarded firmware logs.",
  info:  "Show only informational lines.",
  warn:  "Show only warnings (includes 'warning' level).",
  error: "Show only errors.",
};

type Props = {
  /** When provided, a small × button in the header invokes it. */
  onClose?: () => void;
};

export default function LogsPanel({ onClose }: Props = {}) {
  const logs = useAtom($logs);
  const [filter, setFilter] = useState<"all" | "info" | "warn" | "error">("all");
  const ref = useRef<HTMLDivElement>(null);
  const autoScroll = useRef(true);

  useEffect(() => {
    if (autoScroll.current && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [logs]);

  const filtered = logs.filter((l) => {
    if (filter === "all") return true;
    if (filter === "warn") return l.level === "warn" || l.level === "warning";
    return l.level === filter;
  });

  return (
    <div className="logs-panel">
      <div className="logs-header">
        <div className="segmented" style={{ margin: 0 }}>
          {(["all", "info", "warn", "error"] as const).map((f) => (
            <button
              key={f}
              className={`seg ${filter === f ? "active" : ""}`}
              onClick={() => setFilter(f)}
              data-hint={LOG_FILTER_HINTS[f]}
            >{f}</button>
          ))}
        </div>
        <span
          className="dim"
          style={{ marginLeft: "auto", fontSize: "var(--t-footnote)" }}
          data-hint="Number of log lines matching the current filter. The view keeps the most recent 1000 lines and auto-scrolls when you're at the bottom."
        >{filtered.length} lines</span>
        {onClose && (
          <button
            className="logs-close"
            onClick={onClose}
            aria-label="Hide logs"
            data-hint="Hide the logs panel."
          >×</button>
        )}
      </div>
      <div
        ref={ref}
        className="logs"
        onScroll={(e) => {
          const el = e.currentTarget;
          autoScroll.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
        }}
      >
        {filtered.map((l, i) => (
          <div key={i} className={`log-line ${l.level}`}>
            <span className="dim">[{l.logger ?? "agent"}] </span>{l.msg}
          </div>
        ))}
      </div>
    </div>
  );
}
