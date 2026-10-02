import sys
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from karcytics_sdk.plugin.base import PluginBase
from karcytics_sdk.plugin.state import PluginState


@dataclass
class MockState(PluginState):
    threshold: float = 0.5
    filter_type: str = "Gaussian"
    heavy_data: list = None


class MockPlugin(PluginBase):
    def __init__(self, plugin_id: str, parent=None):
        super().__init__(plugin_id, parent)
        self.state = MockState()
        self.heavy_asset = [9] * 1000

    def get_state(self) -> MockState:
        return self.state

    def set_state(self, state: PluginState) -> None:
        assert isinstance(state, MockState)
        self.state = state


def test_plugin_state_serialization():
    """Test PluginState to_dict and from_dict serialization methods."""
    state = MockState(threshold=0.8, filter_type="Median")
    state_dict = state.to_dict()

    assert state_dict == {"threshold": 0.8, "filter_type": "Median", "heavy_data": None}

    new_state = MockState.from_dict(state_dict)
    assert isinstance(new_state, MockState)
    assert new_state.threshold == 0.8
    assert new_state.filter_type == "Median"


def test_plugin_base_properties():
    """Test basic properties and protocols of PluginBase."""
    plugin = MockPlugin(plugin_id="my_test_plugin")

    assert plugin.plugin_id == "my_test_plugin"
    assert plugin.__plugin_id__ == "my_test_plugin"
    assert plugin.__version__ == "1.0.0"
    assert plugin.get_panel_class() == MockPlugin


def test_plugin_signal_proxying():
    """Verify that PluginBase proxies missing attributes to self.signals."""
    plugin = MockPlugin(plugin_id="test_proxy")

    # Exists on PluginSignals
    assert plugin.state_changed == plugin.signals.state_changed
    assert plugin.status_message == plugin.signals.status_message

    # Missing attribute entirely
    with pytest.raises(AttributeError):
        _ = plugin.non_existent_attribute


def test_plugin_event_bus():
    """Verify that PluginBase can publish and subscribe to the CentralEventBus."""
    plugin = MockPlugin(plugin_id="test_bus")
    called_data = []

    def callback(data):
        called_data.append(data)

    plugin.subscribe_event("test.topic", callback)
    plugin.publish_event("test.topic", "hello_world")

    assert called_data == ["hello_world"]


def test_plugin_undo_redo_history():
    """push_state records real, restorable steps in the plugin's own history."""
    plugin = MockPlugin(plugin_id="test_history")
    spy = MagicMock()
    plugin.state_changed.connect(spy)

    assert plugin.can_undo() is False
    plugin.push_state()  # baseline
    plugin.state.threshold = 0.95
    plugin.push_state("Change threshold")

    assert plugin.can_undo() is True
    assert plugin.can_redo() is False
    assert plugin.undo_text() == "Undo Change threshold"

    assert plugin.undo() is True
    assert plugin.state.threshold == 0.5
    assert plugin.can_redo() is True
    assert plugin.redo_text() == "Redo Change threshold"

    assert plugin.redo() is True
    assert plugin.state.threshold == 0.95
    assert plugin.redo() is False
    assert spy.call_count == 4  # two pushes, one undo, one redo


def test_plugin_undo_restore_failure_keeps_history_consistent():
    """A snapshot that fails to restore must not become the 'current' step."""
    plugin = MockPlugin(plugin_id="test_history_fail")
    plugin.push_state()
    plugin.state.threshold = 0.95
    plugin.push_state("Change threshold")

    def _boom(_snapshot):
        raise RuntimeError("restore failed")

    plugin.bind_undo_history(plugin.undo_history, _boom)
    assert plugin.undo() is False
    assert plugin.can_undo() is True
    assert plugin.can_redo() is False
    assert plugin.undo_text() == "Undo Change threshold"


def test_plugin_undo_signals_follow_history():
    plugin = MockPlugin(plugin_id="test_history_signals")
    undo_spy, redo_spy, changed_spy = MagicMock(), MagicMock(), MagicMock()
    plugin.undo_available.connect(undo_spy)
    plugin.redo_available.connect(redo_spy)
    plugin.undo_state_changed.connect(changed_spy)

    plugin.push_state()
    plugin.state.threshold = 0.7
    plugin.push_state("Edit")
    assert undo_spy.call_args.args == (True,)
    plugin.undo()
    assert undo_spy.call_args.args == (False,)
    assert redo_spy.call_args.args == (True,)
    assert changed_spy.call_count >= 3


@patch("karcytics_sdk.plugin.base.theme_manager")
def test_theme_propagation(mock_theme_manager):
    """Verify applying base stylesheets propagates styles correctly."""
    plugin = MockPlugin(plugin_id="test_theme")
    plugin._apply_theme_styles()

    # Style sheet must contain color tags from Colors mock
    assert "background:" in plugin.styleSheet()


def test_cleanup_and_destructor_raii():
    """Verify cleanup breaks references to heavy assets for prompt GC collection."""
    plugin = MockPlugin(plugin_id="test_cleanup")

    # Set up heavy resources in state and instance
    plugin.state.heavy_data = [99] * 500

    mock_inspector = MagicMock()
    mock_inspector.get_heavy_resources.side_effect = [
        [("heavy_data", plugin.state.heavy_data)],  # attributes of State
        [("heavy_asset", plugin.heavy_asset)],  # attributes of Instance
    ]

    with patch.dict(sys.modules, {"karcytics.core.resource_inspector": MagicMock(ResourceInspector=mock_inspector)}):
        # Trigger cleanup
        plugin.cleanup()

        # Assets must be pruned to None
        assert plugin.state.heavy_data is None
        assert plugin.heavy_asset is None
        assert plugin.state.threshold == 0.5  # Non-heavy values are preserved


def test_widget_close_event():
    """Verify close widget event triggers automated RAII cleanup."""
    plugin = MockPlugin(plugin_id="test_close")

    # Spy on cleanup
    plugin.cleanup = MagicMock()

    # Create real QCloseEvent
    from PyQt6.QtGui import QCloseEvent

    event = QCloseEvent()

    plugin.closeEvent(event)

    plugin.cleanup.assert_called_once()
