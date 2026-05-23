/**
 * Cross-platform run-at-login management.
 *
 * macOS: a per-user launchd LaunchAgent at
 *   ~/Library/LaunchAgents/ro.softwarechef.dashd.agent.plist
 *
 * Linux: a systemd-user unit at
 *   ~/.config/systemd/user/dashd.service
 *
 * Windows: registry value at
 *   HKCU\Software\Microsoft\Windows\CurrentVersion\Run\dashd
 *
 * All three start the agent binary directly — not the Electron app — so the
 * UI window stays explicit and the data keeps flowing whether or not the user
 * has the GUI open.
 */
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawnSync, execSync } from "node:child_process";

const LABEL = "ro.softwarechef.dashd.agent";

function macPlistPath(): string {
  return path.join(os.homedir(), "Library", "LaunchAgents", `${LABEL}.plist`);
}

function linuxUnitPath(): string {
  return path.join(os.homedir(), ".config", "systemd", "user", "dashd.service");
}

function macPlistBody(binary: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${binary}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${path.join(os.homedir(), "Library", "Logs", "dashd.log")}</string>
  <key>StandardErrorPath</key>
  <string>${path.join(os.homedir(), "Library", "Logs", "dashd.err.log")}</string>
</dict>
</plist>
`;
}

function linuxUnitBody(binary: string): string {
  return `[Unit]
Description=dashd USB-tethered desk widget agent
After=default.target

[Service]
ExecStart=${binary}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
`;
}

export async function applyAutostart(enable: boolean, binary: string): Promise<void> {
  if (process.platform === "darwin") {
    const plist = macPlistPath();
    if (enable) {
      await fs.mkdir(path.dirname(plist), { recursive: true });
      await fs.writeFile(plist, macPlistBody(binary));
      // bootstrap if not already loaded; ignore "service already loaded" errors.
      spawnSync("launchctl", ["bootstrap", `gui/${process.getuid?.() ?? ""}`, plist]);
    } else {
      spawnSync("launchctl", ["bootout", `gui/${process.getuid?.() ?? ""}`, plist]);
      try { await fs.unlink(plist); } catch {}
    }
  } else if (process.platform === "linux") {
    const unit = linuxUnitPath();
    if (enable) {
      await fs.mkdir(path.dirname(unit), { recursive: true });
      await fs.writeFile(unit, linuxUnitBody(binary));
      spawnSync("systemctl", ["--user", "daemon-reload"]);
      spawnSync("systemctl", ["--user", "enable", "--now", "dashd.service"]);
    } else {
      spawnSync("systemctl", ["--user", "disable", "--now", "dashd.service"]);
      try { await fs.unlink(unit); } catch {}
      spawnSync("systemctl", ["--user", "daemon-reload"]);
    }
  } else if (process.platform === "win32") {
    const key = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
    if (enable) {
      execSync(`reg add "${key}" /v dashd /t REG_SZ /d "${binary}" /f`);
    } else {
      try { execSync(`reg delete "${key}" /v dashd /f`); } catch {}
    }
  }
}

export async function getAutostartState(): Promise<boolean> {
  if (process.platform === "darwin") {
    try { await fs.access(macPlistPath()); return true; } catch { return false; }
  }
  if (process.platform === "linux") {
    try { await fs.access(linuxUnitPath()); return true; } catch { return false; }
  }
  if (process.platform === "win32") {
    try {
      const out = execSync(
        `reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v dashd`
      ).toString();
      return out.includes("dashd");
    } catch { return false; }
  }
  return false;
}
