"""Dirty-region-aware `QGraphicsScene`/`QGraphicsView` base classes.

Qt's own dirty-rect tracking already turns a `QGraphicsItem.update()` call
into a minimal invalidated-region repaint, as long as the view's
`ViewportUpdateMode` isn't `FullViewportUpdate`. Several plugin canvases
built before this module existed set `FullViewportUpdate` explicitly (often
to paper over a missing `prepareGeometryChange()` call somewhere), forcing a
full-viewport repaint on every single item change. `DirtyTrackingGraphicsView`
defaults to `MinimalViewportUpdate` instead, and `DirtyTrackingGraphicsScene`
adds an opt-in debug check to help catch the missing-`prepareGeometryChange()`
bug class that mode change can expose.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QRectF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QWidget

logger = logging.getLogger(__name__)

STRICT_DIRTY_TRACKING_ENV = "KARCYTICS_STRICT_DIRTY_TRACKING"


class DirtyTrackingGraphicsScene(QGraphicsScene):
    """`QGraphicsScene` companion to `DirtyTrackingGraphicsView`.

    `mark_dirty()` is a drop-in replacement for a bare `item.update()` call.
    When the `KARCYTICS_STRICT_DIRTY_TRACKING` environment variable is set,
    it additionally remembers each item's last-seen `boundingRect()` and
    warns when it differs from what was recorded the previous time
    `mark_dirty()` was called for that item. This is an imperfect signal —
    it cannot see whether `prepareGeometryChange()` was actually called in
    between — but a bounding-rect change it catches is a real prompt to
    check: under `MinimalViewportUpdate`, a geometry change that skipped
    `prepareGeometryChange()` can leave stale or clipped pixels on screen,
    a bug that Qt's default `FullViewportUpdate` mode silently masks.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._last_bounds: dict[int, QRectF] = {}

    def mark_dirty(self, item: QGraphicsItem) -> None:
        """Mark `item` for repaint, matching a bare `item.update()` call."""
        if os.environ.get(STRICT_DIRTY_TRACKING_ENV):
            current = item.boundingRect()
            previous = self._last_bounds.get(id(item))
            if previous is not None and previous != current:
                logger.warning(
                    "%r's boundingRect() changed (%s -> %s) since it was last marked dirty. "
                    "If this item's boundingRect() depends on mutable state, ensure "
                    "prepareGeometryChange() is called before mutating that state, not just "
                    "item.update()/mark_dirty() after.",
                    item,
                    previous,
                    current,
                )
            self._last_bounds[id(item)] = current
        item.update()

    def removeItem(self, item: QGraphicsItem | None) -> None:
        """Remove `item`, also dropping its tracked bounding-rect history."""
        if item is not None:
            self._last_bounds.pop(id(item), None)
        super().removeItem(item)  # type: ignore[arg-type]


class DirtyTrackingGraphicsView(QGraphicsView):
    """`QGraphicsView` defaulting to `MinimalViewportUpdate` instead of a full-viewport repaint per change."""

    def __init__(
        self,
        scene: DirtyTrackingGraphicsScene,
        parent: QWidget | None = None,
        update_mode: QGraphicsView.ViewportUpdateMode | None = None,
    ) -> None:
        super().__init__(scene, parent)
        self.setViewportUpdateMode(update_mode or QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
