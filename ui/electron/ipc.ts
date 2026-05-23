/**
 * TCP client for the agent's local IPC server.
 *
 * Same newline-JSON framing as the USB link. Reads the token from
 * ~/.config/dashd/ipc.token. Single active connection at a time — any prior
 * socket is destroyed before a new connect attempt. A `close` event only
 * triggers a reconnect when it belongs to the *current* socket, so a late
 * close from an old socket can't blank out a freshly-opened one.
 */
import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { EventEmitter } from "node:events";

const TOKEN_PATH = path.join(os.homedir(), ".config", "dashd", "ipc.token");

export class AgentClient extends EventEmitter {
  private sock: net.Socket | null = null;
  private connecting = false;
  private buf = "";
  private closed = false;
  private backoff = 500;
  private readonly minBackoff = 500;
  private readonly maxBackoff = 10_000;
  // We only consider the link "stable" after the server's hello_ack lands,
  // so backoff is reset there rather than on raw TCP connect success. This
  // prevents a tight reconnect loop when the server accepts the socket and
  // then closes it immediately (e.g. bad token).
  private stable = false;

  constructor(private port: number, private host = "127.0.0.1") {
    super();
  }

  async connectWithRetry(): Promise<void> {
    if (this.connecting) return; // already trying — don't pile on
    this.connecting = true;
    try {
      // Always wait at least `minBackoff` between attempts — even on the
      // first one after a clean close — so a "connect succeeds then server
      // closes" cycle can't busy-loop. We bump `backoff` whether the failure
      // came from connect or from a post-connect close, and only reset it
      // once we've actually received a hello_ack (see onData).
      while (!this.closed) {
        try {
          await new Promise((r) => setTimeout(r, this.backoff));
          await this.connectOnce();
          return;
        } catch {
          this.backoff = Math.min(Math.max(this.backoff, this.minBackoff) * 2,
                                  this.maxBackoff);
        }
      }
    } finally {
      this.connecting = false;
    }
  }

  private destroyCurrentSocket(): void {
    if (this.sock) {
      const s = this.sock;
      this.sock = null;
      try {
        s.removeAllListeners();
        s.destroy();
      } catch {
        // ignore
      }
    }
  }

  private connectOnce(): Promise<void> {
    return new Promise((resolve, reject) => {
      // Tear down any leftover socket before opening a new one.
      this.destroyCurrentSocket();

      const sock = net.createConnection({ host: this.host, port: this.port });

      const onError = (e: Error) => {
        // Only matters before connect resolves; after that, 'close' takes over.
        cleanup();
        reject(e);
      };
      const onConnect = () => {
        this.sock = sock;
        this.buf = "";
        this.handshake();
        this.emit("connected");
        sock.off("error", onError); // post-connect errors will surface via 'close'
        resolve();
      };

      const cleanup = () => {
        sock.off("error", onError);
        sock.off("connect", onConnect);
      };

      sock.once("connect", onConnect);
      sock.once("error", onError);
      sock.on("data", (chunk) => this.onData(chunk));
      sock.on("close", () => {
        // Ignore the close of a socket we already replaced.
        if (this.sock !== sock) return;
        this.sock = null;
        this.emit("disconnected");
        // If the server killed us before hello_ack landed, that's a failed
        // attempt — bump the backoff so we don't busy-loop on a rejected
        // handshake. A close *after* stable means a transient drop; let
        // connectWithRetry's existing pre-attempt delay handle the pacing.
        if (!this.stable) {
          this.backoff = Math.min(Math.max(this.backoff, this.minBackoff) * 2,
                                  this.maxBackoff);
        }
        this.stable = false;
        if (!this.closed) {
          // Schedule reconnect on the next tick so we don't recurse from the
          // close handler stack frame.
          setImmediate(() => this.connectWithRetry().catch(() => {}));
        }
      });
      // Belt-and-braces: silently swallow any post-connect error so the
      // process doesn't crash with an unhandled 'error' event. The close
      // handler will run shortly after and trigger reconnect.
      sock.on("error", () => {});
    });
  }

  private handshake(): void {
    let token = "";
    try {
      token = fs.readFileSync(TOKEN_PATH, "utf-8").trim();
    } catch {
      // Token file may not exist yet on a fresh install — agent creates it
      // on startup. We'll fail this hello, the server will close us, and
      // the close-driven reconnect will pick up once the file is there.
    }
    this.write({ type: "hello", token });
  }

  private onData(chunk: Buffer): void {
    this.buf += chunk.toString("utf-8");
    let nl = this.buf.indexOf("\n");
    while (nl !== -1) {
      const line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + 1);
      if (line) {
        try {
          const msg = JSON.parse(line);
          // First server message after a successful handshake is hello_ack.
          // Treat that as proof the link is stable and reset the backoff so
          // a future drop starts retrying quickly.
          if (msg && msg.type === "hello_ack" && msg.ok) {
            this.stable = true;
            this.backoff = this.minBackoff;
          }
          this.emit("message", msg);
        } catch {
          // ignore malformed lines
        }
      }
      nl = this.buf.indexOf("\n");
    }
  }

  sendCmd(cmd: object): void {
    this.write({ type: "cmd", ...cmd });
  }

  private write(obj: object): void {
    if (!this.sock) return;
    try {
      this.sock.write(JSON.stringify(obj) + "\n");
    } catch {
      // socket might be mid-close; reconnect logic picks up.
    }
  }

  close(): void {
    this.closed = true;
    this.destroyCurrentSocket();
  }
}
