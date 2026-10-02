"""Targets inside another window are framed there, not spotlit underneath it."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QDialog, QPushButton, QScrollArea, QWidget

from karcytics_sdk.plugin.academy import AcademyManager
from karcytics_sdk.plugin.academy_driver import AcademyStepDriver
from karcytics_sdk.plugin.tutorial_highlight import TutorialHighlight
from karcytics_sdk.plugin.tutorial_models import Course, InfoStep
from karcytics_sdk.plugin.tutorial_overlay import TutorialOverlay


class FakeEventBus:
    def subscribe(self, topic: str, callback: Any) -> None:
        pass

    def unsubscribe(self, topic: str, callback: Any) -> None:
        pass

    def emit(self, topic: str, *args: Any) -> None:
        pass


def make(tmp_path, *names: str):
    course = Course(id="c", title="C", steps=[InfoStep(id="s", text="x", target_widget_names=list(names))])
    manager = AcademyManager(event_bus=FakeEventBus(), persistence_dir=tmp_path / "academy")
    manager.register_storyboard("m", course)
    manager.start_course_confirmed("c")
    root = QWidget()
    root.resize(900, 600)
    overlay = TutorialOverlay(manager, FakeEventBus(), parent=root)
    overlay.setGeometry(root.rect())
    driver = AcademyStepDriver(manager, overlay, root, state_provider=lambda: root)

    in_panel = QPushButton("Panel button", root)
    in_panel.setObjectName("PanelButton")
    in_panel.setGeometry(20, 20, 120, 30)
    # Parented to the panel (so findChildren() finds it) but its own window —
    # like the flow plugin's Derived Parameters dialog.
    dialog = QDialog(root)
    dialog.resize(300, 200)
    in_dialog = QPushButton("Close", dialog)
    in_dialog.setObjectName("DialogClose")
    in_dialog.setGeometry(200, 150, 80, 30)
    root.show()
    dialog.show()
    return driver, manager, overlay, root, dialog


def teardown(driver: AcademyStepDriver, root: QWidget, dialog: QDialog) -> None:
    """Stop the driver's tick timer and close the windows, so nothing fires
    into a later test's event loop against deleted widgets.
    """
    driver._timer.stop()
    dialog.close()
    root.close()
    root.deleteLater()


def test_dialog_target_is_framed_in_the_dialog_and_its_window_avoided(tmp_path):
    driver, manager, overlay, root, dialog = make(tmp_path, "PanelButton", "DialogClose")
    try:
        driver._update_targets(manager.current_step)

        highlight = driver._window_highlights[dialog]
        assert highlight.visible_count == 1
        frame = dialog.frameGeometry()
        dialog_rect = QRect(overlay.mapFromGlobal(frame.topLeft()), frame.size())
        assert overlay.target_rects == [QRect(20, 20, 120, 30), dialog_rect]
    finally:
        teardown(driver, root, dialog)


def test_frames_clear_when_the_step_no_longer_targets_the_dialog(tmp_path):
    driver, manager, _overlay, root, dialog = make(tmp_path, "DialogClose")
    try:
        driver._update_targets(manager.current_step)
        assert driver._window_highlights[dialog].visible_count == 1

        driver._update_targets(InfoStep(id="next", text="y", target_widget_names=["PanelButton"]))
        assert driver._window_highlights[dialog].visible_count == 0
    finally:
        teardown(driver, root, dialog)


def test_frames_expire_without_driver_refreshes(qtbot):
    host = QWidget()
    target = QWidget(host)
    host.show()
    highlight = TutorialHighlight(host)
    highlight.show_on([target])
    assert highlight.visible_count == 1
    qtbot.waitUntil(lambda: highlight.visible_count == 0, timeout=2000)
    host.close()
    host.deleteLater()


def _scrolled_dialog(qtbot):
    """A dialog whose target sits far down a scroll area, out of view."""
    dialog = QDialog()
    dialog.resize(300, 200)
    area = QScrollArea(dialog)
    area.setGeometry(0, 0, 300, 150)
    content = QWidget()
    content.resize(280, 1000)
    target = QPushButton("Deep", content)
    target.setObjectName("Deep")
    target.setGeometry(10, 900, 100, 30)
    area.setWidget(content)
    footer = QPushButton("Close", dialog)
    footer.setGeometry(200, 160, 80, 30)
    dialog.show()
    qtbot.addWidget(dialog)
    return dialog, area, target


def test_scrolled_out_target_gets_no_frame(qtbot):
    dialog, _area, target = _scrolled_dialog(qtbot)
    highlight = TutorialHighlight(dialog)
    highlight.show_on([target])
    assert highlight.visible_count == 0


def test_dialog_target_is_scrolled_into_view_once_per_step(tmp_path):
    driver, manager, _overlay, root, dialog = make(tmp_path, "Deep")
    area = QScrollArea(dialog)
    area.setGeometry(0, 0, 300, 150)
    content = QWidget()
    content.resize(280, 1000)
    target = QPushButton("Deep", content)
    target.setObjectName("Deep")
    target.setGeometry(10, 900, 100, 30)
    area.setWidget(content)
    area.show()
    try:
        driver._update_targets(manager.current_step)
        assert area.verticalScrollBar().value() > 0
        assert driver._window_highlights[dialog].visible_count == 1

        # The learner scrolls away; later ticks of the same step leave it.
        area.verticalScrollBar().setValue(0)
        driver._update_targets(manager.current_step)
        assert area.verticalScrollBar().value() == 0
        assert driver._window_highlights[dialog].visible_count == 0
    finally:
        teardown(driver, root, dialog)


def test_panel_target_scrolled_out_of_a_sidebar_is_scrolled_into_view(tmp_path):
    driver, manager, overlay, root, dialog = make(tmp_path, "Deep")
    area = QScrollArea(root)
    area.setGeometry(0, 100, 300, 150)
    content = QWidget()
    content.resize(280, 1000)
    target = QPushButton("Deep", content)
    target.setObjectName("Deep")
    target.setGeometry(10, 900, 100, 30)
    area.setWidget(content)
    area.show()
    try:
        driver._update_targets(manager.current_step)
        (rect,) = overlay.target_rects
        assert overlay.rect().contains(rect)
    finally:
        teardown(driver, root, dialog)
