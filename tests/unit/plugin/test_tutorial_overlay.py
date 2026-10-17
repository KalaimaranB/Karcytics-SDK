"""Footer reflow: a button row too wide to share the line with the progress
bar must move the bar onto its own row rather than clip the buttons.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from karcytics_sdk.plugin.academy import AcademyManager
from karcytics_sdk.plugin.tutorial_models import ConsentStep, Course, InfoStep
from karcytics_sdk.plugin.tutorial_overlay import TutorialOverlay


class FakeEventBus:
    def __init__(self) -> None:
        self.subscriptions: dict[str, list[Any]] = {}

    def subscribe(self, topic: str, callback: Any) -> None:
        self.subscriptions.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: Any) -> None:
        if callback in self.subscriptions.get(topic, []):
            self.subscriptions[topic].remove(callback)

    def emit(self, topic: str, *args: Any) -> None:
        for callback in list(self.subscriptions.get(topic, [])):
            callback(*args)


def make_overlay(tmp_path, *steps) -> tuple[TutorialOverlay, AcademyManager]:
    bus = FakeEventBus()
    manager = AcademyManager(event_bus=bus, persistence_dir=tmp_path / "academy")
    course = Course(id="c", title="C", steps=list(steps))
    manager.register_storyboard("m", course)
    manager.start_course_confirmed(course.id)
    root = QWidget()
    root.resize(1200, 800)
    overlay = TutorialOverlay(manager, bus, parent=root)
    overlay._root = root  # type: ignore[attr-defined]  # keep parent alive
    return overlay, manager


def footer_row(overlay: TutorialOverlay, widget: QWidget) -> int:
    index = overlay.footer_layout.indexOf(widget)
    row, _col, _rowspan, _colspan = overlay.footer_layout.getItemPosition(index)
    return row


def test_short_buttons_keep_progress_bar_inline(tmp_path):
    overlay, _ = make_overlay(tmp_path, InfoStep(id="a", text="Hello"))
    overlay.render_step(overlay._academy_manager.current_step)

    assert not overlay._footer_stacked
    assert footer_row(overlay, overlay.progress_bar) == footer_row(overlay, overlay.btn_container) == 0


def test_long_consent_labels_stack_progress_bar_above_buttons(tmp_path):
    consent = ConsentStep(
        id="b",
        text="Can we download a demo file?",
        accept_text="Yes, download it into my folder",
        decline_text="No, I'll use my own file instead",
    )
    overlay, manager = make_overlay(tmp_path, InfoStep(id="a", text="Hello", next_step_id="b"), consent)
    manager.next_step()
    overlay.render_step(manager.current_step)

    assert overlay._footer_stacked
    assert footer_row(overlay, overlay.progress_bar) == 0
    assert footer_row(overlay, overlay.btn_container) == 1

    # Back to a plain step: the footer returns to a single row.
    overlay.render_step(InfoStep(id="c", text="Done"))
    assert not overlay._footer_stacked
    assert footer_row(overlay, overlay.btn_container) == 0


def test_buttons_wider_than_full_row_lift_previous_beside_progress_bar(tmp_path):
    consent = ConsentStep(
        id="b",
        text="Can we download a demo file?",
        accept_text="Yes, download the demo file into my Downloads folder",
        decline_text="No thanks, I'll provide my own file",
    )
    overlay, manager = make_overlay(tmp_path, InfoStep(id="a", text="Hello", next_step_id="b"), consent)
    manager.next_step()
    overlay.render_step(manager.current_step)

    assert overlay._footer_stacked
    assert overlay.footer_layout.indexOf(overlay.btn_previous) != -1
    assert not overlay.btn_previous.isHidden()
    assert footer_row(overlay, overlay.btn_previous) == footer_row(overlay, overlay.progress_bar) == 0
    assert footer_row(overlay, overlay.btn_container) == 1

    # Re-rendering the same step keeps Previous lifted; a plain step restores it.
    overlay.render_step(manager.current_step)
    assert overlay.footer_layout.indexOf(overlay.btn_previous) != -1
    overlay.render_step(InfoStep(id="c", text="Done"))
    assert overlay.footer_layout.indexOf(overlay.btn_previous) == -1
    assert not overlay._footer_stacked


def test_new_spotlight_repaints_opaque_widgets_under_it(tmp_path, monkeypatch):
    """parent.repaint() doesn't repaint an opaque child (e.g. a matplotlib
    canvas) in the region, which would leave the previous step's dim inside
    the new hole — so such widgets are asked to update themselves.
    """
    from PySide6.QtCore import QRect, Qt

    overlay, manager = make_overlay(tmp_path, InfoStep(id="a", text="Read the plot", allow_interaction=True))
    overlay.render_step(manager.current_step)
    root = overlay._root  # type: ignore[attr-defined]

    def opaque_child(geometry: QRect) -> tuple[QWidget, list[Any]]:
        widget = QWidget(root)
        widget.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        widget.setGeometry(geometry)
        widget.show()
        calls: list[Any] = []
        monkeypatch.setattr(widget, "update", lambda *args: calls.append(args))
        return widget, calls

    root.show()
    overlay.raise_()
    _canvas, canvas_updates = opaque_child(QRect(400, 50, 300, 200))
    _elsewhere, elsewhere_updates = opaque_child(QRect(900, 600, 100, 100))

    overlay.set_targets([QRect(400, 50, 300, 200)])

    assert len(canvas_updates) == 1
    assert canvas_updates[0][0].boundingRect() == QRect(0, 0, 300, 200)
    assert elsewhere_updates == []


def test_cyto_character_can_be_turned_off(tmp_path):
    from PySide6.QtCore import QRect

    overlay, manager = make_overlay(tmp_path, InfoStep(id="a", text="Hello"))
    overlay._root.show()  # type: ignore[attr-defined]
    overlay.setGeometry(overlay._root.rect())  # type: ignore[attr-defined]
    try:
        overlay.render_step(manager.current_step)
        assert overlay.cyto.isVisible()

        manager.set_show_character(False)
        overlay.refresh_character()
        assert not overlay.cyto.isVisible()
        assert overlay.bubble_container.isVisible()
        # Alone, the bubble is centred rather than offset to leave Cyto room.
        bubble = overlay.bubble_container
        assert abs(bubble.geometry().center().x() - overlay.width() // 2) <= bubble.width() // 2 - 100

        # A targeted step keeps him hidden and the bubble on screen.
        overlay.set_targets([QRect(100, 100, 120, 30)])
        overlay.render_step(manager.current_step)
        assert not overlay.cyto.isVisible()
        assert overlay.rect().contains(bubble.geometry())
    finally:
        overlay._root.close()  # type: ignore[attr-defined]


def test_pause_is_offered_in_place_of_exit_only_when_pausable(tmp_path):
    overlay, _ = make_overlay(tmp_path, InfoStep(id="a", text="Hello"))

    assert overlay.btn_pause.isHidden()
    assert not overlay.btn_close.isHidden()

    overlay.set_pausable(True)
    assert not overlay.btn_pause.isHidden()
    assert overlay.btn_close.isHidden()


def test_pause_button_requests_a_pause(tmp_path):
    overlay, _ = make_overlay(tmp_path, InfoStep(id="a", text="Hello"))
    overlay.set_pausable(True)
    requests: list[bool] = []
    overlay.pause_requested.connect(lambda: requests.append(True))

    overlay.btn_pause.click()

    assert requests == [True]


def test_pause_waits_for_an_open_dialog(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    overlay, _ = make_overlay(tmp_path, InfoStep(id="a", text="Hello"))
    overlay.set_pausable(True)
    requests: list[bool] = []
    overlay.pause_requested.connect(lambda: requests.append(True))
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: QWidget()))

    overlay.btn_pause.click()

    assert requests == []
