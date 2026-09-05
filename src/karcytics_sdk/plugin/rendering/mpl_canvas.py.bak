"""A matplotlib canvas base class with an async data layer and a cheap overlay layer.

Generalizes a pattern already proven out inside the flow-cytometry plugin
(a "data layer" vs "gate overlay layer" split, with `copy_from_bbox`-based
blitting for the cheap layer) into a reusable SDK base class wired to the
`pipeline` module's compute/rasterize split:

- The **data layer** is expensive and asynchronous: `request_data_redraw()`
  debounces, then submits a `RenderComputeStage` through an `ITaskScheduler`
  so the compute runs off the Qt main thread. When it finishes, the result
  is rasterized under a `RasterLock` and applied to the canvas.
- The **overlay layer** is cheap and synchronous: `draw_overlay_artists_blit()`
  restores the cached data-layer bitmap and blits a handful of artists
  (e.g. a gate being dragged) on top, with no compute stage involved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PyQt6.QtCore import QTimer, pyqtSignal

from .lock import MPL_RASTER_LOCK, RasterLock

if TYPE_CHECKING:
    from collections.abc import Iterable

    from matplotlib.artist import Artist
    from matplotlib.figure import Figure

    from karcytics_sdk.interfaces.i_crash_reporter import ICrashReporter
    from karcytics_sdk.interfaces.i_task_scheduler import ITaskScheduler

    from ..state import PluginState
    from .pipeline import RasterizeStage, RenderComputeStage

logger = logging.getLogger(__name__)


class LayeredMatplotlibCanvas(FigureCanvasQTAgg):
    """`FigureCanvasQTAgg` base class with a debounced async data layer and a cheap overlay layer.

    `raster_lock` defaults to the shared `MPL_RASTER_LOCK` singleton and
    `task_scheduler` defaults to `runtime_services.task_scheduler` — both
    overridable at construction so tests can inject a synchronous fake
    scheduler and a private lock instead of the process-wide ones.
    """

    data_layer_started = pyqtSignal()
    data_layer_finished = pyqtSignal()
    data_layer_failed = pyqtSignal(str)

    def __init__(  # noqa: PLR0913, PLR0917 - a small DI-style constructor; each param is a distinct collaborator, not a group that bundles cleanly
        self,
        figure: Figure,
        raster_lock: RasterLock | None = None,
        task_scheduler: ITaskScheduler | None = None,
        parent: Any = None,
        crash_reporter: ICrashReporter | None = None,
        plugin_id: str | None = None,
    ) -> None:
        super().__init__(figure)
        if parent is not None:
            self.setParent(parent)

        self.raster_lock = raster_lock or MPL_RASTER_LOCK
        self._task_scheduler = task_scheduler
        self._crash_reporter = crash_reporter
        self._plugin_id = plugin_id

        self._compute_stage: RenderComputeStage | None = None
        self._rasterize_stage: RasterizeStage | None = None
        self._pending_state: PluginState | None = None
        self._generation = 0
        self._bitmap_cache: Any = None

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.timeout.connect(self._submit_data_layer)

    # ── Data layer ──────────────────────────────────────────────────

    def set_compute_stage(self, stage: RenderComputeStage) -> None:
        """Set the (expensive, off-thread) compute stage for the data layer."""
        self._compute_stage = stage

    def set_rasterize_stage(self, stage: RasterizeStage) -> None:
        """Set the (short, lock-held) rasterize stage for the data layer."""
        self._rasterize_stage = stage

    def request_data_redraw(self, state: PluginState | None = None, debounce_ms: int = 50) -> None:
        """Schedule a data-layer recompute+redraw, debounced by `debounce_ms`.

        Rapid successive calls (e.g. a slider being dragged) collapse into a
        single compute submission once the debounce window elapses. Requires
        `set_compute_stage()`/`set_rasterize_stage()` to have been called
        first.
        """
        if self._compute_stage is None or self._rasterize_stage is None:
            raise RuntimeError(
                "LayeredMatplotlibCanvas.request_data_redraw() requires set_compute_stage() "
                "and set_rasterize_stage() to be called first."
            )
        self._pending_state = state
        self._redraw_timer.start(debounce_ms)

    def _submit_data_layer(self) -> None:
        # request_data_redraw() already refused to schedule this timer
        # without both stages set; asserting here (rather than trusting
        # that invariant silently) also satisfies the type checker across
        # the timer-callback boundary.
        assert self._compute_stage is not None
        assert self._rasterize_stage is not None

        scheduler = self._task_scheduler
        if scheduler is None:
            from ..runtime_services import task_scheduler as default_scheduler

            scheduler = default_scheduler

        self._generation += 1
        generation = self._generation
        self.data_layer_started.emit()

        worker = scheduler.submit(self._compute_stage, self._pending_state)
        worker.finished.connect(lambda results, g=generation: self._on_compute_finished(g, results))
        worker.error.connect(lambda message, g=generation: self._on_compute_failed(g, message))

    def _on_compute_finished(self, generation: int, results: dict[str, Any]) -> None:
        if generation != self._generation:
            return  # Superseded by a newer request; this result is stale.
        self._apply_data_layer(results["render_data"])
        self.data_layer_finished.emit()

    def _on_compute_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        logger.error("Data layer compute failed: %s", message)
        if self._crash_reporter is not None:
            self._crash_reporter.report_error(
                f"Data layer compute failed: {message}",
                exception=None,
                plugin_id=self._plugin_id,
                fatal=False,
            )
        self.data_layer_failed.emit(message)

    def _apply_data_layer(self, render_data: Any) -> None:
        def _draw() -> None:
            ax = self.figure.gca()
            ax.clear()
            self._rasterize_stage.rasterize(ax, render_data)
            FigureCanvasQTAgg.draw(self)
            self._bitmap_cache = self.copy_from_bbox(self.figure.bbox)

        self.raster_lock.try_run(
            _draw,
            lambda: self._apply_data_layer(render_data),
            crash_reporter=self._crash_reporter,
            plugin_id=self._plugin_id,
        )

    # ── Overlay layer ───────────────────────────────────────────────

    def request_overlay_redraw(self) -> None:
        """Re-blit the cached data-layer bitmap with no additional artists."""
        self.draw_overlay_artists_blit([])

    def draw_overlay_artists_blit(self, artists: Iterable[Artist]) -> None:
        """Restore the cached data-layer bitmap and blit `artists` on top.

        Cheap and synchronous — never submits to `task_scheduler` and never
        triggers a data-layer recompute. `artists` is materialized to a list
        up front so a lazily-evaluated iterable isn't silently exhausted
        or re-evaluated differently across a lock-busy retry.
        """
        artists = list(artists)

        def _blit() -> None:
            if self._bitmap_cache is None:
                return
            self.restore_region(self._bitmap_cache)
            ax = self.figure.gca()
            for artist in artists:
                ax.draw_artist(artist)
            self.blit(ax.bbox)

        self.raster_lock.try_run(
            _blit,
            lambda: self.draw_overlay_artists_blit(artists),
            crash_reporter=self._crash_reporter,
            plugin_id=self._plugin_id,
        )

    @property
    def bitmap_cache(self) -> Any:
        """The cached bitmap of the last successfully drawn data layer, or None."""
        return self._bitmap_cache

    # ── Lock-guarded Qt/matplotlib entry points ────────────────────

    def paintEvent(self, event: Any) -> None:
        """Paint under a non-blocking `RasterLock` acquire, retrying if a background render task holds it.

        Deliberately not wired to `crash_reporter` — Qt calls this on its own
        schedule (widget show/resize/focus churn), so failures here are
        largely Qt lifecycle noise rather than actual render bugs; those are
        already caught at the source in `_apply_data_layer`/
        `draw_overlay_artists_blit`.
        """
        self.raster_lock.try_run(
            lambda: FigureCanvasQTAgg.paintEvent(self, event),
            self.update,
        )

    def draw(self) -> None:
        """Draw under a non-blocking `RasterLock` acquire, retrying if a background render task holds it.

        Deliberately not wired to `crash_reporter` — see `paintEvent()`.
        """
        self.raster_lock.try_run(
            lambda: FigureCanvasQTAgg.draw(self),
            self.draw,
        )
