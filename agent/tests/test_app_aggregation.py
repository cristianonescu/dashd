"""App-key resolution + helper aggregation."""
from __future__ import annotations

from dashd.collectors.system import _app_key


def test_app_key_passthrough_for_plain_processes():
    assert _app_key("python3") == "python3"
    assert _app_key("kernel_task") == "kernel_task"


def test_app_key_collapses_chrome_helpers():
    assert _app_key("Google Chrome Helper") == "Google Chrome"
    assert _app_key("Google Chrome Helper (Renderer)") == "Google Chrome"
    assert _app_key("Google Chrome") == "Google Chrome"


def test_app_key_collapses_slack_helpers():
    assert _app_key("Slack Helper") == "Slack"
    assert _app_key("Slack Helper (Renderer)") == "Slack"
    assert _app_key("Slack") == "Slack"


def test_app_key_collapses_vs_code_and_cursor():
    assert _app_key("Code Helper") == "VS Code"
    assert _app_key("Code Helper (Renderer)") == "VS Code"
    assert _app_key("Cursor Helper") == "Cursor"


def test_app_key_preserves_attributed_vm():
    assert _app_key("VM (Claude)") == "VM (Claude)"
    assert _app_key("Apple VM") == "Apple VM"


def test_app_key_unknown_electron_helper():
    assert _app_key("Electron Helper") == "Electron app"
    assert _app_key("Electron Helper (Renderer)") == "Electron app"


def test_app_key_empty_string():
    assert _app_key("") == "?"
