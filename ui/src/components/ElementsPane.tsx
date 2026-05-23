/**
 * Settings → Elements
 *
 * Per-element show/hide control. Each toggle wraps a `if (visibility::shown("…"))`
 * gate in firmware/src/pages/*. The catalog below MUST stay in sync with
 * those `shown("…")` IDs — they're the canonical list.
 *
 * The firmware persists the hidden set in NVS (FNV-1a hashes), and emits a
 * `visibility_state` event in response to our `get_visibility` query so we
 * can render the right initial checkbox state.
 */
import { useEffect, useMemo, useState } from "react";

/** FNV-1a 32-bit, matching the device-side hash so we can compare. */
function fnv1a(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

type Element = { id: string; label: string };
type PageGroup = { page: string; elements: Element[] };

// Stable IDs — must match firmware/src/pages/*.cpp visibility::shown("…") calls.
const CATALOG: PageGroup[] = [
  { page: "Home", elements: [
    { id: "home.cpu",  label: "CPU tile" },
    { id: "home.ram",  label: "RAM tile" },
    { id: "home.ai",   label: "AI $ tile" },
    { id: "home.git",  label: "Git tile" },
    { id: "home.prs",  label: "PRs tile" },
    { id: "home.msgs", label: "Messages tile" },
  ]},
  { page: "CPU", elements: [
    { id: "cpu.cores", label: "Per-core bars + average" },
    { id: "cpu.load",  label: "Load average (1/5/15 m)" },
    { id: "cpu.freq",  label: "Frequency (current / max)" },
    { id: "cpu.temp",  label: "CPU temperature" },
    { id: "cpu.top",   label: "Top CPU processes" },
  ]},
  { page: "Memory", elements: [
    { id: "memory.headline",  label: "Pressure + used/total" },
    { id: "memory.swap",      label: "Swap usage" },
    { id: "memory.breakdown", label: "Active / Inactive / Cached" },
    { id: "memory.top",       label: "Top RAM processes" },
  ]},
  { page: "GPU", elements: [
    { id: "gpu.headline", label: "Device + vendor" },
    { id: "gpu.util",     label: "Utilization %" },
    { id: "gpu.vram",     label: "VRAM" },
    { id: "gpu.thermals", label: "Temperature + power" },
  ]},
  { page: "Network", elements: [
    { id: "network.headline", label: "Active iface + throughput" },
    { id: "network.ifaces",   label: "Per-interface table" },
  ]},
  { page: "AI Spend", elements: [
    { id: "ai.claude", label: "Claude Code section" },
    { id: "ai.codex",  label: "Codex section" },
  ]},
  { page: "Dev Flow", elements: [
    { id: "dev.branch",      label: "Branch name" },
    { id: "dev.commits",     label: "Commits today" },
    { id: "dev.loc",         label: "LOC ±" },
    { id: "dev.last_commit", label: "Time since last commit" },
  ]},
  { page: "GitHub", elements: [
    { id: "github.prs",    label: "PRs awaiting review" },
    { id: "github.ci",     label: "CI failures (24h)" },
    { id: "github.notifs", label: "Unread notifications" },
  ]},
  { page: "Calendar", elements: [
    { id: "cal.countdown",       label: "Next-event countdown" },
    { id: "cal.title",           label: "Next-event title" },
    { id: "cal.today_remaining", label: "Today remaining" },
  ]},
  { page: "Messages", elements: [
    { id: "msgs.email",    label: "Email cell" },
    { id: "msgs.imessage", label: "iMessage cell" },
    { id: "msgs.slack",    label: "Slack cell" },
    { id: "msgs.teams",    label: "Teams cell" },
    { id: "msgs.whatsapp", label: "WhatsApp cell" },
  ]},
  { page: "Tips", elements: [
    { id: "tips.suggestions", label: "Suggestions list" },
    { id: "tips.top_cpu",     label: "Top CPU processes" },
    { id: "tips.top_ram",     label: "Top RAM processes" },
  ]},
];

export default function ElementsPane() {
  // Set of hashes the device currently considers hidden.
  const [hiddenHashes, setHiddenHashes] = useState<Set<number>>(new Set());
  const [msg, setMsg] = useState("");
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 1800); };

  // Pre-compute id → hash so we can look up quickly.
  const hashByID = useMemo(() => {
    const m = new Map<string, number>();
    for (const g of CATALOG) for (const e of g.elements) m.set(e.id, fnv1a(e.id));
    return m;
  }, []);

  useEffect(() => {
    const off = window.dashd.onMessage((m: any) => {
      if (m.type === "event" && m.name === "visibility_state") {
        const hashes: number[] = Array.isArray(m.hidden_hashes) ? m.hidden_hashes : [];
        setHiddenHashes(new Set(hashes.map((h) => h >>> 0)));
      }
    });
    window.dashd.sendCmd({ name: "get_visibility" });
    return off;
  }, []);

  const isShown = (id: string): boolean => {
    const h = hashByID.get(id);
    return h === undefined ? true : !hiddenHashes.has(h);
  };

  const toggle = (id: string) => {
    const h = hashByID.get(id)!;
    const willShow = hiddenHashes.has(h);   // currently hidden → show it
    setHiddenHashes((prev) => {
      const next = new Set(prev);
      if (willShow) next.delete(h); else next.add(h);
      return next;
    });
    window.dashd.sendCmd({ name: "set_element_visible", id, visible: willShow });
  };

  const reset = () => {
    setHiddenHashes(new Set());
    window.dashd.sendCmd({ name: "reset_visibility" });
    flash("All elements visible");
  };

  const setGroup = (group: PageGroup, visible: boolean) => {
    setHiddenHashes((prev) => {
      const next = new Set(prev);
      for (const e of group.elements) {
        const h = hashByID.get(e.id)!;
        if (visible) next.delete(h); else next.add(h);
      }
      return next;
    });
    for (const e of group.elements) {
      window.dashd.sendCmd({ name: "set_element_visible", id: e.id, visible });
    }
    flash(visible ? `All ${group.page} elements shown` : `All ${group.page} elements hidden`);
  };

  return (
    <>
      <div className="card">
        <h3>Show / hide screen elements</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          Hide any element you don't care about. Hidden ones disappear from
          the device immediately and the remaining elements reflow to fill
          the space. Settings persist in NVS across reboots.
        </p>
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button className="btn" onClick={reset}
                  data-hint="Re-show every element on every page of the device, clearing all hide overrides.">Reset (show all)</button>
          {msg && <span className="dim" style={{ alignSelf: "center" }}>{msg}</span>}
        </div>
      </div>

      {CATALOG.map((group) => {
        const totalShown = group.elements.filter((e) => isShown(e.id)).length;
        return (
          <div className="card" key={group.page}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <h3 style={{ margin: 0, flex: 1 }}>{group.page}</h3>
              <span className="dim" style={{ fontSize: 12 }}>
                {totalShown}/{group.elements.length} shown
              </span>
              <button className="btn" onClick={() => setGroup(group, true)}
                      data-hint={`Show every element on the device's ${group.page} page.`}>All</button>
              <button className="btn" onClick={() => setGroup(group, false)}
                      data-hint={`Hide every element on the device's ${group.page} page.`}>None</button>
            </div>
            <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
              {group.elements.map((e) => (
                <label key={e.id} className="switch" style={{ padding: "4px 0" }}
                       data-hint={`Show or hide "${e.label}" on the device's ${group.page} page. Hidden elements disappear immediately and the rest reflow to fill the space.`}>
                  <input
                    type="checkbox"
                    checked={isShown(e.id)}
                    onChange={() => toggle(e.id)}
                  />
                  <span>{e.label}</span>
                  <code className="dim" style={{ marginLeft: 6, fontSize: 11 }}>{e.id}</code>
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}
