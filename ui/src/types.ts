/** Shared types between renderer and main, mirroring the agent's protocol. */

export type SystemState = {
  cpu_pct?: number[];
  ram_pct?: number;
  ram_used_gb?: number;
  ram_total_gb?: number;
  disk_pct?: number;
  net_up_kbps?: number;
  net_down_kbps?: number;
  battery_pct?: number | null;
  battery_charging?: boolean | null;
  temp_cpu_c?: number | null;
};

export type AIState = {
  claude_code?: {
    // Today
    tokens_today?: number;
    cost_today_usd?: number;
    // This block
    tokens_block?: number;
    cost_block_usd?: number;
    // Block progress — keep legacy `block_pct` (alias of block_elapsed_pct)
    // for compatibility with any older code paths still reading it.
    block_pct?: number | null;
    block_elapsed_pct?: number | null;     // time-elapsed in 5h window
    block_used_pct?: number | null;        // tokens / configured budget
    block_resets_in_min?: number | null;
    block_resets_at?: number | null;       // epoch seconds
    // Week
    tokens_this_week?: number;
    cost_this_week_usd?: number;
    // Breakdown
    models?: Record<string, number>;
    top_projects?: Array<{ name: string; tokens: number }>;
    // Burn rate
    burn_tokens_per_min?: number | null;
    burn_projected_cap_min?: number | null;
  };
  codex?: {
    tokens_today?: number | null;
    cost_today_usd?: number | null;
    block_pct?: number | null;
    block_elapsed_pct?: number | null;
    block_used_pct?: number | null;
    block_resets_in_min?: number | null;
    block_resets_at?: number | null;
    session_active?: boolean;
  };
};

export type GitState = {
  branch?: string;
  commits_today?: number;
  loc_added?: number;
  loc_removed?: number;
  minutes_since_last_commit?: number | null;
};

// Anthropic OAuth Usage block (opt-in, only present when the agent's
// AnthropicOAuthClient is enabled + reachable).
export type AnthropicUsageWindow = {
  used_pct: number;
  resets_at?: number | null;
  resets_in_min?: number | null;
  window_minutes?: number | null;
  pace_delta_pct?: number | null;
  pace_status?: string | null;
  will_last_to_reset?: boolean | null;
  eta_to_cap_min?: number | null;
};

export type AnthropicState = {
  available: boolean;
  reason?: string;
  session?: AnthropicUsageWindow;
  weekly?: AnthropicUsageWindow;
  sonnet_weekly?: AnthropicUsageWindow;
  extra_usage?: {
    enabled: boolean;
    limit_usd: number;
    used_usd: number;
    used_pct: number;
    currency: string;
  };
};

export type StateFrame = {
  type: "state";
  ts: number;
  system?: SystemState | null;
  ai?: AIState | null;
  git?: GitState | null;
  github?: {
    prs_awaiting_review?: number;
    ci_failures_24h?: number;
    unread_notifications?: number;
  } | null;
  calendar?: {
    next_event_title?: string | null;
    next_event_in_min?: number | null;
    today_remaining?: number | null;
  } | null;
  messages?: Record<string, { unread?: number } | null> | null;
  anthropic?: AnthropicState | null;
};

export type LogEvent = {
  type: "event";
  name: "log";
  level: "debug" | "info" | "warn" | "warning" | "error";
  logger?: string;
  msg: string;
};

export type StatusEvent = {
  type: "event";
  name: "agent_status";
  connected: boolean;
  port?: string | null;
};

export type AgentMessage =
  | StateFrame
  | LogEvent
  | StatusEvent
  | { type: "event"; name: string; [k: string]: any }
  | { type: "hello_ack"; ok: boolean };

export type UIPrefs = {
  firstRunComplete?: boolean;
  autostart?: boolean;
  ipcPort?: number;
};

declare global {
  interface Window {
    dashd: import("../electron/preload").DashdAPI;
  }
}
