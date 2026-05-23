/**
 * Spawn + supervise the dashd-agent binary as a child process.
 *
 * Auto-restart with exponential backoff (1s → 30s). The agent itself handles
 * USB reconnect, so a true crash here usually means a Python-level fault —
 * still worth restarting, but log it loudly.
 */
import { spawn, ChildProcess } from "node:child_process";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import net from "node:net";

const TOKEN_PATH = path.join(os.homedir(), ".config", "dashd", "ipc.token");

/**
 * Probe whether a *dashd agent* is already listening on 127.0.0.1:`port`.
 *
 * A plain TCP-connect check isn't enough — some unrelated local service
 * could hold the port, and treating that as "agent already running" would
 * make the supervisor skip spawning forever. So we speak the actual dashd
 * handshake: connect, send `hello` with the token, and require a
 * `hello_ack` line back. Any dashd agent answers that (even with ok=false
 * on a token mismatch); a foreign process won't, so we fall through to
 * spawning our own.
 */
async function probeDashdAgent(port: number, host = "127.0.0.1"): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = net.createConnection({ port, host });
    let buf = "";
    let settled = false;
    const done = (v: boolean) => {
      if (settled) return;
      settled = true;
      sock.removeAllListeners();
      try { sock.destroy(); } catch { /* ignore */ }
      resolve(v);
    };
    sock.once("connect", () => {
      let token = "";
      try { token = fs.readFileSync(TOKEN_PATH, "utf-8").trim(); } catch { /* ignore */ }
      sock.write(JSON.stringify({ type: "hello", token }) + "\n");
    });
    sock.on("data", (chunk) => {
      buf += chunk.toString("utf-8");
      const nl = buf.indexOf("\n");
      if (nl === -1) return;
      try {
        const msg = JSON.parse(buf.slice(0, nl));
        done(!!(msg && msg.type === "hello_ack"));
      } catch {
        done(false); // first line wasn't JSON — not a dashd agent
      }
    });
    sock.once("error", () => done(false));
    sock.setTimeout(600, () => done(false));
  });
}

export type AgentStatus =
  | { running: false; reason?: string }
  | { running: true; pid: number; startedAt: number };

export type SupervisorOptions = {
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  onLog?: (line: string) => void;
  onStatusChange?: (s: AgentStatus) => void;
  /**
   * IPC port the agent is expected to bind. The supervisor probes it before
   * spawning — if something already responds there, we assume a peer agent
   * (CLI run, prior Electron session) is alive and attach to it instead of
   * spawning a duplicate that would fail to bind.
   */
  ipcPort?: number;
};

export class AgentSupervisor {
  private child: ChildProcess | null = null;
  private restartDelay = 1000;
  private readonly maxDelay = 30_000;
  private restartTimer: NodeJS.Timeout | null = null;
  private stopping = false;
  private buf = "";

  constructor(private binary: string, private opts: SupervisorOptions = {}) {}

  async start(): Promise<void> {
    if (!fs.existsSync(this.binary)) {
      const err = `agent binary not found at ${this.binary}`;
      this.opts.onLog?.(err);
      this.opts.onStatusChange?.({ running: false, reason: err });
      return;
    }
    this.stopping = false;

    // If a dashd agent is already listening on the IPC port, attach to it
    // instead of spawning a competing one — the second process would just
    // get EADDRINUSE and exit, looping forever. The probe speaks the dashd
    // handshake so an unrelated process on the port doesn't fool us.
    const port = this.opts.ipcPort;
    if (port && (await probeDashdAgent(port))) {
      this.opts.onLog?.(
        `[supervisor] dashd-agent already listening on :${port} — attaching to existing instance`
      );
      this.opts.onStatusChange?.({ running: true, pid: -1, startedAt: Date.now() });
      return;
    }

    this.spawn();
  }

  private spawn(): void {
    this.opts.onLog?.(`[supervisor] spawn ${this.binary}`);
    const child = spawn(this.binary, this.opts.args ?? [], {
      cwd: this.opts.cwd,
      env: { ...process.env, ...this.opts.env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;
    this.opts.onStatusChange?.({
      running: true,
      pid: child.pid ?? -1,
      startedAt: Date.now(),
    });

    const onChunk = (chunk: Buffer) => {
      this.buf += chunk.toString("utf-8");
      let nl = this.buf.indexOf("\n");
      while (nl !== -1) {
        const line = this.buf.slice(0, nl);
        this.buf = this.buf.slice(nl + 1);
        if (line) this.opts.onLog?.(line);
        nl = this.buf.indexOf("\n");
      }
    };
    child.stdout?.on("data", onChunk);
    child.stderr?.on("data", onChunk);

    child.on("exit", (code, signal) => {
      this.opts.onLog?.(
        `[supervisor] agent exited code=${code} signal=${signal}`
      );
      this.child = null;
      this.opts.onStatusChange?.({
        running: false,
        reason: `exit code=${code} signal=${signal}`,
      });
      if (!this.stopping) {
        // Exit code 3 = port-busy / EADDRINUSE (see agent/dashd/main.py).
        // EADDRINUSE alone doesn't tell us *what* holds the port — it could
        // be a real dashd peer (attach to it) or a foreign process (genuine
        // conflict). Re-probe with the dashd handshake to decide.
        if (code === 3 && this.opts.ipcPort) {
          const port = this.opts.ipcPort;
          probeDashdAgent(port).then((isDashd) => {
            if (this.stopping) return;
            if (isDashd) {
              this.opts.onLog?.(
                `[supervisor] peer dashd-agent already owns :${port} — attaching, not respawning`
              );
              this.opts.onStatusChange?.({ running: true, pid: -1, startedAt: Date.now() });
            } else {
              // A non-dashd process squats the port. Respawning will just
              // hit EADDRINUSE again, but the squatter may release it, so
              // retry with backoff and surface the conflict.
              this.opts.onLog?.(
                `[supervisor] :${port} held by a non-dashd process — cannot start agent; retrying`
              );
              this.opts.onStatusChange?.({
                running: false,
                reason: `ipc port ${port} held by a foreign process`,
              });
              this.restartTimer = setTimeout(() => this.spawn(), this.restartDelay);
              this.restartDelay = Math.min(this.restartDelay * 2, this.maxDelay);
            }
          });
          return;
        }
        this.restartTimer = setTimeout(() => this.spawn(), this.restartDelay);
        this.restartDelay = Math.min(this.restartDelay * 2, this.maxDelay);
      }
    });

    // Reset backoff if the agent stays alive for >10 s.
    setTimeout(() => {
      if (this.child === child) this.restartDelay = 1000;
    }, 10_000);
  }

  async restart(): Promise<void> {
    this.opts.onLog?.("[supervisor] restart requested");
    this.stopping = false;
    if (this.child) {
      const c = this.child;
      const done = new Promise<void>((r) => c.once("exit", () => r()));
      c.kill("SIGTERM");
      await done;
    } else {
      this.spawn();
    }
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    if (!this.child) return;
    const c = this.child;
    const done = new Promise<void>((r) => c.once("exit", () => r()));
    c.kill("SIGTERM");
    await Promise.race([
      done,
      new Promise<void>((r) =>
        setTimeout(() => {
          try {
            c.kill("SIGKILL");
          } catch {}
          r();
        }, 3000)
      ),
    ]);
  }
}
