"""A "Changing theme…" cover for an isolated plugin's window.

The Hub covers its own window while a theme switch restyles it, but an
isolated plugin draws its own window in its own process: the Hub's cover
never reaches it, and a plugin with many widgets and plots restyles for a
noticeable moment with nothing on screen saying so. `covered_while` puts
the same kind of cover over the plugin window for the length of the restyle.

The restyle runs on the GUI thread, so the cover is painted once before it
starts (it can't animate meanwhile) and removed when it ends.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEventLoop, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .theme_fallback import Colors

TEXT = "Changing theme…"
_covering: list[QWidget] = []  # the window being covered, while a restyle runs


class ThemeChangeOverlay(QWidget):
    """A translucent cover with a cell-like mark and a line of text."""

    def __init__(self, parent: QWidget, text: str = TEXT) -> None:
        super().__init__(parent)
        self.setObjectName("ThemeChangeOverlay")
        self._text = text
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setGeometry(parent.rect())

    @property
    def text(self) -> str:
        return self._text

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor(Colors.BG_DARKER)
        background.setAlpha(220)
        painter.fillRect(self.rect(), background)

        cx, cy = self.width() / 2, self.height() / 2 - 30
        accent = QColor(Colors.ACCENT_PRIMARY)
        painter.setPen(Qt.PenStyle.NoPen)
        for radius, alpha in ((45, 60), (25, 200)):  # membrane, nucleus
            accent.setAlpha(alpha)
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        painter.setPen(QColor(Colors.FG_PRIMARY))
        font = QFont(self.font())
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(0, cy + 60, self.width(), 40), Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


def covered_while(window: QWidget, restyle: Callable[[], None]) -> None:
    """Run ``restyle`` with ``window`` covered by a "Changing theme…" overlay.

    The cover is painted before ``restyle`` starts. A restyle requested while
    one is already running (the event pump below can deliver it) just runs.
    """
    if _covering or not window.isVisible():
        restyle()
        return
    overlay = ThemeChangeOverlay(window)
    _covering.append(window)
    try:
        overlay.show()
        overlay.raise_()
        overlay.repaint()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        restyle()
    finally:
        _covering.remove(window)
        overlay.hide()
        overlay.deleteLater()
