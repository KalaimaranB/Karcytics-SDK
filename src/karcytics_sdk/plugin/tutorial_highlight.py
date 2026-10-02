"""Academy spotlight drawn inside a window other than the overlay's own.

The `TutorialOverlay` is a child of the plugin's main window and can't paint
over a separate window. A step's target inside a dialog or popup (a Tool
window, a non-modal QDialog…) would otherwise be spotlit on the main window
*underneath* it — wherever the target happens to sit on screen. The
`AcademyStepDriver` instead gives each such window one of these, which draws
accent frames over the targets inside that window itself.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QWidget

from .theme_fallback import theme_manager

# The driver refreshes every tick (100 ms); once it stops (step changed,
# window closed, Academy hidden) the frames disappear on their own.
_EXPIRE_MS = 400
_PAD = 3


def _visible_rect(widget: QWidget) -> QRect:
    """`widget`'s own rect clipped by every ancestor (e.g. a scroll viewport)."""
    rect = widget.rect()
    child, parent = widget, widget.parentWidget()
    while parent is not None and not child.isWindow():
        offset = widget.mapTo(parent, QPoint(0, 0))
        rect = rect.intersected(parent.rect().translated(-offset))
        child, parent = parent, parent.parentWidget()
    return rect


class TutorialHighlight:
    """Accent frames over widgets inside `host` (a top-level window)."""

    def __init__(self, host: QWidget) -> None:
        self.host = host
        self._frames: list[QFrame] = []
        self._expiry = QTimer(host)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(self.clear)

    @property
    def visible_count(self) -> int:
        return sum(1 for f in self._frames if f.isVisible())

    def show_on(self, widgets: list[QWidget]) -> None:
        """Frames exactly `widgets` (descendants of `host`); hides the rest.

        Only the part of each widget that's actually on screen is framed —
        one scrolled out of its scroll area gets no frame, rather than one
        drawn over whatever sits where it would be (a dialog's buttons).
        """
        shown = [(w, r) for w in widgets if not (r := _visible_rect(w)).isEmpty()]
        while len(self._frames) < len(shown):
            self._frames.append(self._new_frame())
        for frame, (widget, rect) in zip(self._frames, shown, strict=False):
            top_left = widget.mapTo(self.host, rect.topLeft())
            frame.setGeometry(QRect(top_left, rect.size()).adjusted(-_PAD, -_PAD, _PAD, _PAD))
            frame.show()
            frame.raise_()
        for frame in self._frames[len(shown) :]:
            frame.hide()
        self._expiry.start(_EXPIRE_MS)

    def clear(self) -> None:
        for frame in self._frames:
            frame.hide()

    def _new_frame(self) -> QFrame:
        frame = QFrame(self.host)
        frame.setObjectName("TutorialHighlightFrame")
        frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme_manager.apply_style(
            frame,
            "QFrame#TutorialHighlightFrame {"
            "  border: 2px solid {ACCENT_PRIMARY};"
            "  border-radius: 6px;"
            "  background: transparent;"
            "}",
        )
        return frame
