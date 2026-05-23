import { useEffect, useMemo, useRef, useState } from "react";
import { PetPreview } from "./PetPreview";
import ButtonsPane from "./ButtonsPane";
import ElementsPane from "./ElementsPane";
import ConnectionPane from "./ConnectionPane";
import UpdatesPane from "./UpdatesPane";

/** Canonical page list; index here matches firmware PageId. */
const ALL_PAGES = [
  { id: 0, name: "Home"     },
  { id: 1, name: "System"   },
  { id: 2, name: "AI Spend" },
  { id: 3, name: "Dev Flow" },
  { id: 4, name: "GitHub"   },
  { id: 5, name: "Calendar" },
  { id: 6, name: "Messages" },
  { id: 7, name: "Tips"     },
] as const;

const SETTINGS_TAB_HINTS: Record<string, string> = {
  general:    "App-level options: start at login, restart the agent, reload config, reset device preferences.",
  connection: "Choose Cable / Bluetooth / Auto, scan for and pair Bluetooth devices, manage paired devices.",
  collectors: "Reference for every data collector and which env var holds its secret. Edit values in config.toml.",
  pages:      "Reorder and enable/disable the device's pages. The hardware button cycles through enabled pages in this order.",
  elements:   "Show or hide individual elements within each device page.",
  theme:      "Device appearance: backlight brightness, colors, text sizes and warning thresholds.",
  layout:     "Device layout: title bar, footer and screen rotation.",
  pet:        "The animated pet overlay — pick one, install new ones from codexpets.net, or test its animations.",
  buttons:    "Reference for the hardware button gestures, with live press feedback.",
  updates:    "Check for and install new versions of the dashd app and the device firmware.",
  privacy:    "Opt-in features that involve reading credentials or talking to upstream services. Everything default-off.",
};

export default function Settings() {
  const [tab, setTab] = useState<"general" | "connection" | "collectors" | "pages" | "elements" | "theme" | "layout" | "pet" | "buttons" | "updates" | "privacy">("general");

  return (
    <div className="pane">
      <div className="segmented" style={{ margin: "0 0 18px", flexWrap: "wrap" }}>
        {(["general", "connection", "collectors", "pages", "elements", "theme", "layout", "pet", "buttons", "updates", "privacy"] as const).map((t) => (
          <button key={t} className={`seg ${tab === t ? "active" : ""}`}
                  onClick={() => setTab(t)} data-hint={SETTINGS_TAB_HINTS[t]}>{t}</button>
        ))}
      </div>
      {tab === "general"    && <GeneralPane />}
      {tab === "connection" && <ConnectionPane />}
      {tab === "collectors" && <CollectorsPane />}
      {tab === "pages"      && <PagesPane />}
      {tab === "elements"   && <ElementsPane />}
      {tab === "theme"      && <ThemePane />}
      {tab === "layout"     && <LayoutPane />}
      {tab === "pet"        && <PetPane />}
      {tab === "buttons"    && <ButtonsPane />}
      {tab === "updates"    && <UpdatesPane />}
      {tab === "privacy"    && <PrivacyPane />}
    </div>
  );
}


/**
 * Settings → Privacy
 *
 * One thing today: the Anthropic OAuth API opt-in. Future opt-in
 * features land here too (e.g. telemetry, crash reports).
 */
function PrivacyPane() {
  const [oauth, setOauth] = useState<boolean>(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      const p = await window.dashd.getPrefs();
      setOauth(!!(p as any).anthropicOAuth);
    })();
  }, []);

  const onToggle = async (next: boolean) => {
    setBusy(true);
    try {
      const merged = await window.dashd.setPrefs({ anthropicOAuth: next } as any);
      setOauth(!!(merged as any).anthropicOAuth);
      // Restart the agent so it picks up DASHD_ANTHROPIC_OAUTH=1/0.
      await window.dashd.restartAgent();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <h3 data-hint="Opt-in features that touch credentials or upstream services.">
          Anthropic Usage API
        </h3>
        <p className="dim" style={{ fontSize: 13, marginTop: 0 }}>
          dashd can read your local Claude Code OAuth token to fetch the
          Session / Weekly / Sonnet usage gauges directly from Anthropic.
          This is what makes the gauges match Claude.ai exactly instead of
          relying on dashd's local heuristic. Off by default.
        </p>
        <ul className="dim" style={{ fontSize: 12, paddingLeft: 20, marginTop: 4 }}>
          <li>Token is loaded into memory only — never logged, never persisted.</li>
          <li>Only the OAuth usage endpoint is called; no other Anthropic API surface.</li>
          <li>Toggling restarts the dashd agent so the new setting takes effect.</li>
          <li>Disable any time; dashd falls back to local JSONL-derived approximations.</li>
        </ul>
        <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={oauth}
              disabled={busy}
              onChange={(e) => onToggle(e.target.checked)}
              data-hint={oauth
                ? "Reading Claude Code OAuth token on every 60s tick. Click to disable."
                : "Click to enable. dashd will restart the agent and start polling Anthropic's OAuth usage endpoint."}
            />
            <span style={{ fontSize: 13 }}>
              Enable Anthropic OAuth Usage API
            </span>
          </label>
          {busy && <span className="dim" style={{ fontSize: 12 }}>Restarting agent…</span>}
        </div>
      </div>
    </>
  );
}

