"""Edit -> Undo/Redo in an isolated plugin's own window.

Regression coverage for isolated plugins having no working undo at all: the
Hub's Edit menu can't reach a panel living in another process, and the
isolated window's own menu bar had no Undo/Redo items or shortcuts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QLineEdit, QMainWindow, QVBoxLayout, QWidget

from karcytics_sdk.plugin import ui_daemon_runtime as rt
from karcytics_sdk.plugin.base import PluginBase
from karcytics_sdk.plugin.state import PluginState

_LOG = logging.getLogger(__name__)


@dataclass
class _State(PluginState):
    value: int = 0


class _Panel(PluginBase):
    def __init__(self) -> None:
        super().__init__("undo_menu_test")
        self.state = _State()
        self.line_edit = QLineEdit(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.line_edit)

    def get_state(self) -> _State:
        return self.state

    def set_state(self, state: PluginState) -> None:
        assert isinstance(state, _State)
        self.state = state


@pytest.fixture
def window_and_panel(qapp, monkeypatch):  # noqa: ARG001
    monkeypatch.delenv("KARCYTICS_CORE_SERVICES_PORT", raising=False)
    monkeypatch.delenv("KARCYTICS_CORE_SERVICES_TOKEN", raising=False)
    window = QMainWindow()
    rt._build_menu_bar(window, _LOG)
    panel = _Panel()
    window.setCentralWidget(panel)
    rt._wire_undo_menu(window, panel, _LOG)
    yield window, panel
    window.close()
    window.deleteLater()


def _edit(panel: _Panel, value: int, label: str) -> None:
    panel.state.value = value
    panel.push_state(label)


def test_menu_has_disabled_undo_redo_before_any_panel(qapp, monkeypatch):  # noqa: ARG001
    monkeypatch.delenv("KARCYTICS_CORE_SERVICES_PORT", raising=False)
    window = QMainWindow()
    rt._build_menu_bar(window, _LOG)
    assert window._undo_action is not None
    assert not window._undo_action.isEnabled()
    assert not window._redo_action.isEnabled()


def test_actions_use_every_platform_binding(window_and_panel):
    window, _panel = window_and_panel
    assert window._undo_action.shortcuts() == QKeySequence.keyBindings(QKeySequence.StandardKey.Undo)
    assert window._redo_action.shortcuts() == QKeySequence.keyBindings(QKeySequence.StandardKey.Redo)


def test_actions_track_panel_history(window_and_panel):
    window, panel = window_and_panel
    panel.push_state()  # baseline
    assert not window._undo_action.isEnabled()

    _edit(panel, 1, "Set Value")
    assert window._undo_action.isEnabled()
    assert window._undo_action.text() == "Undo Set Value"
    assert not window._redo_action.isEnabled()

    window._undo_action.trigger()
    assert panel.state.value == 0
    assert not window._undo_action.isEnabled()
    assert window._redo_action.isEnabled()
    assert window._redo_action.text() == "Redo Set Value"

    window._redo_action.trigger()
    assert panel.state.value == 1


def test_focused_text_field_gets_its_own_undo(window_and_panel, qapp):
    window, panel = window_and_panel
    panel.push_state()
    _edit(panel, 5, "Set Value")

    window.show()
    panel.line_edit.setFocus()
    qapp.processEvents()
    panel.line_edit.insert("abc")
    if qapp.focusWidget() is not panel.line_edit:
        pytest.skip("platform did not grant focus to the line edit offscreen")

    window._undo_action.trigger()
    assert panel.line_edit.text() == ""  # the typing was undone...
    assert panel.state.value == 5  # ...not the workspace step


def test_panel_without_undo_api_is_left_unwired(qapp, monkeypatch):  # noqa: ARG001
    monkeypatch.delenv("KARCYTICS_CORE_SERVICES_PORT", raising=False)
    window = QMainWindow()
    rt._build_menu_bar(window, _LOG)
    rt._wire_undo_menu(window, QWidget(), _LOG)
    assert getattr(window, "_undo_target", None) is None
    window._undo_action.trigger()  # must not raise
