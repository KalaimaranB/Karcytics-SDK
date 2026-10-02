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
        """Frames exactly `widgets` (descendants of `host`); hides the rest."""
        while len(self._frames) < len(widgets):
            self._frames.append(self._new_frame())
        for frame, widget in zip(self._frames, widgets, strict=False):
            top_left = widget.mapTo(self.host, QPoint(0, 0))
            frame.setGeometry(QRect(top_left, widget.size()).adjusted(-_PAD, -_PAD, _PAD, _PAD))
            frame.show()
            frame.raise_()
        for frame in self._frames[len(widgets) :]:
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
