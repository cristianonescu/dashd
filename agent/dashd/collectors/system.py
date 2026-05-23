"""Cross-platform system metrics via psutil."""
from __future__ import annotations

import time
from typing import Any

import psutil

from dashd.collectors.base import Collector


# How many top-N entries to ship per category. 3 keeps the wire payload small;
# the firmware Tips page displays all 3.
TOP_N = 3

# Process names we never report — kernel-ish or always-on housekeeping that
# isn't actionable for the user.
_BORING_NAMES = {
    "kernel_task", "WindowServer", "loginwindow", "launchd",
    "systemd", "kthreadd", "swapper", "Idle",
}

# Apps known to drive Apple's Virtualization.framework. When the top RAM/CPU
# process is `com.apple.Virtualization.VirtualMachine` (an XPC service whose
# parent is launchd, so the name itself tells you nothing) we look for a
# likely driver in this list and re-label the row to e.g. "VM (Claude)".
_VM_DRIVERS: list[tuple[str, tuple[str, ...]]] = [
    ("Docker",    ("Docker Desktop", "com.docker.backend", "com.docker.virtualization")),
    ("OrbStack",  ("OrbStack", "OrbStack Helper")),
    ("Claude",    ("Claude Helper", "Claude.app")),
    ("UTM",       ("UTM",)),
    ("Tart",      ("tart",)),
    ("VMware",    ("vmware-vmx", "VMware Fusion")),
    ("Parallels", ("prl_disp_service", "Parallels Desktop")),
    ("Lima",      ("limactl",)),
    ("Colima",    ("colima",)),
]


def _detect_vm_driver(all_names: set[str]) -> str | None:
    """Best-effort: identify which app is currently driving the Apple
    Virtualization XPC. Returns the friendly name (e.g. "Claude"), a
    "+"-joined string if multiple candidates are running, or None when
    nothing matches."""
    found: list[str] = []
    for friendly, patterns in _VM_DRIVERS:
        if any(any(pat in n for n in all_names) for pat in patterns):
            found.append(friendly)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    return "+".join(found)


def _attribute_name(name: str, all_names: set[str]) -> str:
    """Rewrite anonymous XPC-service names into something the user can act on.
    Falls back to the raw name when we don't have a mapping."""
    if not name:
        return "?"
    if "com.apple.Virtualization.VirtualMachine" in name:
        drv = _detect_vm_driver(all_names)
        return f"VM ({drv})" if drv else "Apple VM"
    return name


# Generalized helper-pattern attribution. Many Electron-based apps spawn N
# anonymous "Foo Helper" processes; without aggregation the Tips list ends up
# showing 4 separate "Slack Helper" rows that each say almost nothing.
#
# Patterns are checked in order — first match wins.
_HELPER_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("Google Chrome",   ("Google Chrome Helper", "Google Chrome")),
    ("VS Code",         ("Code Helper", "Visual Studio Code")),
    ("Cursor",          ("Cursor Helper", "Cursor")),
    ("Slack",           ("Slack Helper", "Slack")),
    ("Discord",         ("Discord Helper", "Discord")),
    ("Microsoft Teams", ("Teams Helper", "Microsoft Teams")),
    ("Claude",          ("Claude Helper", "Claude")),
    ("Codex",           ("Codex Helper", "Codex")),
    ("dashd",           ("dashd Helper", "dashd")),
    # Generic fallback for any other Electron-backed helper we don't know.
    ("Electron app",    ("Electron Helper",)),
]


def _app_key(name: str) -> str:
    """Return the canonical app label this process belongs to.

    Pre-attributed names (already "VM (...)" or "Apple VM") pass through
    unchanged — they're their own key. Otherwise we walk the helper
    patterns; if nothing matches, the raw name becomes the key (so e.g.
    `python3` stays as `python3`).
    """
    if not name:
        return "?"
    if name.startswith("VM (") or name == "Apple VM":
        return name
    for app_label, patterns in _HELPER_PATTERNS:
        if any(pat in name for pat in patterns):
            return app_label
    return name


