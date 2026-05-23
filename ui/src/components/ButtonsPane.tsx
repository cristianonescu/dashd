/**
 * Settings → Buttons
 *
 * Two jobs:
 *   1. A cheat-sheet of every supported hardware-button gesture and what
 *      it does. This is the reference users would otherwise need to dig
 *      out of docs/wiring.md.
 *   2. Live feedback — when the device emits a button event, the matching
 *      row pulses briefly and the press is appended to a small activity
 *      feed. Lets the user verify their button is actually working
 *      without flipping back to the Logs tab.
 *
 * Events consumed (from the device, via the agent):
 *   button_short_press
 *   button_long_press
 *   page_changed         — passes through page navigations
 */
import { useEffect, useMemo, useRef, useState } from "react";

type Gesture = {
  id: "short" | "long";
  eventName: "button_short_press" | "button_long_press";
  label: string;
  action: string;
  detail: string;
};

const GESTURES: Gesture[] = [
  {
    id: "short",
    eventName: "button_short_press",
    label: "Short press",
    action: "Next page",
    detail:
      "Advances to the next enabled page. Disabled pages are skipped. Plays a horizontal wipe with the pet running ahead.",
  },
  {
    id: "long",
    eventName: "button_long_press",
    label: "Long press",
    action: "Jump to Home",
    detail:
      "Hold for ≥ 800 ms. Plays a centre-zoom transition with the pet jumping in the middle.",
  },
];

type FeedEntry = {
  ts: number;
  gestureId: Gesture["id"];
  page?: string;
};

const FEED_CAP = 12;

export default function ButtonsPane() {
  // Pulse state per gesture: timestamp of the most recent matching event,
  // so we can drive a short CSS-driven flash via key prop changes.
  const [lastFired, setLastFired] = useState<Record<string, number>>({});
  // Recent activity feed.
  const [feed, setFeed] = useState<FeedEntry[]>([]);
  // Remember the last button gesture so when page_changed arrives we can
  // attribute it (page changes happen ~once after each gesture).
  const lastGestureRef = useRef<Gesture["id"] | null>(null);

  useEffect(() => {
    return window.dashd.onMessage((m: any) => {
      if (m.type !== "event") return;
      if (m.name === "button_short_press" || m.name === "button_long_press") {
        const id: Gesture["id"] = m.name === "button_short_press" ? "short" : "long";
        const ts = Date.now();
        setLastFired((prev) => ({ ...prev, [id]: ts }));
        lastGestureRef.current = id;
        setFeed((prev) => {
          const next: FeedEntry[] = [{ ts, gestureId: id }, ...prev];
          return next.slice(0, FEED_CAP);
        });
      } else if (m.name === "page_changed") {
        // Attach this page hop to the most recent gesture entry so the
        // user can see what their button push *did*.
        setFeed((prev) => {
          if (prev.length === 0 || prev[0].page) return prev;
          return [{ ...prev[0], page: String(m.page || "") }, ...prev.slice(1)];
        });
      }
    });
  }, []);

  // Memoize formatter so the feed re-renders cheaply.
  const formatTime = useMemo(() => {
    const f = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    return (ts: number) => f.format(new Date(ts));
  }, []);

  return (
    <>
      <div className="card">
        <h3>Hardware button</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          One tactile button on GPIO 2 (see{" "}
          <a href="#" onClick={(e) => e.preventDefault()} style={{ color: "var(--accent)" }}
             data-hint="Wiring reference — the button connects GPIO 2 to GND. Full schematic is in docs/wiring.md in the repo.">docs/wiring.md</a>). Two gestures. Both
          run an animation on the device — the pet rides each transition.
        </p>

        {GESTURES.map((g) => {
          const lastMs = lastFired[g.id];
          const recent = lastMs && Date.now() - lastMs < 1200;
          return (
            <div
              key={g.id + (recent ? `-${lastMs}` : "")}
              className="card"
              style={{
                margin: "10px 0 0 0",
                background: recent ? "var(--bg)" : "var(--bg-2)",
                borderColor: recent ? "var(--accent)" : "var(--border)",
                transition: "border-color 0.1s ease, background 0.1s ease",
                animation: recent ? "pulse 1.2s ease-out" : "none",
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
                <span style={{ fontWeight: 700, minWidth: 110 }}>{g.label}</span>
                <span style={{ color: "var(--accent)", fontSize: 13 }}>→ {g.action}</span>
                {recent && (
                  <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--good)" }}>
                    fired
                  </span>
                )}
              </div>
              <p className="dim" style={{ fontSize: 12, margin: "6px 0 0" }}>{g.detail}</p>
            </div>
          );
        })}
      </div>

      <div className="card">
        <h3>Recent activity</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          The latest {FEED_CAP} button presses from this session. Press the
          button on the device and you should see it here within ~250 ms.
        </p>
        {feed.length === 0 ? (
          <p className="dim" style={{ fontSize: 12 }}>
            Waiting for a button press from the device…
          </p>
        ) : (
          <div style={{ maxHeight: 260, overflowY: "auto" }}>
            {feed.map((e, i) => {
              const g = GESTURES.find((x) => x.id === e.gestureId)!;
              return (
                <div className="kv" key={`${e.ts}-${i}`}
                     style={{ padding: "4px 0", fontSize: 13, borderBottom: "1px solid var(--border)" }}>
                  <span className="dim" style={{ minWidth: 80 }}>{formatTime(e.ts)}</span>
                  <span style={{ flex: 1, fontWeight: 600 }}>{g.label}</span>
                  <span className="dim" style={{ fontSize: 12 }}>
                    {e.page ? `→ ${e.page}` : "…"}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(77, 127, 232, 0.45); }
          70% { box-shadow: 0 0 0 10px rgba(77, 127, 232, 0); }
          100% { box-shadow: 0 0 0 0 rgba(77, 127, 232, 0); }
        }
      `}</style>
    </>
  );
}
