"""Shared mouse-drag-to-reposition behaviour for floating Academy widgets.

Both Cyto (`CytoWidget`, a `QGraphicsView`) and the instruction bubble
(`_DraggableBubble` in `tutorial_overlay.py`, a `QWidget`) need identical
"click and drag me anywhere" behaviour, but their only common Qt ancestor
is `QWidget` itself. This mixin has no Qt base class of its own — adding a
`QObject` base here would conflict with whichever Qt widget class it's
combined with — so it can be mixed into either without MRO/metaclass
issues. Each concrete widget still declares its own `drag_started`/
`drag_finished`/`dragged_by` signals and wires the three `mouse*Event`
overrides to call into it.

Cyto and the bubble are dragged as a linked pair: `dragged_by` reports
the actual (post-clamp) pixel offset a drag just applied so the overlay
can apply that same offset to the other widget, keeping them together.
"""

from PyQt6.QtCore import QPoint, Qt


class DraggableMixin:
    """Adds click-and-drag repositioning to a `QWidget` subclass.

    The concrete class must define `drag_started`/`drag_finished`
    `pyqtSignal()`s, a `dragged_by = pyqtSignal(QPoint)`, and call
    `_init_draggable()` once in `__init__`, then route its
    `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` overrides
    through `_handle_drag_press`/`_handle_drag_move`/`_handle_drag_release`,
    falling back to `super()` when they return `False`.
    """

    def _init_draggable(self) -> None:
        self._drag_active = False
        self._drag_anchor = QPoint()
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # type: ignore[attr-defined]

    def _handle_drag_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self._drag_active = True
        self._drag_anchor = event.position().toPoint()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)  # type: ignore[attr-defined]
        self.drag_started.emit()  # type: ignore[attr-defined]
        return True

    def _handle_drag_move(self, event) -> bool:
        if not self._drag_active:
            return False
        delta = event.position().toPoint() - self._drag_anchor
        old_pos = self.pos()  # type: ignore[attr-defined]
        new_pos = old_pos + delta
        parent = self.parentWidget()  # type: ignore[attr-defined]
        if parent is not None:
            new_pos.setX(min(max(0, new_pos.x()), max(0, parent.width() - self.width())))  # type: ignore[attr-defined]
            new_pos.setY(min(max(0, new_pos.y()), max(0, parent.height() - self.height())))  # type: ignore[attr-defined]
        self.move(new_pos)  # type: ignore[attr-defined]
        # Report the offset actually applied (post-clamp) so the linked
        # widget (Cyto <-> bubble) can be carried along by the same amount.
        self.dragged_by.emit(new_pos - old_pos)  # type: ignore[attr-defined]
        return True

    def _handle_drag_release(self, event) -> bool:  # noqa: ARG002
        if not self._drag_active:
            return False
        self._drag_active = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # type: ignore[attr-defined]
        self.drag_finished.emit()  # type: ignore[attr-defined]
        return True
