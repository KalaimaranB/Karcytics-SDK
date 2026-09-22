"""Unit tests for ui_daemon_runtime's app-identity helpers — the fix for
isolated plugin windows showing "python3"/"ui_daemon.py" and the generic
Python icon in macOS's native menu bar and Dock instead of the plugin's own
name/icon (see `run()`'s use of `patch_macos_bundle_name` and
`_resolve_app_icon_path`).
"""

import logging

from karcytics_sdk.plugin.ui_daemon_runtime import (
    _resolve_app_icon_path,
    patch_macos_bundle_name,
)

logger = logging.getLogger(__name__)


def test_resolve_app_icon_path_prefers_plugin_icon_when_it_exists(tmp_path, monkeypatch):
    plugin_icon = tmp_path / "plugin_icon.png"
    plugin_icon.write_bytes(b"not a real image, just needs to exist")
    core_icon = tmp_path / "core_logo.icns"
    core_icon.write_bytes(b"not a real image, just needs to exist")
    monkeypatch.setenv("KARCYTICS_CORE_ICON_PATH", str(core_icon))

    resolved = _resolve_app_icon_path(str(plugin_icon))

    assert resolved == plugin_icon


def test_resolve_app_icon_path_falls_back_to_core_icon_when_plugin_icon_missing(tmp_path, monkeypatch):
    core_icon = tmp_path / "core_logo.icns"
    core_icon.write_bytes(b"not a real image, just needs to exist")
    monkeypatch.setenv("KARCYTICS_CORE_ICON_PATH", str(core_icon))

    resolved = _resolve_app_icon_path(str(tmp_path / "does_not_exist.png"))

    assert resolved == core_icon


def test_resolve_app_icon_path_falls_back_to_core_icon_when_no_plugin_icon_given(tmp_path, monkeypatch):
    core_icon = tmp_path / "core_logo.icns"
    core_icon.write_bytes(b"not a real image, just needs to exist")
    monkeypatch.setenv("KARCYTICS_CORE_ICON_PATH", str(core_icon))

    resolved = _resolve_app_icon_path(None)

    assert resolved == core_icon


def test_resolve_app_icon_path_returns_none_when_nothing_resolves(tmp_path, monkeypatch):
    monkeypatch.delenv("KARCYTICS_CORE_ICON_PATH", raising=False)

    resolved = _resolve_app_icon_path(str(tmp_path / "does_not_exist.png"))

    assert resolved is None


def test_patch_macos_bundle_name_never_raises():
    """Best-effort by design — a pyobjc failure (or running on a non-macOS
    CI runner, where this is already a no-op) must never take the plugin
    process down over a cosmetic menu-bar title.
    """
    patch_macos_bundle_name("Flow Cytometry", logger)
