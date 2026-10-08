"""Unit tests for karcytics_sdk.plugin.rendering.graphics_scene."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsView

from karcytics_sdk.plugin.rendering.graphics_scene import (
    STRICT_DIRTY_TRACKING_ENV,
    DirtyTrackingGraphicsScene,
    DirtyTrackingGraphicsView,
)


class _ResizableRectItem(QGraphicsRectItem):
    """A minimal item whose boundingRect() depends on mutable state, mirroring
    the flow-cytometry NodeItem.set_orientation() bug: it changes `_size`
    and calls update() WITHOUT prepareGeometryChange().
    """

    def __init__(self):
        super().__init__()
        self._size = 10.0

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._size, self._size)

    def resize_without_prepare_geometry_change(self, new_size: float) -> None:
        self._size = new_size


class TestDirtyTrackingGraphicsView:
    def test_defaults_to_minimal_viewport_update(self, qapp):
        scene = DirtyTrackingGraphicsScene()
        view = DirtyTrackingGraphicsView(scene)

        assert view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate

    def test_an_explicit_update_mode_overrides_the_default(self, qapp):
        scene = DirtyTrackingGraphicsScene()
        view = DirtyTrackingGraphicsView(scene, update_mode=QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

        assert view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate


class TestMarkDirty:
    def test_mark_dirty_is_a_drop_in_replacement_for_item_update(self, qapp, monkeypatch):
        monkeypatch.delenv(STRICT_DIRTY_TRACKING_ENV, raising=False)
        scene = DirtyTrackingGraphicsScene()
        item = QGraphicsRectItem(0, 0, 5, 5)
        scene.addItem(item)

        scene.mark_dirty(item)  # must not raise, strict mode disabled

    def test_strict_mode_off_by_default_does_not_warn_on_a_geometry_change(self, qapp, monkeypatch, caplog):
        monkeypatch.delenv(STRICT_DIRTY_TRACKING_ENV, raising=False)
        scene = DirtyTrackingGraphicsScene()
        item = _ResizableRectItem()
        scene.addItem(item)

        scene.mark_dirty(item)
        item.resize_without_prepare_geometry_change(20.0)
        scene.mark_dirty(item)

        assert "boundingRect()" not in caplog.text

    def test_strict_mode_warns_when_bounding_rect_changes_without_prepare_geometry_change(
        self, qapp, monkeypatch, caplog
    ):
        monkeypatch.setenv(STRICT_DIRTY_TRACKING_ENV, "1")
        scene = DirtyTrackingGraphicsScene()
        item = _ResizableRectItem()
        scene.addItem(item)

        scene.mark_dirty(item)  # establishes the baseline bounding rect (10x10)
        item.resize_without_prepare_geometry_change(20.0)

        with caplog.at_level("WARNING"):
            scene.mark_dirty(item)

        assert "boundingRect()" in caplog.text

    def test_strict_mode_does_not_warn_when_bounding_rect_is_unchanged(self, qapp, monkeypatch, caplog):
        monkeypatch.setenv(STRICT_DIRTY_TRACKING_ENV, "1")
        scene = DirtyTrackingGraphicsScene()
        item = _ResizableRectItem()
        scene.addItem(item)

        scene.mark_dirty(item)
        with caplog.at_level("WARNING"):
            scene.mark_dirty(item)  # no resize in between

        assert "boundingRect()" not in caplog.text

    def test_remove_item_drops_its_tracked_bounds_history(self, qapp, monkeypatch, caplog):
        monkeypatch.setenv(STRICT_DIRTY_TRACKING_ENV, "1")
        scene = DirtyTrackingGraphicsScene()
        item = _ResizableRectItem()
        scene.addItem(item)
        scene.mark_dirty(item)

        scene.removeItem(item)

        assert id(item) not in scene._last_bounds
