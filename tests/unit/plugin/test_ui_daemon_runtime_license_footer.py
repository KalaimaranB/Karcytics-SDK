"""The Hub's license line appears in exactly one footer of a plugin window."""

from PySide6.QtWidgets import QMainWindow, QStatusBar, QVBoxLayout, QWidget

from karcytics_sdk.plugin.components import LICENSE_LABEL_ENV, BioFooter
from karcytics_sdk.plugin.ui_daemon_runtime import _add_license_footer

LINE = "Class license · UBC MICB 323 – 2026W"


def test_biofooter_shows_the_license_line(qtbot, monkeypatch):
    monkeypatch.setenv(LICENSE_LABEL_ENV, LINE)
    footer = BioFooter(copyright_text="Flow Cytometry (v1)  © K")
    qtbot.addWidget(footer)
    assert footer.license_label.text() == LINE
    assert not footer.license_label.isHidden()


def test_biofooter_hides_the_label_without_a_line(qtbot, monkeypatch):
    monkeypatch.delenv(LICENSE_LABEL_ENV, raising=False)
    footer = BioFooter()
    qtbot.addWidget(footer)
    assert footer.license_label.isHidden()


def test_panel_with_its_own_footer_gets_no_second_one(qtbot, monkeypatch):
    monkeypatch.setenv(LICENSE_LABEL_ENV, LINE)
    window = QMainWindow()
    qtbot.addWidget(window)
    panel = QWidget()
    QVBoxLayout(panel).addWidget(BioFooter())
    window.setCentralWidget(panel)

    assert _add_license_footer(window, panel) is None
    assert window.findChild(QStatusBar) is None


def test_panel_without_a_footer_gets_one(qtbot, monkeypatch):
    monkeypatch.setenv(LICENSE_LABEL_ENV, LINE)
    window = QMainWindow()
    qtbot.addWidget(window)
    panel = QWidget()
    window.setCentralWidget(panel)

    label = _add_license_footer(window, panel)
    assert label is not None and label.text() == LINE


def test_no_footer_without_a_license_line(qtbot, monkeypatch):
    monkeypatch.delenv(LICENSE_LABEL_ENV, raising=False)
    window = QMainWindow()
    qtbot.addWidget(window)
    assert _add_license_footer(window, QWidget()) is None
