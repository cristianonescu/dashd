import { useAtom, $state } from "../store";

function Bar({ pct, warnAt = 70, critAt = 90 }: { pct?: number; warnAt?: number; critAt?: number }) {
  const v = Math.max(0, Math.min(100, pct ?? 0));
  const cls = pct == null ? "" : v >= critAt ? "crit" : v >= warnAt ? "warn" : "good";
  return <div className="bar"><div className={`fill ${cls}`} style={{ width: `${v}%` }} /></div>;
}

function fmtTokens(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(1) + "k";
  return (n / 1_000_000).toFixed(2) + "M";
}

/** Format an integer minute count as "Nm" or "Hh MMm" — used for the
 * 5h-block reset countdown and the burn-rate projection. */
function fmtMin(n?: number | null): string {
  if (n == null) return "—";
  if (n < 60) return `${n}m`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

function avg(arr?: number[]): number | undefined {
  if (!arr || arr.length === 0) return undefined;
  return Math.round(arr.reduce((a, b) => a + b, 0) / arr.length);
}

export default function LiveView() {
  const s = useAtom($state);
  if (!s) return <div className="empty">Waiting for first state frame from agent…</div>;

  const sys: any = s.system ?? {};
  const cc = s.ai?.claude_code ?? {};
  const cx = s.ai?.codex ?? {};
  const git = s.git ?? {};
  const gh = s.github ?? {};
  const cal = s.calendar ?? {};
  const msgs = s.messages ?? {};
  const suggestions: Array<{ severity: "crit" | "warn" | "info"; text: string }> =
    (s as any).suggestions ?? [];

  const cpuAvg = avg(sys.cpu_pct);
  const ramPress = sys.ram_pressure_pct ?? sys.ram_pct;

  return (
    <div className="pane">
      {suggestions.length > 0 && (
        <div className="card">
          <h3 data-hint="Ranked, real-time advice from the agent's rule engine — the same list shown on the device's Tips page. Red = act now, amber = watch, blue = informational.">Suggestions</h3>
          <div className="sugg-list">
            {suggestions.map((s, i) => (
              <div key={i} className={`sugg ${s.severity}`}>
                <span className="badge">{s.severity === "crit" ? "!" : s.severity === "warn" ? "▲" : "i"}</span>
                <span>{s.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="row">
        <div className="col">
          <CpuCard sys={sys}/>
          <MemoryCard sys={sys}/>
          <GpuCard gpu={(s as any).gpu ?? null}/>
          <NetworkCard sys={sys}/>

          <div className="card">
            <h3 data-hint="AI coding-tool usage. Claude Code figures come from ~/.claude/projects; Codex from ~/.codex/sessions.">AI Spend</h3>
            <div className="stat-hero" data-hint="Estimated US-dollar cost of today's Claude Code token usage.">
              <span className="num">${cc.cost_today_usd?.toFixed(2) ?? "—"}</span>
              <span className="unit">today · Claude Code</span>
            </div>

            <div className="kv" data-hint="Total Claude Code tokens used today across all models.">
              <span className="k">Tokens today</span>
              <span className="v">{fmtTokens(cc.tokens_today)}</span>
            </div>

            {/* Tokens / cost this 5h block — the same denominator the
                firmware's AI Spend page surfaces. tokens_block is more
                actionable than tokens_today when you're hunting for what
                spent your budget. */}
            {(cc.tokens_block != null || cc.cost_block_usd != null) && (
              <div className="kv" data-hint="Claude Code tokens consumed since the current 5h rate-limit window opened.">
                <span className="k">This block</span>
                <span className="v">
                  {fmtTokens(cc.tokens_block)}
                  {cc.cost_block_usd != null && (
                    <span className="dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                      ${cc.cost_block_usd.toFixed(2)}
                    </span>
                  )}
                </span>
              </div>
            )}

            {/* Time-elapsed % (always available when activity exists in
                the window). Labelled "elapsed" so it isn't read as
                "% of tokens used". */}
            <div className="kv" data-hint="Time elapsed in the current 5h rate-limit window. Distinct from quota usage — see 'used' below if a per-block token budget is configured.">
              <span className="k">5h block · elapsed</span>
              <span className="v">
                {(cc.block_elapsed_pct ?? cc.block_pct) != null
                  ? `${cc.block_elapsed_pct ?? cc.block_pct}%`
                  : "—"}
                {cc.block_resets_in_min != null && (
                  <span className="dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                    {fmtMin(cc.block_resets_in_min)} to reset
                  </span>
                )}
              </span>
            </div>
            <Bar pct={(cc.block_elapsed_pct ?? cc.block_pct) ?? undefined}
                 warnAt={75} critAt={90} />

            {/* Quota-used % — only when a per-block budget is configured
                (DASHD_CLAUDE_BLOCK_BUDGET or [collectors.claude_code]
                block_token_budget). This is the gauge users actually
                want — "how much of my quota have I burned". */}
            {cc.block_used_pct != null && (
              <>
                <div className="kv" data-hint="Tokens consumed this block as a fraction of the configured per-block budget. Only shown when [collectors.claude_code] block_token_budget is set.">
                  <span className="k">5h block · used</span>
                  <span className="v">
                    {cc.block_used_pct}%
                    {cc.burn_projected_cap_min != null && (
                      <span className={cc.burn_projected_cap_min < 30 ? "warn" : "dim"}
                            style={{ marginLeft: 8, fontWeight: 500 }}>
                        hits cap in {fmtMin(cc.burn_projected_cap_min)}
                      </span>
                    )}
                  </span>
                </div>
                <Bar pct={cc.block_used_pct} warnAt={75} critAt={90} />
              </>
            )}

            {/* Burn rate row — useful even without a budget. */}
            {cc.burn_tokens_per_min != null && cc.burn_tokens_per_min > 0 && (
              <div className="kv" data-hint="Average tokens per minute over the elapsed portion of this 5h block.">
                <span className="k">Burn rate</span>
                <span className="v">{fmtTokens(cc.burn_tokens_per_min)} tok/min</span>
              </div>
            )}

            {/* Last 7 days — context for whether "today" is a heavy day
                or just normal. */}
            {(cc.tokens_this_week != null && cc.tokens_this_week > 0) && (
              <div className="kv" data-hint="Claude Code tokens + dollars across the last 7 days.">
                <span className="k">Last 7 days</span>
                <span className="v">
                  {fmtTokens(cc.tokens_this_week)}
                  {cc.cost_this_week_usd != null && (
                    <span className="dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                      ${cc.cost_this_week_usd.toFixed(2)}
                    </span>
                  )}
                </span>
              </div>
            )}

            {/* Top projects — surfaces where today's tokens went. The
                firmware can't show this since it has no place for a list. */}
            {cc.top_projects && cc.top_projects.length > 0 && (
              <div className="kv" data-hint="Top 3 codebases by Claude Code tokens consumed today. Project names are derived from the cwd that Claude Code recorded.">
                <span className="k">Top projects</span>
                <span className="v" style={{ textAlign: "right", flex: 1, minWidth: 0 }}>
                  {cc.top_projects.map((p, i) => (
                    <span key={p.name} style={{
                      display: "block", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap",
                      fontWeight: i === 0 ? 500 : 400,
                      opacity: 1 - (i * 0.18),
                    }}>
                      {p.name} <span className="dim">· {fmtTokens(p.tokens)}</span>
                    </span>
                  ))}
                </span>
              </div>
            )}

            {/* Codex — tokens are real now (cumulative→delta diff). */}
            <div className="kv" data-hint="Codex tokens consumed today. dashd derives these from cumulative session totals; cost stays — because Anthropic/OpenAI don't publish Codex pricing.">
              <span className="k">Codex tokens today</span>
              <span className="v">
                {fmtTokens(cx.tokens_today)}
                {cx.session_active && <span className="good" style={{ marginLeft: 8, fontWeight: 500 }}>· live</span>}
              </span>
            </div>
            <div className="kv" data-hint="Codex's reported quota usage % for the current 5h window. Distinct from Claude's time-elapsed metric — this IS the actual usage %.">
              <span className="k">Codex block · used</span>
              <span className="v">
                {(cx.block_used_pct ?? cx.block_pct) != null
                  ? `${cx.block_used_pct ?? cx.block_pct}%`
                  : "—"}
                {cx.block_resets_in_min != null && (
                  <span className="dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                    {fmtMin(cx.block_resets_in_min)} to reset
                  </span>
                )}
              </span>
            </div>
            <Bar pct={(cx.block_used_pct ?? cx.block_pct) ?? undefined}
                 warnAt={75} critAt={90} />
          </div>
        </div>

        <div className="col">
          <div className="card">
            <h3 data-hint="Git activity across the repositories listed in config.toml.">Dev Flow</h3>
            <div className="kv" data-hint="Current branch of the active repository."><span className="k">Branch</span><span className="v">{git.branch ?? "—"}</span></div>
            <div className="kv" data-hint="Number of commits made today across the tracked repositories."><span className="k">Commits today</span><span className="v">{git.commits_today ?? "—"}</span></div>
            <div className="kv" data-hint="Lines of code added (green) and removed (red) today.">
              <span className="k">LOC</span>
              <span className="v">
                <span className="good">+{git.loc_added ?? 0}</span>
                <span style={{ margin: "0 6px", color: "var(--dim)" }}>·</span>
                <span className="crit">−{git.loc_removed ?? 0}</span>
              </span>
            </div>
            <div className="kv" data-hint="Minutes since the last commit — a nudge to checkpoint your work.">
              <span className="k">Last commit</span>
              <span className="v">{git.minutes_since_last_commit != null ? `${git.minutes_since_last_commit}m ago` : "—"}</span>
            </div>
          </div>

          <div className="card">
            <h3 data-hint="GitHub activity for your account. Requires a GITHUB_TOKEN env var (see Settings → Collectors).">GitHub</h3>
            <div className="kv" data-hint="Open pull requests that have requested your review."><span className="k">PRs awaiting review</span><span className="v">{gh.prs_awaiting_review ?? "—"}</span></div>
            <div className="kv" data-hint="Failed CI runs across your repositories in the last 24 hours."><span className="k">CI failures (24h)</span><span className="v">{gh.ci_failures_24h ?? "—"}</span></div>
            <div className="kv" data-hint="Unread GitHub notifications."><span className="k">Unread notifications</span><span className="v">{gh.unread_notifications ?? "—"}</span></div>
          </div>

          <div className="card">
            <h3 data-hint="Your next calendar event (Microsoft Graph) and unread-message counts per channel.">Calendar & Messages</h3>
            <div className="kv" data-hint="Title of, and minutes until, your next calendar event.">
              <span className="k">Next event</span>
              <span className="v">
                {cal.next_event_title ?? "—"}
                {cal.next_event_in_min != null && (
                  <span className="dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                    in {cal.next_event_in_min}m
                  </span>
                )}
              </span>
            </div>
            <div className="kv" data-hint="Number of calendar events still to come today."><span className="k">Today remaining</span><span className="v">{cal.today_remaining ?? "—"}</span></div>
            {Object.entries(msgs).map(([k, v]) => (
              <div className="kv" key={k}
                   data-hint={`Unread ${k} messages. Collector for this channel is configured in config.toml.`}>
                <span className="k" style={{ textTransform: "capitalize" }}>{k}</span>
                <span className="v">{v && (v as any).unread != null ? (v as any).unread : "—"}</span>
              </div>
            ))}
          </div>

          <TopProcsCard system={sys} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Resource cards (v0.1.12+). The catch-all "System" card used to roll
// CPU/RAM/disk/net/battery into one box; v0.1.12 splits each resource
// into its own card matching the dedicated device pages. The cards are
// resilient to missing fields — anything `null` renders as "—".
// ─────────────────────────────────────────────────────────────────────

function CpuCard({ sys }: { sys: any }) {
  const cpuAvg = avg(sys.cpu_pct);
  const cores: number[] = sys.cpu_pct ?? [];
  return (
    <div className="card">
      <h3 data-hint="Per-core utilization with load average, frequency, temperature and battery. Mirrors the CPU page on the device.">CPU</h3>
      <div className="kv" data-hint="Average across all CPU cores.">
        <span className="k">Avg</span>
        <span className="v">
          <span className="big-num" style={{ fontSize: 22 }}>
            {cpuAvg != null ? `${cpuAvg}%` : "—"}
          </span>
        </span>
      </div>
      <Bar pct={cpuAvg}/>
      {cores.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, margin: "8px 0 4px" }}
             data-hint="Per-core utilization. Useful for spotting one core pinned at 100% while the others idle.">
          {cores.map((pct, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
              <span className="dim" style={{ width: 14 }}>{i}</span>
              <div style={{ flex: 1 }}><Bar pct={pct}/></div>
              <span style={{ width: 30, textAlign: "right" }}>{pct}%</span>
            </div>
          ))}
        </div>
      )}
      {sys.load_1m != null && (
        <div className="kv" data-hint="UNIX load average over 1 / 5 / 15 minutes. Loosely 'number of runnable processes' — values above your core count signal contention.">
          <span className="k">Load 1/5/15</span>
          <span className="v">{sys.load_1m} · {sys.load_5m} · {sys.load_15m}</span>
        </div>
      )}
      {sys.cpu_freq_mhz != null && (
        <div className="kv" data-hint="Current CPU clock frequency. Many CPUs idle far below their max — a sustained max is a thermal/load tell.">
          <span className="k">Frequency</span>
          <span className="v">
            {sys.cpu_freq_mhz} MHz
            {sys.cpu_freq_max_mhz != null && (
              <span className="dim" style={{ marginLeft: 6, fontWeight: 400 }}>/ {sys.cpu_freq_max_mhz} max</span>
            )}
          </span>
        </div>
      )}
      {sys.temp_cpu_c != null && (
        <div className="kv" data-hint="CPU package temperature (when exposed by the OS).">
          <span className="k">Temp</span>
          <span className="v">{sys.temp_cpu_c.toFixed(0)}°C</span>
        </div>
      )}
      {sys.battery_pct != null && (
        <div className="kv" data-hint="Battery charge level. ⚡ means the machine is plugged in and charging.">
          <span className="k">Battery</span>
          <span className="v">{sys.battery_pct}%{sys.battery_charging ? " ⚡" : ""}</span>
        </div>
      )}
    </div>
  );
}

function MemoryCard({ sys }: { sys: any }) {
  const ramPress = sys.ram_pressure_pct ?? sys.ram_pct;
  return (
    <div className="card">
      <h3 data-hint="Memory pressure, swap usage, breakdown by category, and the primary disk. Mirrors the Memory page on the device.">Memory</h3>
      <div className="kv" data-hint="'Real' memory pressure — active + wired only, excluding reclaimable cache. A better 'will this swap?' signal than raw RAM used.">
        <span className="k">Pressure</span>
        <span className="v">
          {ramPress != null ? `${ramPress}%` : "—"}
          {sys.ram_used_gb != null && (
            <span className="dim" style={{ marginLeft: 8, fontWeight: 400 }}>
              {sys.ram_used_gb} / {sys.ram_total_gb} GB
            </span>
          )}
        </span>
      </div>
      <Bar pct={ramPress} warnAt={70} critAt={90}/>
      {sys.ram_swap_total_gb != null && sys.ram_swap_total_gb > 0 && (
        <>
          <div className="kv" data-hint="Swap = virtual memory paged out to disk. Non-zero values during normal load mean the system is contended.">
            <span className="k">Swap</span>
            <span className="v">
              {sys.ram_swap_used_gb} / {sys.ram_swap_total_gb} GB
              <span className="dim" style={{ marginLeft: 6, fontWeight: 400 }}>({sys.ram_swap_pct}%)</span>
            </span>
          </div>
          <Bar pct={sys.ram_swap_pct} warnAt={50} critAt={80}/>
        </>
      )}
      {(sys.ram_active_gb != null || sys.ram_inactive_gb != null || sys.ram_cached_gb != null) && (
        <div data-hint="Memory broken down by lifecycle. 'Active' = recently used, 'Inactive' = could be reclaimed, 'Cached' = file-system cache." style={{ marginTop: 6 }}>
          {sys.ram_active_gb != null && (
            <div className="kv"><span className="k">Active</span><span className="v">{sys.ram_active_gb} GB</span></div>
          )}
          {sys.ram_inactive_gb != null && (
            <div className="kv"><span className="k">Inactive</span><span className="v">{sys.ram_inactive_gb} GB</span></div>
          )}
          {sys.ram_cached_gb != null && (
            <div className="kv"><span className="k">Cached</span><span className="v">{sys.ram_cached_gb} GB</span></div>
          )}
        </div>
      )}
      {sys.disk_pct != null && (
        <>
          <div className="kv" data-hint="Used space on the primary disk volume.">
            <span className="k">Disk</span>
            <span className="v">{sys.disk_pct}%</span>
          </div>
          <Bar pct={sys.disk_pct} warnAt={85} critAt={95}/>
        </>
      )}
    </div>
  );
}

function GpuCard({ gpu }: { gpu: any }) {
  if (!gpu || gpu.available === false) {
    return (
      <div className="card">
        <h3 data-hint="GPU utilization, VRAM, temperature and power. When the agent can't detect a GPU (or the platform doesn't expose stats), this card shows why.">GPU</h3>
        <p className="dim" style={{ fontSize: 13, margin: 0 }}>
          GPU stats not available
          {gpu?.reason && <> — <span className="dim">{gpu.reason}</span></>}
          .
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <h3 data-hint="GPU utilization, VRAM, temperature and power. Mirrors the GPU page on the device.">GPU</h3>
      <div className="kv">
        <span className="k">Device</span>
        <span className="v">
          {gpu.name ?? "—"}
          {gpu.count > 1 && <span className="dim" style={{ marginLeft: 6, fontWeight: 400 }}>(+{gpu.count - 1} more)</span>}
        </span>
      </div>
      {gpu.vendor && (
        <div className="kv"><span className="k">Vendor</span><span className="v">{gpu.vendor}</span></div>
      )}
      <div className="kv" data-hint="Overall GPU usage. On macOS this is IOAccelerator's Device Utilization %; on NVIDIA it's the SM utilization.">
        <span className="k">Util</span>
        <span className="v">{gpu.util_pct != null ? `${gpu.util_pct}%` : "—"}</span>
      </div>
      <Bar pct={gpu.util_pct}/>
      {gpu.vram_used_mb != null && (
        gpu.vram_total_mb != null ? (
          <>
            <div className="kv"><span className="k">VRAM</span>
              <span className="v">{gpu.vram_used_mb} / {gpu.vram_total_mb} MB</span>
            </div>
            <Bar pct={Math.round((gpu.vram_used_mb * 100) / gpu.vram_total_mb)} warnAt={80} critAt={95}/>
          </>
        ) : (
          <div className="kv" data-hint="On Apple Silicon, the GPU shares unified system memory — there's no fixed VRAM ceiling, so we show only the currently allocated amount.">
            <span className="k">VRAM</span>
            <span className="v">{gpu.vram_used_mb} MB <span className="dim" style={{ fontWeight: 400 }}>(unified)</span></span>
          </div>
        )
      )}
      {gpu.temp_c != null && (
        <div className="kv"><span className="k">Temp</span><span className="v">{gpu.temp_c}°C</span></div>
      )}
      {gpu.power_w != null && (
        <div className="kv"><span className="k">Power</span><span className="v">{gpu.power_w} W</span></div>
      )}
    </div>
  );
}

function NetworkCard({ sys }: { sys: any }) {
  const ifaces: Array<any> = sys.ifaces ?? [];
  const active = ifaces.find((i) => i.is_active) ?? ifaces[0];
  return (
    <div className="card">
      <h3 data-hint="Active outbound interface, current throughput, and a per-interface breakdown of traffic since the OS counters last reset (usually boot). Mirrors the Network page on the device.">Network</h3>
      {active ? (
        <>
          <div className="kv" data-hint="The interface routing your default outbound traffic, detected via the kernel route table.">
            <span className="k">Active</span>
            <span className="v">{active.name}</span>
          </div>
          <div className="kv" data-hint="Current download / upload throughput on the active interface, sampled over the last tick.">
            <span className="k">Throughput</span>
            <span className="v">↓ {active.down_kbps} · ↑ {active.up_kbps} kbps</span>
          </div>
        </>
      ) : (
        <div className="kv" data-hint="No active interface detected — the machine has no default route, or psutil couldn't enumerate interfaces.">
          <span className="k">Status</span><span className="v">no data</span>
        </div>
      )}
      {ifaces.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="dim" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, margin: "4px 0" }}>
            interfaces
          </div>
          {ifaces.map((i, idx) => (
            <div key={idx} className="kv" data-hint={`${i.name} — ${i.is_active ? "active outbound" : i.is_up ? "up" : "down"}. Totals are bytes seen by the OS-level counters since they last reset (typically system boot, not the calendar day).`}>
              <span className="k">
                {i.name}
                {i.is_active && <span className="dim" style={{ marginLeft: 4, fontWeight: 400 }}>*</span>}
                {!i.is_up && <span className="dim" style={{ marginLeft: 4, fontWeight: 400 }}>(down)</span>}
              </span>
              <span className="v">
                ↓ {i.down_kbps}k · ↑ {i.up_kbps}k
                <span className="dim" style={{ marginLeft: 6, fontSize: 11, fontWeight: 400 }}>
                  ({i.down_total_mb}/{i.up_total_mb} MB since boot)
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TopProcsCard({ system }: { system: any }) {
  const topRam: Array<any> = system?.top_ram ?? [];
  const topCpu: Array<any> = system?.top_cpu ?? [];
  const leak = system?.memory_leak;

  const sendAction = (target: string, action: string) => {
    if (action === "quit") {
      if (!confirm(`Send Quit (SIGTERM) to all "${target}" processes?`)) return;
    }
    window.dashd.sendCmd({ name: "proc_action", target, action } as any);
  };

  if (topRam.length === 0 && topCpu.length === 0) return null;

  return (
    <div className="card">
      <h3 data-hint="The heaviest apps right now, aggregated per application (all helper processes summed). Use the row buttons to act on one.">Top Processes</h3>
      {leak && (
        <div className="sugg warn" style={{ marginBottom: 10 }}
             data-hint="The agent tracks per-app memory over a 5-minute window. A steady climb past the threshold is flagged here as a possible leak.">
          <span className="badge">▲</span>
          <span>
            <strong>{leak.name}</strong>
            <span className="dim"> grew </span>
            +{leak.delta_mb} MB
            <span className="dim"> in </span>
            {leak.window_min}m — possible leak
          </span>
        </div>
      )}
      <h4 data-hint="Apps using the most memory, summed across all their processes.">By RAM</h4>
      {topRam.map((r, i) => <ProcRow key={`r${i}`} row={r} unit="ram" onAction={sendAction}/>)}
      <h4 style={{ marginTop: 14 }} data-hint="Apps using the most CPU, summed across all their processes.">By CPU</h4>
      {topCpu.map((r, i) => <ProcRow key={`c${i}`} row={r} unit="cpu" onAction={sendAction}/>)}
    </div>
  );
}

function ProcRow({ row, unit, onAction }: {
  row: any; unit: "ram" | "cpu";
  onAction: (target: string, action: string) => void;
}) {
  const name: string = row?.name ?? "?";
  const procs: number = row?.procs ?? 1;
  let value: string;
  if (unit === "ram") {
    const mb = row?.ram_mb ?? 0;
    value = mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
  } else {
    value = `${(row?.cpu_pct ?? 0).toFixed(0)}%`;
  }
  return (
    <div className="proc-row">
      <span
        className="name"
        data-hint={procs > 1
          ? `"${name}" — ${procs} processes (helpers, renderers, etc.) summed into one app row.`
          : `"${name}" — a single process.`}
      >
        {name}
        {procs > 1 && <span className="count">×{procs}</span>}
      </span>
      <span className="value">{value}</span>
      <span className="actions">
        <button
          data-hint={`Reveal the executable for "${name}" in Finder.`}
          onClick={() => onAction(name, "reveal")}
        >⤴</button>
        <button
          data-hint={`Open macOS Activity Monitor focused on "${name}".`}
          onClick={() => onAction(name, "activity_monitor")}
        >◫</button>
        <button
          data-hint={`Quit "${name}" — sends SIGTERM to every process under this app. You'll be asked to confirm.`}
          className="danger"
          onClick={() => onAction(name, "quit")}
        >×</button>
      </span>
    </div>
  );
}
