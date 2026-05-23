"""BLE pairing trust store (host side).

dashd's BLE pairing is app-level, not BLE SMP bonding (Codex review): on
first pair the device shows a 6-digit code, the agent writes it to an auth
characteristic, and on success the device mints a per-host **trust token**
and returns it. The agent persists that token here, keyed by the device's
BLE address; on every later connect it presents the token instead of the
code, and the device skips the on-screen pairing step.

The file holds secrets, so it's written 0600 — same posture as ipc.token.
A missing/corrupt file simply means "nothing trusted yet".
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("dashd.ble.trust")

DEFAULT_PATH = Path.home() / ".config" / "dashd" / "ble_trust.json"


class TrustStore:
    """Persistent map of BLE device address → trust token."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = path
        self._tokens: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
            if isinstance(data, dict):
                # Keep only string→string entries — ignore anything else.
                self._tokens = {str(k): str(v) for k, v in data.items()
                                if isinstance(v, str)}
        except (OSError, json.JSONDecodeError):
            self._tokens = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tokens, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    # ---- API ---------------------------------------------------------------

    def token_for(self, address: str) -> str | None:
        """The trust token for `address`, or None if this host isn't paired
        with that device."""
        return self._tokens.get(address)

    def is_trusted(self, address: str) -> bool:
        return address in self._tokens

    def remember(self, address: str, token: str) -> None:
        """Record a freshly-minted trust token after a successful pairing."""
        self._tokens[address] = token
        self._save()
        log.info("ble: trusted device %s", address)

    def forget(self, address: str) -> bool:
        """Drop a device's trust. Returns True if it was trusted."""
        if address in self._tokens:
            del self._tokens[address]
            self._save()
            log.info("ble: forgot device %s", address)
            return True
        return False

    def forget_all(self) -> None:
        """Clear every trusted device (factory-reset of pairings)."""
        self._tokens.clear()
        self._save()

    def addresses(self) -> list[str]:
        return list(self._tokens)
