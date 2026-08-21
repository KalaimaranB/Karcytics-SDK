"""Compute/rasterize split for expensive, thread-safe-backend-bound rendering.

Splits "produce plot data" (parallelizable — pure numpy/pandas transforms,
density/histogram computation, no rasterization-backend calls) from
"rasterize data to pixels" (short, must run under a `RasterLock`) into two
distinct stages. This lets background threads do all the expensive work
fully in parallel and hold a `RasterLock` only for the brief final draw
call, instead of serializing compute and rasterization together behind one
lock (the mistake this design is meant to avoid repeating).

`RenderComputeStage` is an `AnalysisBase` subclass, so it drops directly
into the existing `AnalysisWorker`/`AnalysisRunnable`/`ITaskScheduler.submit()`
machinery unchanged — this module adds no new dispatch path, only a
narrower contract for what a render-oriented `AnalysisBase.run()` returns.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis import AnalysisBase
from ..state import PluginState
from .lock import RasterLock

if TYPE_CHECKING:
    from karcytics_sdk.interfaces.i_crash_reporter import ICrashReporter
    from karcytics_sdk.interfaces.i_task_scheduler import ITaskScheduler

logger = logging.getLogger(__name__)


@dataclass
class RenderData:
    """Backend-agnostic bag of arrays/params handed from compute to rasterize.

    Plugins subclass this per plot type (e.g. a `FlowRenderData` carrying
    x/y arrays, a density grid, gate geometry, colormap params).

    Must be safe to build off the Qt main thread, and must own or deep-copy
    everything it carries: it is read back on whichever thread rasterizes it
    (usually the Qt main thread), so a reference to something still mutable
    on live UI/controller state is a race, not just bad style. Must never
    hold a live matplotlib `Artist`/`Axes`/`Figure` reference — those are
    only safe to touch under a `RasterLock`, on the thread that owns them.
    """


class RenderComputeStage(AnalysisBase):
    """An `AnalysisBase` whose real work method is `compute()`, not `run()`.

    `run()` is implemented once here so this drops directly into
    `AnalysisWorker`/`AnalysisRunnable`/`ITaskScheduler.submit()` unchanged.
    Subclasses must never touch a `Figure`/`Axes`/`QPainter` or acquire a
    `RasterLock` inside `compute()` — that is the rasterize stage's job
    only, and the entire point of this split is that `compute()` stays
    fully parallel across concurrent background tasks.
    """

    @abstractmethod
    def compute(self, state: PluginState | None) -> RenderData:
        """Produce a `RenderData` from `state`. Must not touch a rasterization backend."""

    def run(self, state: PluginState | None = None) -> dict[str, Any]:
        """`AnalysisBase.run()` implementation: wraps `compute()`'s result."""
        return {"render_data": self.compute(state)}


class RasterizeStage(ABC):
    """The short, lock-held step that turns a `RenderData` into pixels on `target`.

    Does not acquire a `RasterLock` itself — callers are responsible for
    holding the matching lock around each call, so this composes with either
    an interactive canvas (lock held briefly on the Qt main thread) or a
    fully background task (lock held inside that task's own thread).
    """

    @abstractmethod
    def rasterize(self, target: Any, data: RenderData) -> None:
        """Draw `data` onto `target` (e.g. a matplotlib `Axes`)."""


