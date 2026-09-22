import os
import sys

import pytest

# Force Qt to render offscreen (requires no visible monitor display)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Global QApplication instance to prevent GC cleanup mid-session
_qapp = None


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Ensure a global QApplication instance is initialized before executing PyQt6 tests."""
    global _qapp
    from PyQt6.QtWidgets import QApplication

    _qapp = QApplication.instance()
    if _qapp is None:
        _qapp = QApplication(sys.argv)

    yield _qapp


@pytest.fixture(autouse=True)
def _reset_plugin_config_registry():
    """PluginConfig memoizes one instance per plugin_id per process (see
    PluginConfig.__new__), so any two call sites asking for the same
    plugin_id's config always share one in-memory copy instead of risking
    divergent, overwrite-each-other snapshots. Without a reset, a test that
    reuses a plugin_id under a different fake home (`patch("pathlib.Path.home",
    ...)`) than an earlier test would get that earlier test's cached instance
    back instead of loading fresh — reset before/after every test so each one
    behaves like a fresh process.
    """
    from karcytics_sdk.plugin.io import PluginConfig

    PluginConfig._instances.clear()
    yield
    PluginConfig._instances.clear()
