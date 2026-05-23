# Packaging the agent + UI

dashd ships as one installable app per OS that bundles the Python agent (built via PyInstaller) inside the Electron app. End users don't need Python.

## Build the agent binary

```bash
cd agent
.venv/bin/pyinstaller dashd-agent.spec --clean
# → agent/dist/dashd-agent  (macOS/Linux)  or  agent/dist/dashd-agent.exe  (Windows)
```

The spec lives at [agent/dashd-agent.spec](../agent/dashd-agent.spec). It pins every collector module as a hidden import (PyInstaller can miss dynamically-imported things) and trims `tkinter`, `matplotlib`, etc. Output is ~22 MB.

On macOS, build once per arch (arm64, x64) by running pyinstaller under the matching Python interpreter. We can't cross-compile from one arch to the other; CI on a self-hosted runner per platform is the long-term answer.

## Build the desktop installer

```bash
cd ui
npm install
npm run build               # tsc + vite
npx electron-builder --mac --arm64    # or --win, --linux, --x64
```

Outputs land in `ui/release/`:

| Target | Artifact |
|---|---|
| macOS | `dashd-<version>-arm64.dmg`, `dashd-<version>-x64.dmg` |
| Windows | `dashd Setup <version>.exe` (NSIS, per-user, customizable install dir) |
| Linux | `dashd-<version>.AppImage`, `dashd_<version>_amd64.deb` |

Config: [ui/electron-builder.yml](../ui/electron-builder.yml). The `extraResources` block copies the agent binary into `dashd.app/Contents/Resources/agent/` (or the equivalent on other OSes) so the Electron main process can spawn it via `process.resourcesPath`.

## Cross-platform build prerequisites

You can build for **the OS you're on**. Cross-builds need extra tooling:

- macOS → Windows: needs `wine` (rough), Linux container, plus the NSIS toolchain
- macOS → Linux: works if Docker is available
- Linux → macOS: not supported without signing keys

The realistic path is one CI runner per OS (GitHub Actions has free runners for all three). v0.1.5 builds everything in CI (see `.github/workflows/build.yml`): a dedicated `firmware (esp32-c3)` job runs PlatformIO and produces the `.bin` files, alongside a per-OS matrix that builds installers for macOS (cross-built arm64 + x64 from a single macos-14 runner), Windows, and Linux. All artifacts attach to the same GitHub Release. The agent PyInstaller binary still runs separately per arch — macos-14 builds an arm64 dashd-agent that gets bundled into both the arm64 AND x64 DMGs (electron-builder cross-arch only handles the Electron framework, not the embedded agent binary). That's a known limitation; until we add an x64 PyInstaller runner, Intel-Mac users run an arm64 agent under Rosetta.

## Code signing

**macOS**: ad-hoc signed via `ui/build/afterPack.js` (`codesign --force --deep --sign -`). The signature is locally-rooted (no Apple Developer cert), so:
  - Gatekeeper still prompts on first launch: *"dashd can't be opened because the developer cannot be verified."* Right-click → **Open** → confirm. Once accepted, future launches are silent.
  - `electron-updater`'s Squirrel.Mac path still rejects ad-hoc-signed bundles — silent in-app updates aren't possible without a Developer ID Application certificate. `ui/electron/updater.ts` detects the code-signature error from Squirrel and falls back to `shell.openExternal()` on the GitHub Releases page, so the user can manually grab the new DMG. The tray menu's *"Open release page"* item is the explicit version of this. Investing in an Apple Developer cert ($99/yr) unlocks the silent path.

**Windows**: unsigned. SmartScreen will prompt: *"Windows protected your PC."* Click **More info** → **Run anyway**.

To upgrade to fully-signed builds later:
1. Apple Developer Program ($99 / yr) → replace the ad-hoc hook with a Developer ID Application identity in `electron-builder.yml` and add notarization (`afterSign` hook + `notarytool`). The Gatekeeper warning goes away.
2. Windows EV code-signing cert (~$300/yr from DigiCert/Sectigo) → set `certificateFile` + `certificatePassword` env vars in CI.

## Auto-update

Both the desktop app and the device firmware auto-update from GitHub Releases. See the [README "Auto-update" section](../README.md#auto-update) for the user-facing flow.

Pieces:
- `ui/electron-builder.yml` → `mac.target` produces both `dmg` (manual install) and `zip` (Squirrel.Mac's actual download format for auto-update). `afterPack` runs `codesign --force --deep --sign -` for ad-hoc Mac signing.
- `.github/workflows/build.yml` → CI builds with `electron-builder --publish=never` then uploads via `gh release upload <tag> ... --clobber`, including `latest-*.yml` + `*.blockmap` + `*.zip`. Older versions tried `--publish=onTagOrDraft` but electron-builder's "skip if release >2h old" guard broke every rerun.
- `ui/electron/updater.ts` → `electron-updater` wrapper that drives the renderer popup + tray menu.
- `agent/dashd/firmware_update.py` → fetches the latest release JSON, downloads the matching `.bin`, streams it to the device over the active transport (USB or BLE).
- `firmware/src/ota_link.cpp` → ESP-IDF `esp_ota_*` wrapper with SHA256 verification + auto-rollback.

Each release tag should attach:
- `dashd-<v>-arm64.dmg` + `.blockmap` (macOS Apple Silicon)
- `dashd-<v>-x64.dmg` + `.blockmap` (macOS Intel)
- `dashd-<v>-x64.exe` + `.blockmap` (Windows)
- `dashd-<v>-x86_64.AppImage` + `dashd-<v>-amd64.deb` (Linux)
- `dashd-firmware-v<v>-ble.bin` (firmware with BLE + USB)
- `dashd-firmware-v<v>-usb.bin` (firmware, USB-only build, smaller)
- `latest-mac.yml`, `latest-linux.yml`, `latest.yml` (electron-updater feeds — produced automatically)

## What happens at install time

The installer drops the app into the standard location for the OS (Applications, Program Files, /opt). It does **not** install the LaunchAgent / systemd unit / Run key — that happens on **first launch**, only if the user opts in via the first-run wizard. See [docs/autostart.md](autostart.md).