const PET_STATES = [
  "idle", "run_right", "run_left", "wave", "jump",
  "failed", "waiting", "running", "review",
] as const;
const PET_CORNERS = [
  { value: 0, label: "Top-right" },
  { value: 1, label: "Bottom-right (default)" },
  { value: 2, label: "Bottom-left" },
  { value: 3, label: "Top-left" },
] as const;

type CatalogEntry = { slug: string; name: string; gallery_url: string };
type CatalogState = { loading: boolean; entries: CatalogEntry[]; error?: string };
type InstallState = {
  active: boolean;
  slug?: string;
  status?: "downloading" | "streaming" | "complete" | "failed" | "started";
  message?: string;
};

function PetPane() {
  const [enabled, setEnabled] = useState(true);
  const [corner, setCorner] = useState(1);
  const [activeSlug, setActiveSlug] = useState<string>("default");
  const [catalog, setCatalog] = useState<CatalogState>({ loading: false, entries: [] });
  const [install, setInstall] = useState<InstallState>({ active: false });
  const [filter, setFilter] = useState("");
  const [directInput, setDirectInput] = useState("");
  const [directPreviewSlug, setDirectPreviewSlug] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 1800); };

  // Subscribe to agent events for catalog + install lifecycle + on-device acks.
  useEffect(() => {
    return window.dashd.onMessage((m: any) => {
      if (m.type !== "event") return;
      if (m.name === "pets_catalog")
        setCatalog({ loading: false, entries: m.entries || [] });
      else if (m.name === "pets_catalog_error")
        setCatalog({ loading: false, entries: [], error: m.error });
      else if (m.name === "pets_install_started")
        setInstall({ active: true, slug: m.slug, status: "downloading", message: "Downloading + converting…" });
      else if (m.name === "pets_install_complete")
        setInstall({ active: true, slug: m.slug, status: "streaming", message: "Streaming to device…" });
      else if (m.name === "pets_install_failed")
        setInstall({ active: false, slug: m.slug, status: "failed", message: m.error });
      // Device-side events
      else if (m.name === "pet_install_started")
        setInstall((s) => ({ ...s, status: "started", message: m.ok ? "Device ready to receive" : "Device refused" }));
      else if (m.name === "pet_install_ended") {
        setInstall({ active: false, slug: m.slug, status: m.ok ? "complete" : "failed",
                     message: m.ok ? "Installed and active" : "Install failed on device" });
        if (m.ok && m.slug) setActiveSlug(m.slug);
      } else if (m.name === "pet_activated") {
        if (m.ok && m.slug) setActiveSlug(m.slug);
      }
    });
  }, []);

  const fetchCatalog = () => {
    setCatalog({ loading: true, entries: [] });
    window.dashd.sendCmd({ name: "pets_catalog" });
  };

  const installPet = (slugOrUrl: string) => {
    if (!slugOrUrl.trim()) return;
    setInstall({ active: true, slug: slugOrUrl, status: "downloading", message: "Asking agent…" });
    window.dashd.sendCmd({ name: "pets_install", slug: slugOrUrl.trim() });
    flash(`Installing ${slugOrUrl}…`);
  };

  const setActive = (slug: string) => {
    window.dashd.sendCmd({ name: "pet_set_active", slug });
    setActiveSlug(slug);
    flash(slug === "default" ? "Switched to default" : `Switched to ${slug}`);
  };

  const removePet = (slug: string) => {
    window.dashd.sendCmd({ name: "pet_remove", slug });
    flash(`Removed ${slug}`);
  };

  const applyEnabled = (v: boolean) => {
    setEnabled(v);
    window.dashd.sendCmd({ name: "pet_set_enabled", enabled: v });
    flash(v ? "Pet on" : "Pet off");
  };

  const applyCorner = (v: number) => {
    setCorner(v);
    window.dashd.sendCmd({ name: "pet_set_corner", corner: v });
    flash("Position applied");
  };

  // Filtered catalog: case-insensitive substring match on slug or name.
  const filtered = useMemo(() => {
    if (!filter.trim()) return catalog.entries;
    const f = filter.toLowerCase();
    return catalog.entries.filter((e) =>
      e.slug.toLowerCase().includes(f) || e.name.toLowerCase().includes(f)
    );
  }, [catalog.entries, filter]);

  return (
    <>
      <div className="card">
        <h3>Pet overlay</h3>
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
          <PetPreview slug={activeSlug} size={96} />
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: 13, margin: 0 }}>
              Active: <strong>{activeSlug}</strong>
            </p>
            <p className="dim" style={{ fontSize: 12, marginTop: 4 }}>
              Default pet: <strong>Claw'd</strong> by{" "}
              <a href="https://codexpets.net/gallery/claw-d" style={{ color: "var(--accent)" }}
                 data-hint="Open Claw'd's page on codexpets.net (the original creator's gallery).">krrsantan</a>
              {" "}— bundled with attribution from{" "}
              <a href="https://codexpets.net" style={{ color: "var(--accent)" }}
                 data-hint="Open codexpets.net — the community gallery this app's pets come from.">codexpets.net</a>.
            </p>
          </div>
        </div>
        <label className="switch" style={{ marginTop: 10 }}
               data-hint="Draw the animated pet sprite on top of every device page. Applies immediately and persists on the device.">
          <input type="checkbox" checked={enabled} onChange={(e) => applyEnabled(e.target.checked)}/>
          Show pet on every page
        </label>
        <p className="dim" style={{ fontSize: 12, marginTop: 4 }}>
          Turn off to reclaim the corner of the screen.
        </p>
        <div className="kv" style={{ marginTop: 12 }}>
          <span className="k">Position</span>
          <select value={corner} onChange={(e) => applyCorner(Number(e.target.value))}
                  style={{ width: 220 }}
                  data-hint="Which screen corner the pet sits in. Applied to the device immediately.">
            {PET_CORNERS.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => setActive("default")}
                  data-hint="Switch the active pet back to the bundled default, Claw'd.">
            Reset to Claw'd
          </button>
          <Toast msg={msg}/>
        </div>
      </div>

      <div className="card">
        <h3>Install a pet</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          Pets come from <a href="https://codexpets.net" style={{ color: "var(--accent)" }}
             data-hint="Open codexpets.net to browse pets in your browser.">codexpets.net</a>
          {" "}— the agent downloads and converts on demand, then streams the binary to the device.
          Paste a slug (<code>claw-d</code>) or full URL, or browse the catalog below.
        </p>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            type="text"
            value={directInput}
            onChange={(e) => setDirectInput(e.target.value)}
            placeholder="e.g. pixel-coder or https://codexpets.net/gallery/pixel-coder"
            style={{ flex: 1 }}
            data-hint="Paste a codexpets.net pet slug (like 'pixel-coder') or a full gallery URL."
          />
          <button className="btn"
                  onClick={() => directInput.trim() && setDirectPreviewSlug(directInput.trim())}
                  disabled={!directInput.trim()}
                  data-hint="Fetch and animate this pet here in the app, without installing it to the device.">
            Preview
          </button>
          <button className="btn primary" onClick={() => installPet(directInput)} disabled={install.active}
                  data-hint="Download + convert this pet and stream it to the device over the active transport. Becomes the active pet when done.">
            Install
          </button>
        </div>
        {directPreviewSlug && (
          <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center" }}>
            <PetPreview slug={directPreviewSlug} size={80} />
            <span className="dim" style={{ fontSize: 12 }}>
              previewing <code>{directPreviewSlug}</code>
            </span>
          </div>
        )}
        {install.active && (
          <p className="dim" style={{ fontSize: 12, marginTop: 10 }}>
            {install.slug}: {install.message ?? install.status}
          </p>
        )}
        {!install.active && install.status === "complete" && (
          <p className="good" style={{ fontSize: 12, marginTop: 10 }}>
            ✓ {install.slug} installed
          </p>
        )}
        {!install.active && install.status === "failed" && (
          <p className="crit" style={{ fontSize: 12, marginTop: 10 }}>
            ✗ {install.message}
          </p>
        )}
      </div>

      <div className="card">
        <h3>Catalog ({catalog.entries.length || "—"} pets)</h3>
        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button className="btn" onClick={fetchCatalog} disabled={catalog.loading}
                  data-hint="Fetch the full pet catalog from codexpets.net (~800 pets). The agent enumerates the site's sitemap.">
            {catalog.loading ? "Loading…" : (catalog.entries.length ? "Refresh" : "Load catalog")}
          </button>
          {catalog.entries.length > 0 && (
            <input type="text" value={filter} onChange={(e) => setFilter(e.target.value)}
                   placeholder="Filter…" style={{ flex: 1 }}
                   data-hint="Filter the catalog by pet name or slug (case-insensitive substring match)."/>
          )}
        </div>
        {catalog.error && (
          <p className="crit" style={{ fontSize: 12 }}>Catalog load failed: {catalog.error}</p>
        )}
        {catalog.entries.length > 0 && (
          <CatalogList entries={filtered} onInstall={installPet} disabled={install.active}/>
        )}
        {!catalog.loading && catalog.entries.length === 0 && !catalog.error && (
          <p className="dim" style={{ fontSize: 12 }}>
            Catalog not loaded yet — click "Load catalog" to fetch the full list from codexpets.net.
          </p>
        )}
      </div>

      <div className="card">
        <h3>Pet state</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          The agent automatically picks the animation based on what's happening
          (new commit → wave, CI fails → failed, RAM/CPU pressure → running).
          Tap below to test manually.
        </p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {PET_STATES.map((s) => (
            <button key={s} className="btn"
                    onClick={() => {
                      window.dashd.sendCmd({ name: "pet_set_state", state: s });
                      flash(`→ ${s}`);
                    }}
                    data-hint={`Force the pet into its "${s}" animation on the device — for testing. Normally the agent picks the animation automatically.`}>
              {s}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

/** Lazy-rendered catalog grid. Only renders the first N items at a time and
 *  appends more on scroll-near-bottom — keeps the DOM cheap with 800+ pets.
 */
function CatalogList({ entries, onInstall, disabled }: {
  entries: CatalogEntry[]; onInstall: (slug: string) => void; disabled: boolean;
}) {
  const PAGE = 30;
  const [visibleCount, setVisibleCount] = useState(PAGE);
  // Slugs the user has clicked "Preview" on — preview component is mounted
  // lazily so we don't fetch ~800 spritesheets up-front.
  const [openPreviews, setOpenPreviews] = useState<Set<string>>(() => new Set());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setVisibleCount(PAGE); setOpenPreviews(new Set()); }, [entries]);

  const togglePreview = (slug: string) => {
    setOpenPreviews((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug); else next.add(slug);
      return next;
    });
  };

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 60) {
      setVisibleCount((n) => Math.min(n + PAGE, entries.length));
    }
  };

  const shown = entries.slice(0, visibleCount);
  return (
    <div ref={ref} onScroll={onScroll}
         style={{ maxHeight: 360, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
      {shown.map((e) => {
        const isOpen = openPreviews.has(e.slug);
        return (
          <div key={e.slug} style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)" }}>
            <div className="kv" style={{ margin: 0 }}>
              {isOpen && <PetPreview slug={e.slug} size={56} />}
              <span style={{ flex: 1, paddingLeft: isOpen ? 10 : 0 }}>
                <strong>{e.name}</strong> <span className="dim">{e.slug}</span>
              </span>
              <span style={{ display: "flex", gap: 4 }}>
                <a href={e.gallery_url}
                   style={{ color: "var(--accent)", fontSize: 12, alignSelf: "center" }}
                   data-hint={`Open this pet's page on codexpets.net (${e.gallery_url}).`}>↗</a>
                <button className="btn" onClick={() => togglePreview(e.slug)}
                        data-hint={isOpen ? "Hide this pet's animated preview." : "Show an animated preview of this pet, here in the app."}>
                  {isOpen ? "Hide" : "Preview"}
                </button>
                <button className="btn" disabled={disabled} onClick={() => onInstall(e.slug)}
                        data-hint={`Install "${e.name}" to the device and make it the active pet.`}>
                  Install
                </button>
              </span>
            </div>
          </div>
        );
      })}
      {visibleCount < entries.length && (
        <p className="dim" style={{ textAlign: "center", padding: 8, fontSize: 12 }}>
          Scroll to load more ({entries.length - visibleCount} remaining)
        </p>
      )}
    </div>
  );
}

