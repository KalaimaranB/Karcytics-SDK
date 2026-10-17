"""A zoomable, pannable image viewer for image-analysis plugins.

`ImageView` is the part every image plugin's canvas had re-implemented
(western-blot's `ImageCanvas`, cytometrics' `MultiChannelCanvas`): showing
an image, wheel zoom around the cursor with limits, panning, fit-to-view, an
empty state with a load button and drag-and-drop, a calibration line, a
scale bar, and named overlay layers. Plugins subclass it and add their own
tools and items.

Panning: the middle button always pans, as does Ctrl+left-drag. Call
`set_left_drag_pans(True)` for a viewer where a plain left-drag pans too.

Subclasses that override mouse events should handle their own tool first
and fall back to ``super()``, which runs the calibration tool and panning.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .components import PrimaryButton
from .theme_fallback import Colors, theme_manager

_RGB_CHANNELS = 3
_SCALE_BAR_TARGET_FRACTION = 0.2  # aim for a bar ~20% of the viewport width
_SCALE_BAR_MARGIN_PX = 16
_SCALE_BAR_THICKNESS_PX = 4
_NICE_STEPS = (1.0, 2.0, 5.0)


def array_to_pixmap(image: np.ndarray) -> QPixmap:
    """Display pixmap for a 2-D grayscale or (H, W, 3) RGB array.

    Floats are taken as [0, 1]; other dtypes are scaled from their own
    min..max range to 8 bits for display only (the array is not modified).
    """
    if image.ndim not in (2, 3) or (image.ndim == _RGB_CHANNELS and image.shape[2] != _RGB_CHANNELS):
        raise ValueError(f"expected (H, W) or (H, W, 3), got {image.shape}")
    if np.issubdtype(image.dtype, np.floating):
        display = (np.clip(image, 0.0, 1.0) * 255).astype(np.uint8)
    elif image.dtype == np.uint8:
        display = image
    else:
        lo, hi = float(image.min()), float(image.max())
        span = hi - lo or 1.0
        display = ((image.astype(np.float64) - lo) / span * 255).astype(np.uint8)
    display = np.ascontiguousarray(display)
    h, w = display.shape[:2]
    if display.ndim == 2:  # noqa: PLR2004
        qimage = QImage(display.data, w, h, w, QImage.Format.Format_Grayscale8)
    else:
        qimage = QImage(display.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())  # copy: the QImage borrows `display`'s buffer


def nice_scale_bar_length(um_per_px: float, viewport_px: float, zoom: float) -> float:
    """A 1/2/5 x 10^n µm length close to `_SCALE_BAR_TARGET_FRACTION` of the view."""
    target_um = viewport_px * _SCALE_BAR_TARGET_FRACTION * um_per_px / zoom
    if target_um <= 0 or not math.isfinite(target_um):
        return 0.0
    magnitude = 10 ** math.floor(math.log10(target_um))
    return max(step * magnitude for step in _NICE_STEPS if step * magnitude <= target_um)


def _format_um(value: float) -> str:
    if value >= 1000:  # noqa: PLR2004
        return f"{value / 1000:g} mm"
    return f"{value:g} µm"


def _as_left_button(event: QMouseEvent) -> QMouseEvent:
    """`event` re-issued as a left-button event: QGraphicsView's ScrollHandDrag
    only starts and stops on the left button, so a middle-button pan replays as one.
    """
    if event.button() != Qt.MouseButton.MiddleButton:
        return event
    return QMouseEvent(
        event.type(),
        event.position(),
        event.globalPosition(),
        Qt.MouseButton.LeftButton,
        event.buttons() | Qt.MouseButton.LeftButton,
        event.modifiers(),
    )


class ImageView(QGraphicsView):
    """Image viewer with zoom, pan, fit, empty state, calibration and layers."""

    image_loaded = Signal()
    zoom_changed = Signal(float)
    load_requested = Signal()  # the empty state's load button was clicked
    files_dropped = Signal(list)  # list[str] of dropped files with accepted extensions
    calibration_line_drawn = Signal(float)  # length of the drawn line in image pixels

    MIN_ZOOM = 0.1
    MAX_ZOOM = 20.0
    ZOOM_STEP = 1.15

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._image_item: QGraphicsPixmapItem | None = None
        self._layers: dict[str, QGraphicsItemGroup] = {}
        self._zoom = 1.0
        self._user_zoomed = False
        self._left_drag_pans = False
        self._panning = False
        self._accepted_extensions: tuple[str, ...] = ()
        self._um_per_px: float | None = None

        self._calibrating = False
        self._calib_start: QPointF | None = None
        self._calib_item: QGraphicsLineItem | None = None

        self._empty_state: QWidget | None = None

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        theme_manager.apply_style(self, "QGraphicsView { border: none; background-color: {BG_DARKEST}; }")

    # ── Image ────────────────────────────────────────────────────────────

    @property
    def has_image(self) -> bool:
        return self._image_item is not None

    @property
    def image_rect(self) -> QRectF:
        return self._image_item.boundingRect() if self._image_item is not None else QRectF()

    def set_pixmap(self, pixmap: QPixmap, *, keep_view: bool = False) -> None:
        """Show `pixmap`. Overlay layers are kept; the view refits unless `keep_view`."""
        if self._image_item is None:
            self._image_item = self._scene.addPixmap(pixmap)
            self._image_item.setZValue(-1)
        else:
            self._image_item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        if self._empty_state is not None:
            self._empty_state.hide()
        if not keep_view:
            self.fit_to_view()
        self.image_loaded.emit()

    def set_array(self, image: np.ndarray, *, keep_view: bool = False) -> None:
        """Show a numpy image (see `array_to_pixmap` for accepted shapes and dtypes)."""
        self.set_pixmap(array_to_pixmap(image), keep_view=keep_view)

    def clear_image(self) -> None:
        if self._image_item is not None:
            self._scene.removeItem(self._image_item)
            self._image_item = None
        if self._empty_state is not None:
            self._empty_state.show()
            self._center_empty_state()

    # ── Zoom and fit ─────────────────────────────────────────────────────

    @property
    def zoom(self) -> float:
        """Zoom relative to fit-to-view (1.0 = whole image fits)."""
        return self._zoom

    def fit_to_view(self) -> None:
        self.resetTransform()
        if self._image_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self._user_zoomed = False
        self.viewport().update()

    def zoom_by(self, factor: float) -> bool:
        """Zoom by `factor` within [MIN_ZOOM, MAX_ZOOM]; returns whether it zoomed."""
        new_zoom = self._zoom * factor
        if not (self.MIN_ZOOM <= new_zoom <= self.MAX_ZOOM):
            return False
        self._zoom = new_zoom
        self._user_zoomed = True
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)
        self.viewport().update()
        return True

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP)
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._center_empty_state()
        if self._image_item is not None and not self._user_zoomed:
            self.fit_to_view()

    # ── Panning ──────────────────────────────────────────────────────────

    def set_left_drag_pans(self, enabled: bool) -> None:
        """Whether a plain left-drag pans (otherwise only middle / Ctrl+left)."""
        self._left_drag_pans = enabled
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if enabled else QGraphicsView.DragMode.NoDrag)

    def _is_pan_press(self, event) -> bool:
        if event.button() == Qt.MouseButton.MiddleButton:
            return True
        return event.button() == Qt.MouseButton.LeftButton and bool(
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )

    # ── Empty state and drag-and-drop ────────────────────────────────────

    def set_empty_state(
        self,
        title: str,
        *,
        button_text: str | None = None,
        hint: str | None = None,
        icon: str | None = None,
    ) -> None:
        """Show a centred message (and optional load button) while no image is loaded."""
        overlay = QWidget(self.viewport())
        layout = QVBoxLayout(overlay)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon:
            icon_label = QLabel(icon)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet("font-size: 40px; border: none; background: transparent;")
            layout.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme_manager.apply_style(
            title_label,
            "color: {FG_PRIMARY}; font-size: 15px; font-weight: bold; border: none; background: transparent;",
        )
        layout.addWidget(title_label)
        if button_text:
            button = PrimaryButton(button_text)
            button.clicked.connect(self.load_requested.emit)
            layout.addWidget(button)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            theme_manager.apply_style(
                hint_label, "color: {FG_SECONDARY}; font-size: 12px; border: none; background: transparent;"
            )
            layout.addWidget(hint_label)
        overlay.adjustSize()
        if self._empty_state is not None:
            self._empty_state.deleteLater()
        self._empty_state = overlay
        overlay.setVisible(self._image_item is None)
        self._center_empty_state()

    def _center_empty_state(self) -> None:
        if self._empty_state is None:
            return
        size = self._empty_state.sizeHint()
        x = (self.viewport().width() - size.width()) // 2
        y = (self.viewport().height() - size.height()) // 2
        self._empty_state.setGeometry(max(0, x), max(0, y), size.width(), size.height())

    def accept_dropped_files(self, extensions: Iterable[str]) -> None:
        """Enable drag-and-drop of files with these extensions (e.g. ``(".tif", ".png")``)."""
        self._accepted_extensions = tuple(e.lower() for e in extensions)
        self.setAcceptDrops(bool(self._accepted_extensions))

    def _dropped_paths(self, event) -> list[str]:
        mime = event.mimeData()
        if not self._accepted_extensions or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.toLocalFile().lower().endswith(self._accepted_extensions)]

    def dragEnterEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── Calibration line ─────────────────────────────────────────────────

    @property
    def is_calibrating(self) -> bool:
        return self._calibrating

    def start_calibration(self) -> None:
        """Next left-drag draws a line (Shift: horizontal/vertical); emits its length in px."""
        self._calibrating = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def cancel_calibration(self) -> None:
        self._finish_calibration(emit=False)

    def _finish_calibration(self, *, emit: bool) -> None:
        if emit and self._calib_item is not None:
            length = self._calib_item.line().length()
            if length > 0:
                self.calibration_line_drawn.emit(length)
        self._calibrating = False
        self._calib_start = None
        self.viewport().unsetCursor()
        self.set_left_drag_pans(self._left_drag_pans)

    def _calibration_point(self, event) -> QPointF:
        pos = self.mapToScene(event.position().toPoint())
        if self._calib_start is not None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if abs(pos.x() - self._calib_start.x()) > abs(pos.y() - self._calib_start.y()):
                pos.setY(self._calib_start.y())
            else:
                pos.setX(self._calib_start.x())
        return pos

    # ── Scale bar ────────────────────────────────────────────────────────

    def set_scale(self, um_per_px: float | None) -> None:
        """Show a scale bar for this calibration (None or <= 0 hides it)."""
        self._um_per_px = um_per_px if um_per_px and um_per_px > 0 else None
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        super().drawForeground(painter, rect)
        if self._um_per_px is None or self._image_item is None:
            return
        view_scale = self.transform().m11() or 1.0
        bar_um = nice_scale_bar_length(self._um_per_px, self.viewport().width(), view_scale)
        if bar_um <= 0:
            return
        bar_px = bar_um / self._um_per_px * view_scale
        painter.save()
        painter.resetTransform()  # draw in viewport pixels
        right = self.viewport().width() - _SCALE_BAR_MARGIN_PX
        bottom = self.viewport().height() - _SCALE_BAR_MARGIN_PX
        color = QColor(Colors.FG_PRIMARY)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRect(QRectF(right - bar_px, bottom - _SCALE_BAR_THICKNESS_PX, bar_px, _SCALE_BAR_THICKNESS_PX))
        painter.setPen(QPen(color))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        label_rect = QRectF(right - max(bar_px, 80.0), bottom - 24, max(bar_px, 80.0), 16)
        painter.drawText(
            label_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom), _format_um(bar_um)
        )
        painter.restore()

    # ── Overlay layers ───────────────────────────────────────────────────

    def layer(self, name: str, z: float = 0.0) -> QGraphicsItemGroup:
        """The named overlay group, created on first use at depth `z`."""
        group = self._layers.get(name)
        if group is None:
            group = QGraphicsItemGroup()
            group.setHandlesChildEvents(False)  # children keep their own hover/click handling
            group.setZValue(z)
            self._scene.addItem(group)
            self._layers[name] = group
        return group

    def add_to_layer(self, name: str, item: QGraphicsItem) -> QGraphicsItem:
        self.layer(name).addToGroup(item)
        return item

    def clear_layer(self, name: str) -> None:
        group = self._layers.get(name)
        if group is None:
            return
        for child in group.childItems():
            group.removeFromGroup(child)
            self._scene.removeItem(child)

    def set_layer_visible(self, name: str, visible: bool) -> None:
        self.layer(name).setVisible(visible)

    # ── Mouse ────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if self._calibrating and event.button() == Qt.MouseButton.LeftButton:
            self._calib_start = self._calibration_point(event)
            if self._calib_item is None:
                self._calib_item = QGraphicsLineItem()
                pen = QPen(QColor(Colors.ACCENT_WARNING), 3)
                pen.setCosmetic(True)
                self._calib_item.setPen(pen)
                self._calib_item.setZValue(1000)
                self._scene.addItem(self._calib_item)
            self._calib_item.setLine(QLineF(self._calib_start, self._calib_start))
            event.accept()
            return
        if self._is_pan_press(event) and not self._left_drag_pans:
            self._panning = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event = _as_left_button(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._calibrating and self._calib_start is not None and self._calib_item is not None:
            self._calib_item.setLine(QLineF(self._calib_start, self._calibration_point(event)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._calibrating and self._calib_start is not None and event.button() == Qt.MouseButton.LeftButton:
            if self._calib_item is not None:  # the release point (and Shift state) is final
                self._calib_item.setLine(QLineF(self._calib_start, self._calibration_point(event)))
            self._finish_calibration(emit=True)
            event.accept()
            return
        if self._panning:
            super().mouseReleaseEvent(_as_left_button(event))
            self._panning = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            return
        super().mouseReleaseEvent(event)

    # ── Lifecycle ────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Release scene items. Call when the owning panel closes."""
        self._scene.clear()
        self._layers.clear()
        self._image_item = None
        self._calib_item = None
