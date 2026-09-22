"""Unit tests for PluginBase.check_for_updates / show_toast.

Covers the three-way branch in `_on_update_check_result`: a toast always
appears for "update_available" and "up_to_date" (per product decision — the
toast should fire even when the plugin is already current, so the check
visibly ran), and never for "check_failed" (a broken network must not be
allowed to falsely claim "you're up to date").
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from unittest.mock import patch

from karcytics_sdk.plugin.base import PluginBase
from karcytics_sdk.plugin.state import PluginState
from karcytics_sdk.plugin.update_check import UpdateCheckResult


@dataclass
class _DummyState(PluginState):
    value: int = 0


class _DummyPlugin(PluginBase):
    def __init__(self):
        super().__init__(plugin_id="test_plugin")
        self.state = _DummyState()

    def get_state(self) -> PluginState:
        return self.state

    def set_state(self, state: PluginState) -> None:
        self.state = state


class TestOnUpdateCheckResult:
    def test_shows_an_update_toast_when_a_newer_version_exists(self, qapp):
        plugin = _DummyPlugin()
        with patch.object(plugin, "show_toast") as mock_show_toast:
            plugin._on_update_check_result(
                UpdateCheckResult(status="update_available", remote_version="9.9.9"),
                display_name="test_plugin",
            )

        mock_show_toast.assert_called_once()
        message = mock_show_toast.call_args.args[0]
        assert "9.9.9" in message
        assert "test_plugin" in message

    def test_shows_a_welcome_toast_when_already_up_to_date(self, qapp):
        """The toast must always appear, even when there's no update — a
        generic welcome confirming the installed version is current.
        """
        plugin = _DummyPlugin()
        with patch.object(plugin, "show_toast") as mock_show_toast:
            plugin._on_update_check_result(
                UpdateCheckResult(status="up_to_date", remote_version="1.0.0"),
                display_name="test_plugin",
            )

        mock_show_toast.assert_called_once()
        message = mock_show_toast.call_args.args[0]
        assert "1.0.0" in message
        assert "test_plugin" in message

    def test_stays_silent_when_the_check_itself_failed(self, qapp):
        """Never show a toast off a failed check — that would risk telling a
        user they're up to date when the check couldn't confirm anything.
        """
        plugin = _DummyPlugin()
        with patch.object(plugin, "show_toast") as mock_show_toast:
            plugin._on_update_check_result(UpdateCheckResult(status="check_failed"), display_name="test_plugin")

        mock_show_toast.assert_not_called()

    def test_update_and_up_to_date_toasts_use_different_styles(self, qapp):
        plugin = _DummyPlugin()

        with patch.object(plugin, "show_toast") as mock_show_toast:
            plugin._on_update_check_result(
                UpdateCheckResult(status="update_available", remote_version="9.9.9"),
                display_name="test_plugin",
            )
        update_kwargs = mock_show_toast.call_args.kwargs

        with patch.object(plugin, "show_toast") as mock_show_toast:
            plugin._on_update_check_result(
                UpdateCheckResult(status="up_to_date", remote_version="1.0.0"),
                display_name="test_plugin",
            )
        up_to_date_kwargs = mock_show_toast.call_args.kwargs

        assert update_kwargs["icon"] != up_to_date_kwargs["icon"]
        assert update_kwargs["color"] != up_to_date_kwargs["color"]

    def test_uses_the_given_display_name_instead_of_the_plugin_id(self, qapp):
        plugin = _DummyPlugin()
        with patch.object(plugin, "show_toast") as mock_show_toast:
            plugin._on_update_check_result(
                UpdateCheckResult(status="up_to_date", remote_version="1.0.0"),
                display_name="Flow Cytometry",
            )

        message = mock_show_toast.call_args.args[0]
        assert "Flow Cytometry" in message
        assert "test_plugin" not in message


class TestCheckForUpdates:
    def test_skips_the_check_when_installed_version_cannot_be_resolved(self, qapp):
        plugin = _DummyPlugin()
        with patch("karcytics_sdk.plugin.update_check.resolve_installed_version", return_value=None):
            with patch("karcytics_sdk.plugin.base._UpdateCheckWorker") as mock_worker_cls:
                plugin.check_for_updates(repo_url="https://github.com/example/example")

        mock_worker_cls.assert_not_called()

    def test_starts_a_background_worker_with_the_resolved_version(self, qapp):
        """Covers the real-world case that motivated resolve_installed_version:
        an isolated plugin loaded via a sys.path insert (not pip-installed),
        so plain importlib.metadata alone would find nothing.
        """
        plugin = _DummyPlugin()
        with patch("karcytics_sdk.plugin.update_check.resolve_installed_version", return_value="1.2.3") as mock_resolve:
            with patch("karcytics_sdk.plugin.base._UpdateCheckWorker") as mock_worker_cls:
                plugin.check_for_updates(repo_url="https://github.com/example/example")

        mock_resolve.assert_called_once_with("test_plugin", plugin)
        mock_worker_cls.assert_called_once_with("1.2.3", "https://github.com/example/example", plugin)
        mock_worker_cls.return_value.start.assert_called_once()

    def test_an_explicit_current_version_skips_version_resolution(self, qapp):
        plugin = _DummyPlugin()
        with patch("karcytics_sdk.plugin.update_check.resolve_installed_version") as mock_resolve:
            with patch("karcytics_sdk.plugin.base._UpdateCheckWorker") as mock_worker_cls:
                plugin.check_for_updates(repo_url="https://github.com/example/example", current_version="0.0.1")

        mock_resolve.assert_not_called()
        mock_worker_cls.assert_called_once_with("0.0.1", "https://github.com/example/example", plugin)

    @staticmethod
    def _connected_display_name(plugin, mock_worker_cls) -> str:
        """Pull the `display_name` bound into the `finished_ok` slot's partial."""
        connected = mock_worker_cls.return_value.finished_ok.connect.call_args.args[0]
        assert isinstance(connected, partial)
        assert connected.func == plugin._on_update_check_result
        return connected.keywords["display_name"]

    def test_defaults_the_toast_display_name_to_the_plugin_id(self, qapp):
        plugin = _DummyPlugin()
        with patch("karcytics_sdk.plugin.update_check.resolve_installed_version", return_value="1.0.0"):
            with patch("karcytics_sdk.plugin.base._UpdateCheckWorker") as mock_worker_cls:
                plugin.check_for_updates(repo_url="https://github.com/example/example")

        assert self._connected_display_name(plugin, mock_worker_cls) == "test_plugin"

    def test_uses_an_explicit_display_name_over_the_plugin_id(self, qapp):
        plugin = _DummyPlugin()
        with patch("karcytics_sdk.plugin.update_check.resolve_installed_version", return_value="1.0.0"):
            with patch("karcytics_sdk.plugin.base._UpdateCheckWorker") as mock_worker_cls:
                plugin.check_for_updates(
                    repo_url="https://github.com/example/example",
                    display_name="Flow Cytometry",
                )

        assert self._connected_display_name(plugin, mock_worker_cls) == "Flow Cytometry"
