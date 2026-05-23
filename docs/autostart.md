# Run dashd at login

The Electron app asks once on first launch ("Start dashd at login? Yes / Not now") and writes the matching per-OS autostart file when the user says yes. The toggle lives at Settings → General after that.

The autostart entry runs the **agent binary** (`dashd-agent`), not the Electron app. The UI is a window; the agent is the service.

## macOS — launchd

A per-user LaunchAgent at:

```
~/Library/LaunchAgents/ro.softwarechef.dashd.agent.plist
```

```xml
<plist version="1.0">
<dict>
  <key>Label</key>            <string>ro.softwarechef.dashd.agent</string>
  <key>ProgramArguments</key> <array><string>/path/to/dashd-agent</string></array>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>StandardOutPath</key>  <string>~/Library/Logs/dashd.log</string>
  <key>StandardErrorPath</key><string>~/Library/Logs/dashd.err.log</string>
</dict>
</plist>
```

Loaded with `launchctl bootstrap gui/<uid> <plist>`; unloaded with `launchctl bootout`.

## Linux — systemd-user

Unit at `~/.config/systemd/user/dashd.service`:

```
[Unit]
Description=dashd desk widget agent (USB + Bluetooth LE)

[Service]
ExecStart=/path/to/dashd-agent
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Activated with `systemctl --user enable --now dashd.service`. Requires lingering enabled (`loginctl enable-linger $USER`) if you want it running while you're not logged in.

## Windows — Run key

Registry value:

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\dashd = "C:\Path\To\dashd-agent.exe"
```

Set with `reg add`, removed with `reg delete`. Runs on user login; doesn't run when the user isn't signed in (use a proper Windows Service for that — out of scope for v1).

## What the toggle does NOT do

- It doesn't start the **UI window** at login. By design — the data should flow whether the GUI is open or not.
- It doesn't open the firewall, request notification permissions, or grant Full Disk Access. Those are still per-user OS prompts that the agent surfaces when it needs them (see [docs/collectors.md](collectors.md) for the iMessage FDA story).

## Disabling

Settings → General → toggle off. The app removes the plist / unit / Run key on the spot.
