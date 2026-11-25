"""Plugin windows show the Hub's license line in their footer."""

from PySide6.QtWidgets import QMainWindow

from karcytics_sdk.plugin.ui_daemon_runtime import LICENSE_LABEL_ENV, _add_license_footer


def test_footer_shows_the_hubs_license_line(qtbot, monkeypatch):
    monkeypatch.setenv(LICENSE_LABEL_ENV, "Class license · UBC MICB 323 – 2026W")
    window = QMainWindow()
    qtbot.addWidget(window)

    label = _add_license_footer(window)

    assert label is not None
    assert label.text() == "Class license · UBC MICB 323 – 2026W"
    assert window.statusBar().isAncestorOf(label)


def test_no_footer_without_a_license_line(qtbot, monkeypatch):
    monkeypatch.delenv(LICENSE_LABEL_ENV, raising=False)
    window = QMainWindow()
    qtbot.addWidget(window)

    assert _add_license_footer(window) is None
