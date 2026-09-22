"""Unit tests for the SDK's shared Preferences dialog and its default
"Theme" page — regression coverage for a bug where every isolated plugin's
Preferences dialog opened completely blank (SDKPreferencesDialog is just an
empty nav-list/stack shell; nothing populated a default page, and no plugin
implemented the optional `populate_preferences()` hook), unlike the Hub's
own in-process preferences dialog which always has a working Theme page.
"""

from unittest.mock import MagicMock

from karcytics_sdk.plugin.ui_preferences import (
    AutosaveWorkflowsPreferencesPage,
    SDKPreferencesDialog,
    SDKThemePreferencesPage,
)


def _fake_client(themes=None, current_name=None, switch_error=None):
    client = MagicMock()

    def _call(method, **kwargs):
        if method == "theme.list_categorized_themes":
            return themes if themes is not None else {}
        if method == "theme.get_current_theme_name":
            return {"name": current_name}
        if method == "theme.switch_theme":
            if switch_error is not None:
                raise switch_error
            return {"status": "ok"}
        raise AssertionError(f"unexpected method: {method}")

    client.call.side_effect = _call
    return client


def test_theme_page_lists_every_theme_from_every_category(qapp):  # noqa: ARG001
    themes = {
        "Dark": [["Karcytics Default", "/themes/default.json"]],
        "Light": [["Daylight", "/themes/daylight.json"]],
    }
    client = _fake_client(themes=themes)

    page = SDKThemePreferencesPage(client)

    assert len(page.button_group.buttons()) == 2
    labels = {b.text() for b in page.button_group.buttons()}
    assert labels == {"Karcytics Default", "Daylight"}


def test_theme_page_preselects_the_hubs_current_theme(qapp):  # noqa: ARG001
    themes = {"Dark": [["Karcytics Default", "/themes/default.json"], ["Nebula", "/themes/nebula.json"]]}
    client = _fake_client(themes=themes, current_name="Nebula")

    page = SDKThemePreferencesPage(client)

    checked = [b for b in page.button_group.buttons() if b.isChecked()]
    assert [b.text() for b in checked] == ["Nebula"]


def test_theme_page_switch_calls_the_real_handler_contract(qapp):  # noqa: ARG001
    """Regression test: this used to call client.call("theme.switch_theme",
    {"theme_path": ...}) — a positional dict CoreServicesClient.call() (which
    only accepts **kwargs) can't accept, and a key name
    ("theme_path") the Hub's handler (which reads kwargs["path"]) never
    matched anyway. Selecting a theme must reach the Hub as `path=<str>`.
    """
    themes = {"Dark": [["Karcytics Default", "/themes/default.json"]]}
    client = _fake_client(themes=themes)

    page = SDKThemePreferencesPage(client)
    radio = page.button_group.buttons()[0]
    radio.setChecked(True)

    client.call.assert_any_call("theme.switch_theme", path="/themes/default.json")


def test_theme_page_reports_switch_failure_without_crashing(qapp):  # noqa: ARG001
    themes = {"Dark": [["Karcytics Default", "/themes/default.json"]]}
    client = _fake_client(themes=themes, switch_error=RuntimeError("hub unreachable"))

    page = SDKThemePreferencesPage(client)
    radio = page.button_group.buttons()[0]
    radio.setChecked(True)

    assert "hub unreachable" in page._status_label.text()


def test_theme_page_degrades_gracefully_when_hub_unreachable(qapp):  # noqa: ARG001
    client = MagicMock()
    client.call.side_effect = RuntimeError("connection refused")

    page = SDKThemePreferencesPage(client)

    assert page.button_group.buttons() == []
    assert "connection refused" in page._status_label.text()


def test_preferences_dialog_add_page_selects_first_page_by_default(qapp):  # noqa: ARG001
    from PyQt6.QtWidgets import QWidget

    dialog = SDKPreferencesDialog(client=MagicMock())
    dialog.add_page("Theme", QWidget())

    assert dialog.nav_list.count() == 1
    assert dialog.nav_list.currentRow() == 0


def test_preferences_dialog_is_styled_like_the_hubs_own_dialog(qapp):  # noqa: ARG001
    """Regression guard: SDKPreferencesDialog used to have no stylesheet at
    all, so an isolated plugin's Preferences dialog looked visibly different
    (unstyled) from the Hub's own in-process PreferencesDialog.
    """
    dialog = SDKPreferencesDialog(client=MagicMock())

    assert dialog.styleSheet().strip() != ""
    assert "QListWidget::item:selected" in dialog.styleSheet()


class TestAutosaveWorkflowsPreferencesPage:
    def _fake_controller(self, enabled=False):
        controller = MagicMock()
        controller.enabled = enabled
        return controller

    def test_checkbox_reflects_the_controllers_current_state(self, qapp):  # noqa: ARG001
        page = AutosaveWorkflowsPreferencesPage(self._fake_controller(enabled=True))

        assert page._checkbox.isChecked() is True

    def test_toggling_the_checkbox_updates_the_controller(self, qapp):  # noqa: ARG001
        controller = self._fake_controller(enabled=False)
        page = AutosaveWorkflowsPreferencesPage(controller)

        page._checkbox.setChecked(True)

        controller.set_enabled.assert_called_once_with(True)
