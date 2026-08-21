"""Unit tests for karcytics_sdk.plugin.rendering.mpl_canvas."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from matplotlib.figure import Figure

from karcytics_sdk.plugin.rendering.lock import RasterLock
from karcytics_sdk.plugin.rendering.mpl_canvas import LayeredMatplotlibCanvas
from karcytics_sdk.plugin.rendering.pipeline import RasterizeStage, RenderComputeStage, RenderData


@dataclass
class _FakeRenderData(RenderData):
    value: int = 0


class _FakeComputeStage(RenderComputeStage):
    def compute(self, state=None) -> _FakeRenderData:
        return _FakeRenderData(value=1)


class _RecordingRasterizeStage(RasterizeStage):
    def __init__(self):
        self.calls = []

    def rasterize(self, target, data):
        self.calls.append((target, data))


class _FakeWorker:
    """Stand-in for AnalysisWorker: records signal connections, lets the test fire them manually."""

    def __init__(self):
        self._finished_cbs = []
        self._error_cbs = []
        self.finished = MagicMock()
        self.finished.connect = self._finished_cbs.append
        self.error = MagicMock()
        self.error.connect = self._error_cbs.append

    def emit_finished(self, results):
        for cb in list(self._finished_cbs):
            cb(results)

    def emit_error(self, message):
        for cb in list(self._error_cbs):
            cb(message)


def _fake_scheduler():
    scheduler = MagicMock()
    workers = []

    def _submit(analyzer, state=None):
        worker = _FakeWorker()
        workers.append(worker)
        return worker

    scheduler.submit.side_effect = _submit
    return scheduler, workers


@pytest.fixture
def canvas(qapp):
    fig = Figure()
    c = LayeredMatplotlibCanvas(fig, raster_lock=RasterLock("test-canvas"))
    yield c
    c.deleteLater()


class TestDataLayer:
    def test_request_data_redraw_without_stages_raises(self, canvas):
        with pytest.raises(RuntimeError):
            canvas.request_data_redraw()

    def test_debounced_request_submits_exactly_once(self, canvas, qtbot):
        scheduler, workers = _fake_scheduler()
        canvas._task_scheduler = scheduler
        canvas.set_compute_stage(_FakeComputeStage("test"))
        canvas.set_rasterize_stage(_RecordingRasterizeStage())

        canvas.request_data_redraw(debounce_ms=10)
        canvas.request_data_redraw(debounce_ms=10)  # rapid second call must collapse into one submit
        canvas.request_data_redraw(debounce_ms=10)

        qtbot.wait(60)
        assert len(workers) == 1

    def test_finished_result_is_rasterized_and_signals_fire_in_order(self, canvas, qtbot):
        scheduler, workers = _fake_scheduler()
        canvas._task_scheduler = scheduler
        rasterize_stage = _RecordingRasterizeStage()
        canvas.set_compute_stage(_FakeComputeStage("test"))
        canvas.set_rasterize_stage(rasterize_stage)

        events = []
        canvas.data_layer_started.connect(lambda: events.append("started"))
        canvas.data_layer_finished.connect(lambda: events.append("finished"))

        canvas.request_data_redraw(debounce_ms=10)
        qtbot.waitUntil(lambda: len(workers) == 1, timeout=1000)
        assert events == ["started"]

        workers[0].emit_finished({"render_data": _FakeRenderData(value=1)})

        assert events == ["started", "finished"]
        assert len(rasterize_stage.calls) == 1
        assert rasterize_stage.calls[0][1] == _FakeRenderData(value=1)
        assert canvas.bitmap_cache is not None

    def test_a_stale_compute_result_from_a_superseded_request_is_dropped(self, canvas, qtbot):
        """Regression guard for the out-of-order-async-result hazard: a slow
        compute for an earlier request must not overwrite a newer request's
        already-applied result.
        """
        scheduler, workers = _fake_scheduler()
        canvas._task_scheduler = scheduler
        rasterize_stage = _RecordingRasterizeStage()
        canvas.set_compute_stage(_FakeComputeStage("test"))
        canvas.set_rasterize_stage(rasterize_stage)

        canvas.request_data_redraw(debounce_ms=10)
        qtbot.waitUntil(lambda: len(workers) == 1, timeout=1000)
        # A second request starts its own debounce+submit cycle, bumping the generation.
        canvas.request_data_redraw(debounce_ms=10)
        qtbot.waitUntil(lambda: len(workers) == 2, timeout=1000)

        # Newer request's compute finishes first.
        workers[1].emit_finished({"render_data": _FakeRenderData(value=2)})
        # Stale, older request's compute finishes late.
        workers[0].emit_finished({"render_data": _FakeRenderData(value=1)})

        assert [call[1] for call in rasterize_stage.calls] == [_FakeRenderData(value=2)]

    def test_compute_failure_emits_data_layer_failed(self, canvas, qtbot):
        scheduler, workers = _fake_scheduler()
        canvas._task_scheduler = scheduler
        canvas.set_compute_stage(_FakeComputeStage("test"))
        canvas.set_rasterize_stage(_RecordingRasterizeStage())

        failures = []
        canvas.data_layer_failed.connect(failures.append)

        canvas.request_data_redraw(debounce_ms=10)
        qtbot.waitUntil(lambda: len(workers) == 1, timeout=1000)
        workers[0].emit_error("boom")

        assert failures == ["boom"]


class TestCrashReporting:
    @pytest.fixture
    def canvas_with_reporter(self, qapp):
        reporter = MagicMock()
        fig = Figure()
        c = LayeredMatplotlibCanvas(
            fig,
            raster_lock=RasterLock("test-canvas-crash"),
            crash_reporter=reporter,
            plugin_id="flow_cytometry",
        )
        yield c, reporter
        c.deleteLater()

    def test_compute_failure_reports_to_crash_reporter(self, canvas_with_reporter, qtbot):
        canvas, reporter = canvas_with_reporter
        scheduler, workers = _fake_scheduler()
        canvas._task_scheduler = scheduler
        canvas.set_compute_stage(_FakeComputeStage("flow_cytometry"))
        canvas.set_rasterize_stage(_RecordingRasterizeStage())

        canvas.request_data_redraw(debounce_ms=10)
        qtbot.waitUntil(lambda: len(workers) == 1, timeout=1000)
        workers[0].emit_error("boom")

        reporter.report_error.assert_called_once_with(
            "Data layer compute failed: boom",
            exception=None,
            plugin_id="flow_cytometry",
            fatal=False,
        )

    def test_rasterize_failure_reports_to_crash_reporter(self, canvas_with_reporter, qtbot):
        canvas, reporter = canvas_with_reporter
        scheduler, workers = _fake_scheduler()
        canvas._task_scheduler = scheduler
        canvas.set_compute_stage(_FakeComputeStage("flow_cytometry"))

        class _FailingRasterizeStage(RasterizeStage):
            def rasterize(self, target, data):
                raise ValueError("rasterize boom")

        canvas.set_rasterize_stage(_FailingRasterizeStage())

        canvas.request_data_redraw(debounce_ms=10)
        qtbot.waitUntil(lambda: len(workers) == 1, timeout=1000)
        workers[0].emit_finished({"render_data": _FakeRenderData(value=1)})  # must not raise

        reporter.report_error.assert_called_once()
        args, kwargs = reporter.report_error.call_args
        assert "matplotlib-agg" in args[0] or "raster" in args[0].lower()
        assert isinstance(kwargs["exception"], ValueError)
        assert kwargs["plugin_id"] == "flow_cytometry"

    def test_no_crash_reporter_by_default(self, qapp):
        fig = Figure()
        c = LayeredMatplotlibCanvas(fig, raster_lock=RasterLock("test-canvas-default"))
        assert c._crash_reporter is None
        c.deleteLater()


class TestOverlayLayer:
    def test_draw_overlay_artists_blit_is_a_noop_without_a_cached_bitmap(self, canvas):
        # No data layer has ever been applied, so there's nothing to restore/blit onto.
        canvas.draw_overlay_artists_blit([])  # must not raise

    def test_request_overlay_redraw_does_not_touch_the_task_scheduler(self, canvas):
        scheduler = MagicMock()
        canvas._task_scheduler = scheduler

        canvas.request_overlay_redraw()

        scheduler.submit.assert_not_called()
