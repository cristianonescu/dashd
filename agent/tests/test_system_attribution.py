"""XPC-service attribution: anonymous Apple-Virtualization rows should be
rewritten to show the driving app (Claude / Docker / OrbStack / …).
"""
from __future__ import annotations

from dashd.collectors.system import (
    _attribute_name,
    _detect_vm_driver,
)


def test_attribute_plain_name_is_passthrough():
    assert _attribute_name("Google Chrome Helper", {"Google Chrome Helper"}) == "Google Chrome Helper"


def test_unrelated_xpc_not_touched():
    assert _attribute_name("com.apple.WebKit.GPU", set()) == "com.apple.WebKit.GPU"


def test_virtualization_with_no_driver_running_says_apple_vm():
    out = _attribute_name(
        "/System/.../com.apple.Virtualization.VirtualMachine",
        {"unrelated", "process", "list"},
    )
    assert out == "Apple VM"


def test_virtualization_with_claude_running():
    out = _attribute_name(
        "/System/.../com.apple.Virtualization.VirtualMachine",
        {"Claude Helper", "Google Chrome"},
    )
    assert out == "VM (Claude)"


def test_virtualization_with_docker_running():
    out = _attribute_name(
        "com.apple.Virtualization.VirtualMachine",
        {"Docker Desktop", "Finder"},
    )
    assert out == "VM (Docker)"


def test_virtualization_with_multiple_candidates():
    # Both Claude and OrbStack alive → we report both, separated by "+".
    out = _attribute_name(
        "com.apple.Virtualization.VirtualMachine",
        {"Claude Helper", "OrbStack"},
    )
    assert out == "VM (Claude+OrbStack)" or out == "VM (OrbStack+Claude)"


def test_detect_vm_driver_returns_none_when_quiet():
    assert _detect_vm_driver({"systemd", "kernel_task"}) is None


def test_detect_vm_driver_finds_one():
    assert _detect_vm_driver({"limactl"}) == "Lima"
