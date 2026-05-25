# dashd uninstaller — Windows.
#
# Removes the host-side state dashd leaves behind. The NSIS installer's
# `unins000.exe` (in the install dir) handles the app bundle itself plus
# the Start-menu / Desktop shortcuts; this script cleans up the parts
# NSIS doesn't touch:
#
#   - The running agent (best-effort)
#   - Autostart registry entry under HKCU
#   - %APPDATA%\dashd  (user config + cached state)
#   - %LOCALAPPDATA%\dashd  (electron-updater cache)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -KeepConfig
#
# After the script finishes, run the NSIS uninstaller (Settings → Apps →
# dashd → Uninstall, or `unins000.exe` in the install dir) to remove
# the app bundle. If you flip the order the NSIS uninstaller still
# works, it just leaves the orphaned config dirs behind.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$KeepConfig
)

$ErrorActionPreference = 'Continue'

function Say  ($msg) { Write-Host "▸ $msg" -ForegroundColor Cyan }
function Ok   ($msg) { Write-Host "✓ $msg" -ForegroundColor Green }
function Warn ($msg) { Write-Host "! $msg" -ForegroundColor Yellow }

function Remove-Path([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    if ($DryRun) {
        Warn "would remove: $Path"
    } else {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            Ok "removed: $Path"
        } catch {
            Warn "could not remove $Path : $($_.Exception.Message)"
        }
    }
}

# ── 1) stop running agent ────────────────────────────────────────────────────
Say "stopping any running dashd processes…"
$procs = @(Get-Process -Name dashd-agent, dashd_agent, dashd -ErrorAction SilentlyContinue)
if ($procs.Count -gt 0) {
    if ($DryRun) {
        $procs | ForEach-Object { Write-Host "  $($_.Id) $($_.ProcessName)" }
        Warn "would terminate the processes above"
    } else {
        $procs | ForEach-Object {
            try { $_.Kill(); $_.WaitForExit(3000) | Out-Null } catch { }
        }
        Ok "terminated $($procs.Count) process(es)"
    }
} else {
    Ok "no dashd processes running"
}

# ── 2) autostart registry entry ─────────────────────────────────────────────
Say "removing autostart registry entry…"
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$value  = 'dashd'
if (Get-ItemProperty -Path $runKey -Name $value -ErrorAction SilentlyContinue) {
    if ($DryRun) {
        Warn "would delete: $runKey\$value"
    } else {
        Remove-ItemProperty -Path $runKey -Name $value -ErrorAction SilentlyContinue
        Ok "removed: $runKey\$value"
    }
} else {
    Ok "no autostart entry to remove"
}

# ── 3) user config + cached state ───────────────────────────────────────────
$cfgDir = Join-Path $env:APPDATA 'dashd'
if ($KeepConfig) {
    Warn "-KeepConfig supplied; leaving $cfgDir in place"
} else {
    Say "removing user config + cached state…"
    Remove-Path $cfgDir
}

# ── 4) electron-updater cache ───────────────────────────────────────────────
Say "removing electron-updater cache…"
Remove-Path (Join-Path $env:LOCALAPPDATA 'dashd')
Remove-Path (Join-Path $env:LOCALAPPDATA 'dashd-updater')

# ── 5) summary + remaining manual steps ─────────────────────────────────────
Write-Host ""
if ($DryRun) {
    Warn "dry run complete — re-run without -DryRun to actually delete"
} else {
    Ok "dashd host state removed"
}
Write-Host ""
Write-Host "Remaining manual step:"
Write-Host "  • Run the NSIS uninstaller — Settings → Apps → dashd → Uninstall"
Write-Host "    (or run unins000.exe in the install directory)."
Write-Host "  • The ESP32 device's NVS preferences are independent — wipe them"
Write-Host "    via Settings → General → 'Reset device prefs' BEFORE you"
Write-Host "    uninstall, or reflash blank firmware via PlatformIO."
