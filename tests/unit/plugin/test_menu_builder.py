"""Where StandardMenuBuilder puts Preferences on each platform.

Regression coverage for Windows/Linux showing Preferences under Edit:
QAction.MenuRole.PreferencesRole only relocates the action on macOS (into
the native app-name menu), so everywhere else the builder has to provide
that app-name menu itself.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMainWindow

from karcytics_sdk.plugin import menu_builder
from karcytics_sdk.plugin.menu_builder import StandardMenuBuilder


@pytest.fixture
def window(qapp):  # noqa: ARG001
    win = QMainWindow()
    yield win
    win.close()
    win.deleteLater()


def _titles(menu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _noop() -> None:
    pass


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_preferences_goes_in_leading_app_menu_off_macos(window, monkeypatch, platform):
    monkeypatch.setattr(menu_builder.sys, "platform", platform)
    builder = StandardMenuBuilder(window)
    builder.add_file_menu()
    edit = builder.add_edit_menu(undo_cb=_noop, redo_cb=_noop, pref_cb=_noop)

    assert builder.app_menu is not None
    assert builder.menubar.actions()[0] is builder.app_menu.menuAction()
    assert builder.app_menu.title() == "Karcytics"
    assert _titles(builder.app_menu) == ["Preferences..."]
    assert _titles(edit) == ["&Undo", "&Redo"]
    assert edit.menuAction().isVisible()


def test_app_menu_title_is_caller_supplied_off_macos(window, monkeypatch):
    monkeypatch.setattr(menu_builder.sys, "platform", "win32")
    builder = StandardMenuBuilder(window)
    builder.add_edit_menu(pref_cb=_noop, app_menu_title="Flow Cytometry")

    assert builder.app_menu is not None
    assert builder.app_menu.title() == "Flow Cytometry"


def test_preferences_only_edit_menu_is_hidden_off_macos(window, monkeypatch):
    monkeypatch.setattr(menu_builder.sys, "platform", "win32")
    builder = StandardMenuBuilder(window)
    edit = builder.add_edit_menu(pref_cb=_noop)

    assert not edit.menuAction().isVisible()

    # A later call that does add Edit items brings it back.
    builder.add_edit_menu(undo_cb=_noop)
    assert edit.menuAction().isVisible()


def test_preferences_stays_role_tagged_in_edit_on_macos(window, monkeypatch):
    monkeypatch.setattr(menu_builder.sys, "platform", "darwin")
    builder = StandardMenuBuilder(window)
    edit = builder.add_edit_menu(undo_cb=_noop, pref_cb=_noop)

    assert builder.app_menu is None
    assert _titles(edit) == ["&Undo", "Preferences..."]