function Toast({ msg }: { msg: string }) {
  if (!msg) return null;
  return <span className="dim" style={{ marginLeft: 8, alignSelf: "center" }}>{msg}</span>;
}

function GeneralPane() {
  const [autostart, setAutostart] = useState<boolean>(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string>("");

  useEffect(() => {
    (async () => {
      setAutostart(await window.dashd.getAutostart());
      setLoading(false);
    })();
  }, []);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 1800); };
  const onToggle = async (v: boolean) => {
    setLoading(true);
    await window.dashd.setAutostart(v);
    setAutostart(v);
    setLoading(false);
    flash(v ? "Autostart enabled" : "Autostart disabled");
  };

  return (
    <div className="card">
      <h3>General</h3>
      <label className="switch"
             data-hint="Register dashd to launch automatically when you log in (macOS launchd / Linux systemd-user / Windows Run key).">
        <input type="checkbox" disabled={loading} checked={autostart}
               onChange={(e) => onToggle(e.target.checked)}/>
        Start dashd at login
      </label>
      <p className="dim" style={{ marginTop: 8, fontSize: 12 }}>
        Agent runs in the background even when this window is closed.
      </p>
      <div style={{ marginTop: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn" onClick={() => window.dashd.restartAgent()}
                data-hint="Stop and respawn the background dashd-agent process. Use after a crash or a code update.">Restart agent</button>
        <button className="btn" onClick={() => { window.dashd.sendCmd({ name: "reload_config" }); flash("Reloaded"); }}
                data-hint="Re-read ~/.config/dashd/config.toml and rebuild the collector list — no agent restart needed.">
          Reload config.toml
        </button>
        <button className="btn" onClick={() => { window.dashd.sendCmd({ name: "reset_prefs" }); flash("Device prefs cleared"); }}
                data-hint="Clear ALL device-side overrides (theme, layout, pages, visibility, brightness) from NVS and restore compile-time defaults.">
          Reset device prefs
        </button>
        <Toast msg={msg}/>
      </div>
    </div>
  );
}

const COLLECTORS: Array<{ key: string; desc: string; secrets?: string[] }> = [
  { key: "system",      desc: "CPU, RAM, disk, net, battery" },
  { key: "claude_code", desc: "Tokens + cost from ~/.claude/projects" },
  { key: "codex",       desc: "Block % from ~/.codex/sessions" },
  { key: "git",         desc: "Commits + LOC across configured repos" },
  { key: "github",      desc: "PRs / CI / notifications", secrets: ["GITHUB_TOKEN"] },
  { key: "calendar",    desc: "Microsoft Graph next event" },
  { key: "email",       desc: "IMAP unread count", secrets: ["DASHD_EMAIL_PASSWORD"] },
  { key: "imessage",    desc: "macOS only; needs Full Disk Access" },
  { key: "whatsapp",    desc: "Best-effort; currently always null" },
];

function CollectorsPane() {
  return (
    <div className="card">
      <h3>Collectors</h3>
      <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
        Configure in <code>~/.config/dashd/config.toml</code>. Secret values use the listed env vars
        so they never end up in config files or git. Hit "Reload config.toml" after editing.
      </p>
      {COLLECTORS.map((c) => (
        <div className="kv" key={c.key} style={{ alignItems: "baseline" }}
             data-hint={`${c.desc}. Enable/disable under [collectors.${c.key}] in config.toml`
               + (c.secrets && c.secrets.length
                   ? `; secret via env var ${c.secrets.join(", ")}.`
                   : ".")}>
          <span className="k" style={{ minWidth: 110 }}>{c.key}</span>
          <span className="v" style={{ flex: 1, textAlign: "left", paddingLeft: 12 }}>
            <span className="dim">{c.desc}</span>
            {c.secrets && c.secrets.length > 0 && (
              <span style={{ marginLeft: 8 }} className="warn">env: {c.secrets.join(", ")}</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Drag-to-reorder list of pages with per-row enable toggle. */
function PagesPane() {
  const [order, setOrder] = useState<number[]>(ALL_PAGES.map((p) => p.id));
  const [enabled, setEnabled] = useState<boolean[]>(() => ALL_PAGES.map(() => true));
  const dragFrom = useRef<number | null>(null);
  const [msg, setMsg] = useState("");

  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length || from === to) return;
    const o = [...order]; const e = [...enabled];
    const [pid] = o.splice(from, 1);
    const [en] = e.splice(from, 1);
    o.splice(to, 0, pid); e.splice(to, 0, en);
    setOrder(o); setEnabled(e);
  };

  const applyOrder = () => {
    window.dashd.sendCmd({ name: "set_pages_order", order });
    setMsg("Order applied"); setTimeout(() => setMsg(""), 1800);
  };
  const applyEnabled = () => {
    let mask = 0;
    enabled.forEach((on, i) => { if (on) mask |= (1 << order[i]); });
    if (mask === (1 << ALL_PAGES.length) - 1) mask = 0; // all → 0
    window.dashd.sendCmd({ name: "set_pages_enabled", mask });
    setMsg("Enabled mask applied"); setTimeout(() => setMsg(""), 1800);
  };
  const reset = () => {
    setOrder(ALL_PAGES.map((p) => p.id));
    setEnabled(ALL_PAGES.map(() => true));
    window.dashd.sendCmd({ name: "set_pages_order", order: [] });
    window.dashd.sendCmd({ name: "set_pages_enabled", mask: 0 });
    setMsg("Reset to defaults"); setTimeout(() => setMsg(""), 1800);
  };

  return (
    <>
    <AutoAdvanceCard/>
    <div className="card">
      <h3>Pages</h3>
      <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
        Drag a row to reorder. The button short-press cycles through the enabled pages
        in this order. Order + enabled mask both persist on the device (NVS).
      </p>
      {order.map((pid, slot) => {
        const p = ALL_PAGES.find((q) => q.id === pid)!;
        return (
          <div
            className="kv"
            key={pid}
            draggable
            onDragStart={() => { dragFrom.current = slot; }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => { if (dragFrom.current != null) move(dragFrom.current, slot); dragFrom.current = null; }}
            style={{ cursor: "grab", padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 6, marginBottom: 4, background: "var(--bg-2)" }}
            data-hint={`Drag this row to change where "${p.name}" sits in the button-cycle order. Don't forget to click "Apply order".`}
          >
            <span className="dim" style={{ marginRight: 8 }}>≡</span>
            <label className="switch" style={{ flex: 1 }}
                   data-hint={`Include "${p.name}" in the device's page cycle. Uncheck to skip it. Apply with "Apply enabled".`}>
              <input type="checkbox"
                     checked={enabled[slot]}
                     onChange={(e) => { const arr = [...enabled]; arr[slot] = e.target.checked; setEnabled(arr); }}/>
              {p.name}
            </label>
            <span style={{ display: "flex", gap: 4 }}>
              <button className="btn" onClick={() => move(slot, slot - 1)} disabled={slot === 0}
                      data-hint="Move this page one slot earlier in the order.">↑</button>
              <button className="btn" onClick={() => move(slot, slot + 1)} disabled={slot === order.length - 1}
                      data-hint="Move this page one slot later in the order.">↓</button>
              <button className="btn" onClick={() => window.dashd.sendCmd({ name: "show_page", page: p.name })}
                      data-hint={`Jump the device straight to the "${p.name}" page right now.`}>Show</button>
            </span>
          </div>
        );
      })}
      <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn primary" onClick={applyOrder}
                data-hint="Send the current page order to the device. Persists in NVS across reboots.">Apply order</button>
        <button className="btn primary" onClick={applyEnabled}
                data-hint="Send the enabled/disabled mask to the device. Disabled pages are skipped by the button cycle.">Apply enabled</button>
        <button className="btn" onClick={reset}
                data-hint="Restore the default page order and re-enable every page, on both this list and the device.">Reset to defaults</button>
        <Toast msg={msg}/>
      </div>
    </div>
    </>
  );
}

/**
 * Settings → Pages → Auto-advance card.
 *
 * Drives the device's auto-cycling timer. The firmware owns the
 * countdown + persists settings to NVS; this UI just sends a
 * `set_auto_advance` cmd whenever the user changes anything. We also
 * mirror the chosen values into the Electron UI prefs so the device's
 * NVS gets re-asserted on every agent reconnect (in case some other
 * tool reset prefs in the meantime).
 */
function AutoAdvanceCard() {
  const [enabled, setEnabled] = useState<boolean>(true);
  const [intervalS, setIntervalS] = useState<number>(8);
  const [mode, setMode] = useState<"sequential" | "random">("sequential");
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    (async () => {
      const p = (await window.dashd.getPrefs()) as any;
      if (p.autoAdvanceEnabled !== undefined)    setEnabled(!!p.autoAdvanceEnabled);
      if (p.autoAdvanceIntervalS !== undefined)  setIntervalS(Number(p.autoAdvanceIntervalS));
      if (p.autoAdvanceMode !== undefined)       setMode(p.autoAdvanceMode);
      setLoaded(true);
    })();
  }, []);

  const apply = async (next: {
    enabled?: boolean; intervalS?: number; mode?: "sequential" | "random";
  }) => {
    const newEnabled  = next.enabled  ?? enabled;
    const newInterval = Math.max(3, Math.min(300, next.intervalS ?? intervalS));
    const newMode     = next.mode     ?? mode;
    setEnabled(newEnabled);
    setIntervalS(newInterval);
    setMode(newMode);
    await window.dashd.setPrefs({
      autoAdvanceEnabled: newEnabled,
      autoAdvanceIntervalS: newInterval,
      autoAdvanceMode: newMode,
    } as any);
    window.dashd.sendCmd({
      name: "set_auto_advance",
      enabled: newEnabled,
      interval_s: newInterval,
      mode: newMode,
    });
    setMsg("Applied"); setTimeout(() => setMsg(""), 1200);
  };

  if (!loaded) return null;

  return (
    <div className="card">
      <h3 data-hint="The device can cycle between enabled pages on a timer with no button press needed. Settings persist in device NVS.">
        Auto-advance pages
      </h3>
      <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
        Cycle automatically through the enabled pages so the device shows
        each one in turn. A button press or a manual page jump resets the
        countdown so you always get a full interval to read what you just
        selected. Off → only the button changes pages.
      </p>
      <label className="switch" style={{ marginTop: 8 }}
             data-hint="Enable timer-driven page cycling on the device.">
        <input type="checkbox" checked={enabled}
               onChange={(e) => apply({ enabled: e.target.checked })}/>
        Cycle pages automatically
      </label>
      <div style={{ marginTop: 12, opacity: enabled ? 1 : 0.5,
                    pointerEvents: enabled ? "auto" : "none" }}>
        <div className="kv" style={{ alignItems: "center" }}>
          <span style={{ flex: "0 0 120px" }}>Interval</span>
          <input type="range" min={3} max={60} step={1} value={intervalS}
                 onChange={(e) => apply({ intervalS: Number(e.target.value) })}
                 style={{ flex: 1 }}
                 data-hint="Seconds between page changes (3 – 60). The firmware clamps higher values up to 300 s if you set them via the wire protocol directly."/>
          <span className="dim" style={{ flex: "0 0 60px", textAlign: "right" }}>{intervalS}s</span>
        </div>
        <div className="kv" style={{ alignItems: "center", marginTop: 8 }}>
          <span style={{ flex: "0 0 120px" }}>Order</span>
          <label style={{ marginRight: 12, cursor: "pointer" }}
                 data-hint="Sequential: pages appear in the order you set under Pages, skipping disabled ones.">
            <input type="radio" name="aa-mode" value="sequential"
                   checked={mode === "sequential"}
                   onChange={() => apply({ mode: "sequential" })}/>
            {" "}Sequential
          </label>
          <label style={{ cursor: "pointer" }}
                 data-hint="Random: pages appear in a shuffled order. Once every enabled page has been shown, the shuffle restarts with a fresh order.">
            <input type="radio" name="aa-mode" value="random"
                   checked={mode === "random"}
                   onChange={() => apply({ mode: "random" })}/>
            {" "}Random
          </label>
        </div>
      </div>
      <Toast msg={msg}/>
    </div>
  );
}

function hexToRgb565(hex: string): number {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return 0;
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 0xff;
  const g = (v >> 8) & 0xff;
  const b = v & 0xff;
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

const COLOR_HINTS: Record<string, string> = {
  bg:     "Screen background color.",
  fg:     "Primary text / foreground color.",
  dim:    "Muted text — labels, secondary info, separators.",
  good:   "'Healthy' accent — used for OK gauges and good values.",
  warn:   "Warning accent — gauges past their warn threshold.",
  crit:   "Critical accent — gauges past their crit threshold.",
  accent: "Brand accent — highlights, the title bar dot, the active page.",
};

const SCALE_HINTS: Record<string, string> = {
  title: "Size multiplier for page titles.",
  label: "Size multiplier for field labels and small text.",
  value: "Size multiplier for metric values and key/value rows.",
  big:   "Size multiplier for the large hero numbers.",
};

const THRESHOLD_HINTS: Record<string, string> = {
  cpu_warn:          "CPU % at which the CPU gauge turns amber.",
  cpu_crit:          "CPU % at which the CPU gauge turns red.",
  ram_warn:          "RAM % at which the RAM gauge turns amber.",
  ram_crit:          "RAM % at which the RAM gauge turns red.",
  calendar_soon_min: "Minutes-before-an-event at which the device flags it as 'soon'.",
  commit_fresh_min:  "Minutes since last commit before the device flags your work as stale.",
};

function ThemePane() {
  const [brightness, setBrightness] = useState<number>(100);
  const [colors, setColors] = useState({
    bg:     "#0A0E14",
    fg:     "#FFFFFF",
    dim:    "#7BEBEF",
    good:   "#3FB950",
    warn:   "#D29922",
    crit:   "#F85149",
    accent: "#4D7FE8",
  });
  const [thresholds, setThresholds] = useState({
    cpu_warn: 70, cpu_crit: 90,
    ram_warn: 80, ram_crit: 95,
    calendar_soon_min: 15,
    commit_fresh_min: 30,
  });
  const [scales, setScales] = useState({ title: 1, label: 1, value: 1, big: 1 });
  const [msg, setMsg] = useState("");
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 1800); };

  const applyColors = () => {
    const payload: Record<string, number> = {};
    for (const [k, v] of Object.entries(colors)) payload[k] = hexToRgb565(v);
    window.dashd.sendCmd({ name: "set_theme", colors: payload });
    flash("Colors applied");
  };
  const applyThresholds = () => {
    window.dashd.sendCmd({ name: "set_thresholds", thresholds });
    flash("Thresholds applied");
  };
  const applyBrightness = () => {
    window.dashd.sendCmd({ name: "set_brightness", value: brightness });
  };
  const applyScales = () => {
    window.dashd.sendCmd({ name: "set_text_scales", scales });
    flash("Text scales applied");
  };

  return (
    <>
      <div className="card">
        <h3>Backlight</h3>
        <div className="kv"><span className="k">brightness</span><span className="v">{brightness}%</span></div>
        <input type="range" min={0} max={100} value={brightness}
               onChange={(e) => setBrightness(Number(e.target.value))}
               onMouseUp={applyBrightness} style={{ width: "100%" }}
               data-hint="Display backlight level. Applied to the device when you release the slider."/>
      </div>

      <div className="card">
        <h3>Colors</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          Picker values are converted to RGB565 and persisted in the device's NVS.
        </p>
        {Object.entries(colors).map(([k, v]) => (
          <div className="kv" key={k}>
            <span className="k">{k}</span>
            <input type="color" value={v}
                   onChange={(e) => setColors((p) => ({ ...p, [k]: e.target.value }))}
                   style={{ width: 60, height: 28, padding: 0, background: "transparent", border: "1px solid var(--border)", borderRadius: 4 }}
                   data-hint={COLOR_HINTS[k] ?? `The "${k}" theme color.`}/>
          </div>
        ))}
        <button className="btn primary" onClick={applyColors} style={{ marginTop: 10 }}
                data-hint="Send all seven theme colors to the device (converted to RGB565).">Apply colors</button>
      </div>

      <div className="card">
        <h3>Text sizes</h3>
        <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
          Per-role multiplier (1× – 4×). Larger sizes may overflow narrow screen layouts.
        </p>
        {(["title", "label", "value", "big"] as const).map((k) => (
          <div className="kv" key={k} style={{ alignItems: "center" }}>
            <span className="k" style={{ minWidth: 60 }}>{k}</span>
            <span className="v" style={{ flex: 1, paddingLeft: 16 }}>
              <input type="range" min={1} max={4} value={scales[k]}
                     onChange={(e) => setScales((p) => ({ ...p, [k]: Number(e.target.value) }))}
                     style={{ width: "70%" }}
                     data-hint={SCALE_HINTS[k]}/>
              <span style={{ marginLeft: 8 }}>{scales[k]}×</span>
            </span>
          </div>
        ))}
        <button className="btn primary" onClick={applyScales} style={{ marginTop: 10 }}
                data-hint="Send the four text-size multipliers to the device.">Apply sizes</button>
        <Toast msg={msg}/>
      </div>

      <div className="card">
        <h3>Thresholds</h3>
        {(Object.keys(thresholds) as Array<keyof typeof thresholds>).map((k) => (
          <div className="kv" key={k} style={{ alignItems: "center" }}>
            <span className="k">{k}</span>
            <span className="v" style={{ flex: 1, paddingLeft: 16 }}>
              <input type="range" min={0} max={k.includes("min") ? 240 : 100} value={thresholds[k]}
                     onChange={(e) => setThresholds((p) => ({ ...p, [k]: Number(e.target.value) }))}
                     style={{ width: "70%" }}
                     data-hint={THRESHOLD_HINTS[k]}/>
              <span style={{ marginLeft: 8 }}>{thresholds[k]}{k.includes("min") ? "m" : "%"}</span>
            </span>
          </div>
        ))}
        <button className="btn primary" onClick={applyThresholds} style={{ marginTop: 10 }}
                data-hint="Send all warning/critical thresholds to the device — they drive the amber/red colors on gauges.">Apply thresholds</button>
      </div>
    </>
  );
}

function LayoutPane() {
  const [showTitle, setShowTitle] = useState(true);
  const [showFooter, setShowFooter] = useState(true);
  const [rotation, setRotation] = useState(0);
  const [msg, setMsg] = useState("");
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 1800); };

  const apply = () => {
    window.dashd.sendCmd({
      name: "set_layout",
      show_title: showTitle,
      show_footer: showFooter,
      rotation,
    });
    flash("Layout applied");
  };

  return (
    <div className="card">
      <h3>Layout</h3>
      <p className="dim" style={{ fontSize: 12, marginTop: 0 }}>
        Title bar shows the page name + connection indicator. Footer shows last-update
        age + n/N page counter. Hiding either gives pages more vertical space.
      </p>
      <label className="switch"
             data-hint="Show the top bar with the page name and the host-connection dot. Hiding it gives pages more vertical room.">
        <input type="checkbox" checked={showTitle}
               onChange={(e) => setShowTitle(e.target.checked)}/>
        Show title bar
      </label>
      <br/>
      <label className="switch" style={{ marginTop: 8 }}
             data-hint="Show the bottom bar with the last-update age and the n/N page counter.">
        <input type="checkbox" checked={showFooter}
               onChange={(e) => setShowFooter(e.target.checked)}/>
        Show footer
      </label>

      <div className="kv" style={{ marginTop: 16 }}>
        <span className="k">Rotation</span>
        <select value={rotation} onChange={(e) => setRotation(Number(e.target.value))}
                style={{ width: 180 }}
                data-hint="Rotate the whole display in 90° steps — handy depending on how the device is mounted.">
          <option value={0}>0° — portrait (default)</option>
          <option value={1}>90° — landscape (right)</option>
          <option value={2}>180° — upside down</option>
          <option value={3}>270° — landscape (left)</option>
        </select>
      </div>

      <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
        <button className="btn primary" onClick={apply}
                data-hint="Send the title/footer/rotation settings to the device. Persists in NVS.">Apply layout</button>
        <Toast msg={msg}/>
      </div>
    </div>
  );
}
