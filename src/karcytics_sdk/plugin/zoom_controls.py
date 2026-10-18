"""A strip of zoom buttons laid over the corner of a canvas."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QWidget

from .components import SecondaryButton


class ZoomControls(QWidget):
    """Zoom out / zoom in / fit buttons for any zoomable view.

    It only calls back, so it works over a QGraphicsView, a custom-painted
    widget or a plot alike. `overlay_on(host)` pins it to the host's
    bottom-right corner.

    Args:
        on_zoom_in, on_zoom_out, on_fit: What each button does.
        extra: More buttons to put before the zoom buttons (a pan toggle, say).
    """

    def __init__(
        self,
        on_zoom_in: Callable[[], None],
        on_zoom_out: Callable[[], None],
        on_fit: Callable[[], None],
        extra: Sequence[QAbstractButton] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.zoom_out_button = SecondaryButton("Zoom Out (-)")
        self.zoom_in_button = SecondaryButton("Zoom In (+)")
        self.fit_button = SecondaryButton("Fit View (F)")
        self.zoom_out_button.clicked.connect(on_zoom_out)
        self.zoom_in_button.clicked.connect(on_zoom_in)
        self.fit_button.clicked.connect(on_fit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for button in (*extra, self.zoom_out_button, self.zoom_in_button, self.fit_button):
            layout.addWidget(button)

    def overlay_on(self, host: QWidget, margin: int = 16) -> None:
        """Pin this strip to `host`'s bottom-right corner (`host` must have no layout yet)."""
        layout = QHBoxLayout(host)
        layout.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.setContentsMargins(0, 0, margin, margin)
        layout.addWidget(self)