class RasterizeToImageTask(AnalysisBase):
    """Runs a compute stage then a rasterize stage in one background task.

    Holds `raster_lock` only around the rasterize step, not the compute
    step — the point of this class. `figure_factory` builds the offscreen
    drawing target each run; it is injected rather than hardcoded to
    matplotlib so a non-matplotlib rasterize backend can reuse this same
    task shape later. `figure_factory` must return a `(target, canvas)`
    pair where `canvas` supports `.draw()`, `.get_width_height()`, and
    `.buffer_rgba()` (matplotlib's Agg-canvas duck type).
    """

    def __init__(
        self,
        compute_stage: RenderComputeStage,
        rasterize_stage: RasterizeStage,
        raster_lock: RasterLock,
        figure_factory: Callable[[], tuple[Any, Any]],
        plugin_id: str = "unknown",
    ) -> None:
        super().__init__(plugin_id)
        self._compute_stage = compute_stage
        self._rasterize_stage = rasterize_stage
        self._raster_lock = raster_lock
        self._figure_factory = figure_factory

    def run(self, state: PluginState | None = None) -> dict[str, Any]:
        """Compute (parallel, unlocked) then rasterize (short, lock-held)."""
        data = self._compute_stage.compute(state)
        target, canvas = self._figure_factory()
        with self._raster_lock:
            self._rasterize_stage.rasterize(target, data)
            canvas.draw()
            width, height = canvas.get_width_height()
            image_data = bytes(canvas.buffer_rgba())
        return {"image_data": image_data, "width": width, "height": height}


class RenderPipelineController(QObject):
    """Pairs a `RenderComputeStage` with a `RasterizeStage` for ad-hoc use.

    For plugins that want the async-compute/locked-rasterize split without
    adopting `mpl_canvas.LayeredMatplotlibCanvas` wholesale (e.g. a one-off
    "export image" action, or a non-matplotlib rasterize target).

    `request(state)` submits `compute_stage` via `task_scheduler`; when it
    finishes, `rasterize_stage.rasterize(target, render_data)` runs under
    `raster_lock` (with `target` built fresh by `target_factory`), then
    `result_ready` fires with the resulting `RenderData`.

    Generation-gated: if `request()` is called again before a prior
    request's compute finishes, only the most recently requested call's
    result is ever applied — an earlier, slower compute finishing late is
    dropped rather than visually reverting a newer, already-applied result.
    """

    result_ready = pyqtSignal(object)  # emits the RenderData that was rasterized
    compute_failed = pyqtSignal(str)

    def __init__(  # noqa: PLR0913, PLR0917 - a small DI-style constructor; each param is a distinct collaborator, not a group that bundles cleanly
        self,
        compute_stage: RenderComputeStage,
        rasterize_stage: RasterizeStage,
        raster_lock: RasterLock,
        task_scheduler: ITaskScheduler,
        target_factory: Callable[[], Any],
        parent: QObject | None = None,
        crash_reporter: ICrashReporter | None = None,
        plugin_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._compute_stage = compute_stage
        self._rasterize_stage = rasterize_stage
        self._raster_lock = raster_lock
        self._task_scheduler = task_scheduler
        self._target_factory = target_factory
        self._generation = 0
        self._crash_reporter = crash_reporter
        self._plugin_id = plugin_id or getattr(compute_stage, "plugin_id", None)

    def request(self, state: PluginState | None = None) -> None:
        """Submit `compute_stage` for the given state, superseding any in-flight request."""
        self._generation += 1
        generation = self._generation
        worker = self._task_scheduler.submit(self._compute_stage, state)
        worker.finished.connect(lambda results, g=generation: self._on_compute_finished(g, results))
        worker.error.connect(lambda message, g=generation: self._on_compute_failed(g, message))

    def _on_compute_finished(self, generation: int, results: dict[str, Any]) -> None:
        if generation != self._generation:
            return  # Superseded by a newer request; drop this stale result.
        data = results["render_data"]
        target = self._target_factory()
        try:
            with self._raster_lock:
                self._rasterize_stage.rasterize(target, data)
        except Exception as exc:
            logger.exception("Render pipeline rasterize failed")
            if self._crash_reporter is not None:
                self._crash_reporter.report_error(
                    "Render pipeline rasterize failed",
                    exception=exc,
                    plugin_id=self._plugin_id,
                    fatal=False,
                )
            return
        self.result_ready.emit(data)

    def _on_compute_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        logger.error("Render pipeline compute failed: %s", message)
        if self._crash_reporter is not None:
            self._crash_reporter.report_error(
                f"Render pipeline compute failed: {message}",
                exception=None,
                plugin_id=self._plugin_id,
                fatal=False,
            )
        self.compute_failed.emit(message)
