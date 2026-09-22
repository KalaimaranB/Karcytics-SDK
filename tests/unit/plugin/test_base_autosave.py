"""Unit tests for PluginBase.setup_workflow_autosave / populate_preferences.

Covers the opt-in hook every plugin uses to get the SDK's shared workflow
autosave loop (see karcytics_sdk.plugin.autosave.WorkflowAutosaveController)
and the automatic "Workspace" preferences page it contributes once a plugin
has opted in.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from karcytics_sdk.plugin.base import PluginBase
from karcytics_sdk.plugin.io import PluginConfig
from karcytics_sdk.plugin.state import PluginState
from karcytics_sdk.plugin.ui_preferences import AutosaveWorkflowsPreferencesPage, SDKPreferencesDialog


@dataclass
class _DummyState(PluginState):
    value: int = 0


class _DummyPlugin(PluginBase):
    def __init__(self, plugin_id: str = "autosave_base_test_plugin"):
        super().__init__(plugin_id=plugin_id)
        self.state = _DummyState()

    def get_state(self) -> PluginState:
        return self.state

    def set_state(self, state: PluginState) -> None:
        self.state = state


class TestSetupWorkflowAutosave:
    def test_returns_a_started_controller(self, qapp, tmp_path):  # noqa: ARG002
        with patch("pathlib.Path.home", return_value=tmp_path):
            plugin = _DummyPlugin()
            controller = plugin.setup_workflow_autosave(
                has_saved_once=lambda: True,
                save=lambda on_done: on_done(True),
            )

        assert controller is plugin._workflow_autosave_controller
        assert controller._timer.isActive() is True

    def test_cleanup_stops_the_controller(self, qapp, tmp_path):  # noqa: ARG002
        with patch("pathlib.Path.home", return_value=tmp_path):
            plugin = _DummyPlugin()
            controller = plugin.setup_workflow_autosave(
                has_saved_once=lambda: True,
                save=lambda on_done: on_done(True),
            )

            plugin.cleanup()

        assert controller._timer.isActive() is False


class TestPopulatePreferences:
    def test_no_op_when_autosave_was_never_set_up(self, qapp, tmp_path):  # noqa: ARG002
        with patch("pathlib.Path.home", return_value=tmp_path):
            plugin = _DummyPlugin()
            dialog = SDKPreferencesDialog(client=MagicMock())

            plugin.populate_preferences(dialog)

        assert dialog.nav_list.count() == 0

    def test_adds_a_workspace_page_once_autosave_is_set_up(self, qapp, tmp_path):  # noqa: ARG002
        with patch("pathlib.Path.home", return_value=tmp_path):
            plugin = _DummyPlugin()
            plugin.setup_workflow_autosave(
                has_saved_once=lambda: True,
                save=lambda on_done: on_done(True),
            )
            dialog = SDKPreferencesDialog(client=MagicMock())

            plugin.populate_preferences(dialog)

        assert dialog.nav_list.count() == 1
        assert dialog.nav_list.item(0).text() == "Workspace"
        assert isinstance(dialog.stack.widget(0), AutosaveWorkflowsPreferencesPage)


class TestPluginConfigSharing:
    def test_autosave_preference_shares_storage_with_the_plugins_own_config(self, qapp, tmp_path):  # noqa: ARG002
        """A plugin's own PluginConfig(plugin_id) and the one the autosave
        controller builds for the same plugin_id must be the exact same
        object (see PluginConfig.__new__) — otherwise one's .save() can
        silently drop the other's unsaved in-memory changes.
        """
        with patch("pathlib.Path.home", return_value=tmp_path):
            plugin = _DummyPlugin()
            own_config = PluginConfig(plugin.plugin_id)
            own_config.set("some_other_setting", "keep me")

            controller = plugin.setup_workflow_autosave(
                has_saved_once=lambda: True,
                save=lambda on_done: on_done(True),
            )
            controller.set_enabled(True)

        assert own_config.get("some_other_setting") == "keep me"
