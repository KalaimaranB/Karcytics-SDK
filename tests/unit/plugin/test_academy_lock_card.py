"""The Academy lock card: locks a panel (widgets and menu bar) behind a
centred card, and gives back exactly what it took on unlock.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QMainWindow, QPushButton, QVBoxLayout, QWidget

from karcytics_sdk.plugin.academy_lock_card import AcademyLockCard, LockCardContent

CONTENT = LockCardContent(
    eyebrow="KARCYTICS ACADEMY · PAUSED",
    title="Ready to continue Course 2?",
    message="Cyto kept your place.",
    primary_text="▶ Resume",
    secondary_text="Leave course…",
    step_number=23,
    step_total=60,
)


def make_window() -> tuple[QMainWindow, QWidget, QPushButton, QLineEdit]:
    window = QMainWindow()
    window.menuBar().addMenu("File")
    panel = QWidget()
    layout = QVBoxLayout(panel)
    button = QPushButton("Draw gate", panel)
    field = QLineEdit(panel)
    layout.addWidget(button)
    layout.addWidget(field)
    window.setCentralWidget(panel)
    window.resize(1200, 800)
    return window, panel, button, field


def test_locking_disables_the_panel_and_menu_bar():
    window, panel, button, field = make_window()
    card = AcademyLockCard(panel)

    card.show_lock(CONTENT)

    assert card.is_locked
    assert not button.isEnabled()
    assert not field.isEnabled()
    assert not window.menuBar().isEnabled()
    assert card.primary_button.isEnabled()
    assert card.geometry() == panel.rect()


def test_unlock_restores_only_what_the_card_disabled():
    window, panel, button, field = make_window()
    field.setEnabled(False)  # the panel's own choice
    card = AcademyLockCard(panel)

    card.show_lock(CONTENT)
    card.unlock()

    assert not card.is_locked
    assert button.isEnabled()
    assert not field.isEnabled()
    assert window.menuBar().isEnabled()
    assert card.isHidden()


def test_a_widget_added_while_locked_is_disabled_too():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel)
    card.show_lock(CONTENT)

    late = QPushButton("New plot", panel)
    assert not late.isEnabled()

    card.unlock()
    assert late.isEnabled()


def test_card_shows_the_text_and_progress():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel)
    card.show_lock(CONTENT)

    assert card.title_label.text() == "Ready to continue Course 2?"
    assert card.progress_label.text() == "Step 23 of 60"
    assert card.progress_bar.value() == 23
    assert not card.progress_bar.isHidden()


def test_no_progress_bar_without_a_total():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel)
    card.show_lock(LockCardContent("E", "T", "M", "Go", "Leave"))

    assert card.progress_bar.isHidden()


def test_buttons_report_clicks():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel)
    card.show_lock(CONTENT)
    clicks: list[str] = []
    card.primary_clicked.connect(lambda: clicks.append("primary"))
    card.secondary_clicked.connect(lambda: clicks.append("secondary"))

    card.primary_button.click()
    card.secondary_button.click()

    assert clicks == ["primary", "secondary"]


def test_hidden_character_stays_hidden():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel, show_character=False)
    card.show_lock(CONTENT)

    assert card.cyto.isHidden()


def test_an_input_field_shows_only_when_asked_for():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel)

    card.show_lock(CONTENT)
    assert card.input_field.isHidden()
    assert card.input_text() == ""

    card.show_lock(LockCardContent("E", "T", "M", "Go", "Leave", input_label="Name:", input_default="Flow"))
    assert not card.input_field.isHidden()
    card.input_field.setText("  My run  ")
    assert card.input_text() == "My run"


def test_enter_in_the_field_is_the_primary_choice():
    _window, panel, _button, _field = make_window()
    card = AcademyLockCard(panel)
    card.show_lock(LockCardContent("E", "T", "M", "Go", "Leave", input_label="Name:"))
    clicks: list[bool] = []
    card.primary_clicked.connect(lambda: clicks.append(True))

    card.input_field.returnPressed.emit()

    assert clicks == [True]
