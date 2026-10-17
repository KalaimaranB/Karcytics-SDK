"""ImageView: the shared zoom/pan/calibrate/layers image viewer."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsView, QPushButton

from karcytics_sdk.plugin.image_view import ImageView, array_to_pixmap, nice_scale_bar_length


@pytest.fixture
def view(qtbot):
    v = ImageView()
    qtbot.addWidget(v)
    v.resize(400, 400)
    return v


class TestArrayToPixmap:
    def test_grayscale_uint8(self):
        pm = array_to_pixmap(np.zeros((10, 20), np.uint8))
        assert (pm.width(), pm.height()) == (20, 10)

    def test_rgb(self):
        img = array_to_pixmap(
            np.dstack([np.full((4, 4), 255, np.uint8), np.zeros((4, 4), np.uint8), np.zeros((4, 4), np.uint8)])
        )
        assert img.toImage().pixelColor(0, 0).red() == 255
        assert img.toImage().pixelColor(0, 0).green() == 0

    def test_sixteen_bit_is_stretched_for_display_only(self):
        arr = np.array([[1000, 3000]], np.uint16)
        pm = array_to_pixmap(arr)
        row = pm.toImage()
        assert row.pixelColor(0, 0).value() == 0
        assert row.pixelColor(1, 0).value() == 255
        assert arr.dtype == np.uint16  # input untouched

    def test_float_is_read_as_unit_range(self):
        pm = array_to_pixmap(np.array([[0.0, 0.5, 2.0]]))
        values = [pm.toImage().pixelColor(x, 0).value() for x in range(3)]
        assert values == [0, 127, 255]

    def test_rejects_other_shapes(self):
        with pytest.raises(ValueError):
            array_to_pixmap(np.zeros((4, 4, 2)))


class TestImageAndZoom:
    def test_set_array_shows_image_and_signals(self, view, qtbot):
        with qtbot.waitSignal(view.image_loaded):
            view.set_array(np.zeros((50, 100), np.uint8))

        assert view.has_image
        assert view.image_rect.width() == 100

    def test_zoom_is_clamped(self, view):
        view.set_array(np.zeros((50, 50), np.uint8))

        assert view.zoom_by(2.0)
        assert not view.zoom_by(100.0)
        assert view.zoom == pytest.approx(2.0)

    def test_wheel_zooms_in_and_fit_resets(self, view, qtbot):
        view.set_array(np.zeros((50, 50), np.uint8))
        with qtbot.waitSignal(view.zoom_changed):
            view.zoom_by(ImageView.ZOOM_STEP)

        view.fit_to_view()

        assert view.zoom == pytest.approx(1.0)

    def test_new_image_keeps_layers(self, view):
        view.add_to_layer("cells", QGraphicsRectItem(0, 0, 5, 5))

        view.set_array(np.zeros((20, 20), np.uint8))
        view.set_array(np.ones((20, 20), np.uint8))

        assert len(view.layer("cells").childItems()) == 1


class TestPanning:
    def test_left_drag_pans_only_when_enabled(self, view):
        assert view.dragMode() == QGraphicsView.DragMode.NoDrag
        view.set_left_drag_pans(True)
        assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag

    def test_middle_button_pan_restores_no_drag(self, view):
        view.set_array(np.zeros((50, 50), np.uint8))

        QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=QPoint(100, 100))
        assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=QPoint(120, 100))

        assert view.dragMode() == QGraphicsView.DragMode.NoDrag


class TestCalibration:
    def _drag(self, view, start: QPoint, end: QPoint, modifier=Qt.KeyboardModifier.NoModifier):
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, modifier, start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, modifier, end)

    def test_line_length_is_reported_in_image_pixels(self, view, qtbot):
        view.set_array(np.zeros((100, 100), np.uint8))
        view.start_calibration()
        a, b = QPoint(100, 200), QPoint(300, 200)
        expected = (view.mapToScene(b) - view.mapToScene(a)).manhattanLength()

        with qtbot.waitSignal(view.calibration_line_drawn) as blocker:
            self._drag(view, a, b)

        assert blocker.args[0] == pytest.approx(expected, rel=1e-3)
        assert not view.is_calibrating

    def test_shift_constrains_to_horizontal(self, view, qtbot):
        view.set_array(np.zeros((100, 100), np.uint8))
        view.start_calibration()
        a, b = QPoint(100, 200), QPoint(300, 230)
        horizontal = abs(view.mapToScene(b).x() - view.mapToScene(a).x())

        with qtbot.waitSignal(view.calibration_line_drawn) as blocker:
            self._drag(view, a, b, Qt.KeyboardModifier.ShiftModifier)

        assert blocker.args[0] == pytest.approx(horizontal, rel=1e-3)

    def test_cancel_emits_nothing(self, view, qtbot):
        view.start_calibration()
        view.cancel_calibration()

        with qtbot.assertNotEmitted(view.calibration_line_drawn):
            self._drag(view, QPoint(10, 10), QPoint(50, 10))


class TestLayers:
    def test_clear_and_hide(self, view):
        view.add_to_layer("ids", QGraphicsRectItem(0, 0, 1, 1))
        view.add_to_layer("ids", QGraphicsRectItem(0, 0, 1, 1))

        view.set_layer_visible("ids", False)
        assert not view.layer("ids").isVisible()

        view.clear_layer("ids")
        assert view.layer("ids").childItems() == []
        assert len(view.scene().items()) == 1  # only the (empty) group remains


class TestEmptyStateAndDrops:
    def test_load_button_requests_a_load_and_hides_once_an_image_shows(self, view, qtbot):
        view.set_empty_state("Load an image", button_text="Load", hint="or drop one here")
        button = view.findChild(QPushButton)
        assert button is not None and not view._empty_state.isHidden()

        with qtbot.waitSignal(view.load_requested):
            button.click()
        view.set_array(np.zeros((5, 5), np.uint8))

        assert view._empty_state.isHidden()

    def test_drop_emits_only_accepted_files(self, view, qtbot):
        view.accept_dropped_files((".tif", ".png"))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("/data/a.TIF"), QUrl.fromLocalFile("/data/notes.txt")])
        event = QDropEvent(
            QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
        )

        with qtbot.waitSignal(view.files_dropped) as blocker:
            view.dropEvent(event)

        assert blocker.args[0] == ["/data/a.TIF"]


class TestScaleBar:
    @pytest.mark.parametrize(
        ("um_per_px", "viewport", "zoom", "expected"),
        [(0.5, 1000, 1.0, 100.0), (0.65, 1000, 1.0, 100.0), (0.65, 1000, 4.0, 20.0), (2.0, 500, 1.0, 200.0)],
    )
    def test_picks_a_round_length_near_a_fifth_of_the_view(self, um_per_px, viewport, zoom, expected):
        assert nice_scale_bar_length(um_per_px, viewport, zoom) == pytest.approx(expected)

    def test_paints_without_error(self, view):
        view.set_array(np.zeros((100, 100), np.uint8))
        view.set_scale(0.5)

        assert not view.grab().isNull()