def _top_processes(n: int = TOP_N) -> tuple[list[dict], list[dict]]:
    """Return (top_by_cpu, top_by_ram) lists of process snapshots.

    Each entry is a small dict the firmware will render. Names are truncated
    so the on-device frame stays tight. XPC-service names are re-attributed
    via _attribute_name before truncation so the user sees the driving app.
    """
    total_ram = psutil.virtual_memory().total or 1
    snapshots: list[dict[str, Any]] = []
    # Collect names once so the attribution helper has the full picture.
    all_names: set[str] = set()
    raw: list[tuple[str, Any]] = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            n_ = p.info.get("name") or ""
            if n_:
                all_names.add(n_)
            if n_ in _BORING_NAMES:
                continue
            cpu = p.cpu_percent(interval=None)
            mem = p.info.get("memory_info")
            rss = mem.rss if mem else 0
            raw.append((n_ or "?", (cpu, rss)))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Aggregate per app: many Electron-style apps and Chrome spawn one main
    # process plus N "helpers". Showing 5 separate "Google Chrome Helper"
    # rows in a 3-row Tips list is mostly noise — the user wants to know
    # "Chrome is 4.3 GB across 5 procs". Sum RSS + CPU per canonical key.
    grouped: dict[str, dict[str, float | int]] = {}
    for name, (cpu, rss) in raw:
        attributed = _attribute_name(name, all_names)
        key = _app_key(attributed)
        g = grouped.setdefault(key, {"cpu": 0.0, "rss": 0, "procs": 0})
        g["cpu"] = float(g["cpu"]) + cpu
        g["rss"] = int(g["rss"]) + rss
        g["procs"] = int(g["procs"]) + 1

    for key, g in grouped.items():
        rss = int(g["rss"])
        snapshots.append({
            "name": key[:14],
            "cpu_pct": round(float(g["cpu"]), 1),
            "ram_pct": round(rss * 100 / total_ram, 1),
            "ram_mb": round(rss / (1024 * 1024)),
            "procs": int(g["procs"]),
        })

    by_cpu = sorted(snapshots, key=lambda s: s["cpu_pct"], reverse=True)[:n]
    by_ram = sorted(snapshots, key=lambda s: s["ram_pct"], reverse=True)[:n]
    return by_cpu, by_ram


class SystemCollector(Collector):
    key = "system"

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(enabled)
        # Seed counters so the first delta is meaningful.
        self._last_net = psutil.net_io_counters()
        self._last_t = time.monotonic()
        # Prime cpu_percent so the next call returns a real value, not 0.0.
        psutil.cpu_percent(percpu=True)
        # Prime per-process counters too — the first call returns 0 for every
        # process, so we burn one round at init time.
        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    async def collect(self) -> dict[str, Any] | None:
        now = time.monotonic()
        net = psutil.net_io_counters()
        dt = max(now - self._last_t, 1e-3)
        up_kbps = int((net.bytes_sent - self._last_net.bytes_sent) * 8 / 1000 / dt)
        down_kbps = int((net.bytes_recv - self._last_net.bytes_recv) * 8 / 1000 / dt)
        self._last_net = net
        self._last_t = now

        vm = psutil.virtual_memory()
        cpu = [round(x) for x in psutil.cpu_percent(percpu=True)]

        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        battery_pct: int | None = round(bat.percent) if bat else None
        battery_charging: bool | None = bool(bat.power_plugged) if bat else None

        temp_c: float | None = None
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
                for entries in temps.values():
                    if entries:
                        temp_c = round(entries[0].current, 1)
                        break
        except Exception:
            temp_c = None

        by_cpu, by_ram = _top_processes(TOP_N)

        # Real memory pressure = "active + wired" / total. macOS counts
        # inactive/cached file pages as "used" in vm.percent, which inflates
        # the number by 10–30 % on most machines. The wired+active sum is
        # what would actually have to be paged out under contention.
        ram_pressure_pct: int | None = None
        active = getattr(vm, "active", None)
        wired = getattr(vm, "wired", None)
        if active is not None and wired is not None and vm.total:
            ram_pressure_pct = round((active + wired) * 100 / vm.total)

        return {
            "cpu_pct": cpu,
            "ram_pct": round(vm.percent),
            "ram_pressure_pct": ram_pressure_pct,  # cache-excluded; may be None on Linux/Windows
            "ram_used_gb": round(vm.used / (1024**3), 1),
            "ram_total_gb": round(vm.total / (1024**3), 1),
            "disk_pct": round(psutil.disk_usage("/").percent),
            "net_up_kbps": up_kbps,
            "net_down_kbps": down_kbps,
            "battery_pct": battery_pct,
            "battery_charging": battery_charging,
            "temp_cpu_c": temp_c,
            "top_cpu": by_cpu,
            "top_ram": by_ram,
        }
