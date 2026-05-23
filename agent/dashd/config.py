"""Config loader: TOML on disk → typed pydantic models."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SerialConfig(BaseModel):
    port: str | None = None
    baud: int = 460800
    # USB VID:PID for ESP32-C3 native USB Serial/JTAG.
    vid: int = 0x303A
    pid: int = 0x1001


class UpdateConfig(BaseModel):
    interval_seconds: float = 2.0
    # Slower tick used when nobody is consuming state — device disconnected
    # AND no IPC client is signalling an active window. Keeps the
    # suggestion + tray-icon status fresh enough to be useful without
    # paying the per-tick collector cost. Set <= interval_seconds to
    # disable the idle throttle (always tick at the fast rate).
    idle_interval_seconds: float = 30.0


class TransportConfig(BaseModel):
    # Which device transport to use:
    #   "cable"     — USB-CDC only
    #   "bluetooth" — BLE only
    #   "auto"      — USB when a dashd device is enumerated, else BLE
    # Overridable with the DASHD_TRANSPORT env var. The Electron app sets
    # this via that env var when it spawns the agent.
    mode: str = "auto"


class UserConfig(BaseModel):
    email: str = ""
    timezone: str = "UTC"


class CollectorToggle(BaseModel):
    enabled: bool = False
    # Allow arbitrary extra fields per-collector without schema churn.
    model_config = {"extra": "allow"}


class CollectorsConfig(BaseModel):
    system: CollectorToggle = Field(default_factory=lambda: CollectorToggle(enabled=True))
    # GPU collector — cross-platform best-effort. Returns
    # `{available: false, reason: "..."}` on systems with no detectable
    # GPU (or no driver), so it's safe to leave on. Disable here to
    # skip the per-tick subprocess cost.
    gpu: CollectorToggle = Field(default_factory=lambda: CollectorToggle(enabled=True))
    claude_code: CollectorToggle = Field(default_factory=CollectorToggle)
    codex: CollectorToggle = Field(default_factory=CollectorToggle)
    git: CollectorToggle = Field(default_factory=CollectorToggle)
    github: CollectorToggle = Field(default_factory=CollectorToggle)
    calendar: CollectorToggle = Field(default_factory=CollectorToggle)
    slack: CollectorToggle = Field(default_factory=CollectorToggle)
    email: CollectorToggle = Field(default_factory=CollectorToggle)
    teams: CollectorToggle = Field(default_factory=CollectorToggle)
    imessage: CollectorToggle = Field(default_factory=CollectorToggle)
    whatsapp: CollectorToggle = Field(default_factory=CollectorToggle)
    # Opt-in Anthropic OAuth usage API (Session/Weekly/Sonnet/Extra
    # gauges matching Claude.ai exactly). Default off — the user must
    # explicitly opt in via Settings or DASHD_ANTHROPIC_OAUTH=1.
    anthropic_oauth: CollectorToggle = Field(default_factory=CollectorToggle)


class Config(BaseModel):
    serial: SerialConfig = Field(default_factory=SerialConfig)
    transport: TransportConfig = Field(default_factory=TransportConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    user: UserConfig = Field(default_factory=UserConfig)
    collectors: CollectorsConfig = Field(default_factory=CollectorsConfig)


DEFAULT_CONFIG_PATHS = [
    Path.home() / ".config" / "dashd" / "config.toml",
    Path.cwd() / "config.toml",
]


def load_config(path: Path | None = None) -> Config:
    """Load config from `path`, the first default that exists, or built-in defaults."""
    candidates = [path] if path else DEFAULT_CONFIG_PATHS
    for p in candidates:
        if p and p.is_file():
            with p.open("rb") as f:
                data: dict[str, Any] = tomllib.load(f)
            return Config.model_validate(data)
    return Config()


def env_override_config(cfg: Config) -> Config:
    """Apply DASHD_* env vars that override config values."""
    port = os.environ.get("DASHD_SERIAL_PORT")
    if port:
        cfg.serial.port = port
    mode = os.environ.get("DASHD_TRANSPORT")
    if mode in ("cable", "bluetooth", "auto"):
        cfg.transport.mode = mode
    return cfg
