#!/usr/bin/env bash
#
# dashd uninstaller — macOS + Linux.
#
# Removes everything dashd leaves on disk OUTSIDE the app bundle itself:
#
#   - The running agent (best-effort SIGTERM)
#   - User config / cached state under ~/.config/dashd/
#   - macOS Library logs and the autostart LaunchAgent
#   - Linux user-systemd autostart unit
#   - electron-updater download cache
#
# The app bundle is left for you to remove manually so this script can't
# accidentally trash an open process. On macOS, drag /Applications/dashd
# to the Trash after running this. On Debian / Ubuntu, `sudo apt remove
# dashd`. On AppImage, delete the .AppImage file.
#
# Pass --dry-run to print what WOULD be removed without touching anything.
# Pass --keep-config to leave ~/.config/dashd/ in place (handy if you're
# reinstalling and want to keep your pet bundles + BLE trust list).
set -u

DRY_RUN=0
KEEP_CONFIG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)     DRY_RUN=1 ;;
    --keep-config) KEEP_CONFIG=1 ;;
    -h|--help)
      sed -n '1,/^set -u/p' "$0" | sed 's/^#//' | sed 's/^!.*//'
      exit 0
      ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

# Detect platform — POSIX-only paths use `uname` which works on both.
OS="$(uname -s)"

# ── helpers ──────────────────────────────────────────────────────────────────
say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m!\033[0m %s\n' "$*"; }

do_rm() {
  local path="$1"
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    warn "would remove: $path"
  else
    rm -rf -- "$path" && ok "removed: $path"
  fi
}

# ── 1) stop running agent ────────────────────────────────────────────────────
say "stopping any running dashd processes…"
if pgrep -fl 'dashd-agent|dashd\.app|dashd_agent' >/dev/null 2>&1; then
  if [ "$DRY_RUN" -eq 1 ]; then
    pgrep -fl 'dashd-agent|dashd\.app|dashd_agent' | sed 's/^/  /'
    warn "would SIGTERM the processes above"
  else
    pkill -TERM -f 'dashd-agent' 2>/dev/null || true
    pkill -TERM -f 'dashd_agent' 2>/dev/null || true
    pkill -TERM -f 'dashd\.app' 2>/dev/null || true
    sleep 1
    ok "sent SIGTERM"
  fi
else
  ok "no dashd processes running"
fi

# ── 2) per-OS autostart removal ─────────────────────────────────────────────
case "$OS" in
  Darwin)
    say "removing macOS autostart LaunchAgent…"
    PLIST="$HOME/Library/LaunchAgents/ro.softwarechef.dashd.agent.plist"
    if [ -f "$PLIST" ]; then
      if [ "$DRY_RUN" -eq 0 ]; then
        launchctl unload "$PLIST" 2>/dev/null || true
      fi
      do_rm "$PLIST"
    else
      ok "no LaunchAgent to remove"
    fi
    ;;
  Linux)
    say "removing Linux user-systemd autostart unit…"
    UNIT="$HOME/.config/systemd/user/dashd.service"
    if [ -f "$UNIT" ]; then
      if [ "$DRY_RUN" -eq 0 ]; then
        systemctl --user disable dashd.service 2>/dev/null || true
        systemctl --user stop    dashd.service 2>/dev/null || true
      fi
      do_rm "$UNIT"
    else
      ok "no systemd unit to remove"
    fi
    ;;
esac

# ── 3) user config + cached state ───────────────────────────────────────────
if [ "$KEEP_CONFIG" -eq 1 ]; then
  warn "--keep-config supplied; leaving ~/.config/dashd in place"
else
  say "removing user config + cached state…"
  do_rm "$HOME/.config/dashd"
fi

# ── 4) logs + auto-update cache ─────────────────────────────────────────────
case "$OS" in
  Darwin)
    say "removing macOS logs + electron-updater cache…"
    do_rm "$HOME/Library/Logs/dashd.log"
    do_rm "$HOME/Library/Logs/dashd.err.log"
    do_rm "$HOME/Library/Caches/dashd"
    do_rm "$HOME/Library/Application Support/dashd"
    ;;
  Linux)
    say "removing electron-updater cache…"
    do_rm "$HOME/.cache/dashd"
    ;;
esac

# ── 5) summary + remaining manual steps ─────────────────────────────────────
echo
if [ "$DRY_RUN" -eq 1 ]; then
  warn "dry run complete — re-run without --dry-run to actually delete"
else
  ok  "dashd state removed"
fi
echo
echo "Remaining manual step:"
case "$OS" in
  Darwin)
    echo "  • Drag /Applications/dashd.app to the Trash."
    echo "  • The ESP32 device's NVS preferences are independent — to wipe"
    echo "    them, open Settings → General → 'Reset device prefs' BEFORE"
    echo "    you uninstall, or flash blank firmware via PlatformIO."
    ;;
  Linux)
    echo "  • If installed via .deb: sudo apt remove dashd"
    echo "  • If installed via AppImage: delete the .AppImage file"
    echo "  • The ESP32 device's NVS preferences are independent — wipe"
    echo "    them via Settings → General → 'Reset device prefs' BEFORE"
    echo "    you uninstall, or flash blank firmware via PlatformIO."
    ;;
  *)
    warn "unsupported platform: $OS — no app-bundle removal hint"
    ;;
esac
