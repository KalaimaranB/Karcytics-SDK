"""Targets inside another window are framed there, not spotlit underneath it."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QDialog, QPushButton, QWidget

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
