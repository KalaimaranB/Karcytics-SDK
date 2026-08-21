"""Unit tests for karcytics_sdk.plugin.rendering.pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from karcytics_sdk.plugin.rendering.lock import RasterLock
from karcytics_sdk.plugin.rendering.pipeline import (
    RasterizeStage,
    RasterizeToImageTask,
    RenderComputeStage,
    RenderData,
    RenderPipelineController,
)


@dataclass
class _FakeRenderData(RenderData):
    value: int = 0


class _FakeComputeStage(RenderComputeStage):
    def __init__(self, value: int = 42, plugin_id: str = "test"):
        super().__init__(plugin_id)
        self.value = value
        self.compute_calls = 0

    def compute(self, state=None) -> _FakeRenderData:
        self.compute_calls += 1
        return _FakeRenderData(value=self.value)


class _FailingComputeStage(RenderComputeStage):
    def compute(self, state=None):
        raise ValueError("compute boom")


class _FakeRasterizeStage(RasterizeStage):
    def __init__(self):
        self.rasterize_calls = []

    def rasterize(self, target, data):
        self.rasterize_calls.append((target, data))


class _FakeCanvas:
    """Duck-types the (draw, get_width_height, buffer_rgba) contract RasterizeToImageTask expects."""

    def draw(self):
        pass

    def get_width_height(self):
        return (10, 20)

    def buffer_rgba(self):
        return bytearray(b"\x00" * (10 * 20 * 4))


class TestRenderComputeStage:
    def test_run_wraps_compute_result_in_render_data_key(self):
        stage = _FakeComputeStage(value=7)

        results = stage.run(state=None)

        assert results == {"render_data": _FakeRenderData(value=7)}

    def test_compute_stage_cannot_be_instantiated_without_compute(self):
        with pytest.raises(TypeError):
            RenderComputeStage("test")  # abstract


class TestRasterizeToImageTask:
    def test_run_computes_then_rasterizes_under_the_lock_and_returns_image_dict(self):
        compute_stage = _FakeComputeStage(value=99)
        rasterize_stage = _FakeRasterizeStage()
        lock = RasterLock("test-image-task")
        fake_canvas = _FakeCanvas()
        target = object()

        task = RasterizeToImageTask(
            compute_stage=compute_stage,
            rasterize_stage=rasterize_stage,
            raster_lock=lock,
            figure_factory=lambda: (target, fake_canvas),
        )

        results = task.run(state=None)

        assert compute_stage.compute_calls == 1
        assert rasterize_stage.rasterize_calls == [(target, _FakeRenderData(value=99))]
        assert results["width"] == 10
        assert results["height"] == 20
        assert results["image_data"] == bytes(fake_canvas.buffer_rgba())
        # Lock must be released again after run() — not left held.
        assert lock.acquire(blocking=False) is True
        lock.release()

    def test_run_holds_the_lock_only_around_rasterize_not_compute(self):
        """The whole point of the compute/rasterize split: compute() must be
        able to run even while the lock is held by someone else, since it
        never touches it, and rasterize() must genuinely hold the lock.

        RLock is reentrant for the SAME thread, so proving the lock is held
        during rasterize() requires checking from a DIFFERENT thread than
        the one running task.run().
        """
        import threading

        compute_stage = _FakeComputeStage(value=1)
        lock = RasterLock("test")
        inside_rasterize = threading.Event()
        proceed = threading.Event()

        class _BlockingRasterizeStage(RasterizeStage):
            def rasterize(self, target, data):
                inside_rasterize.set()
                proceed.wait(timeout=2)

        task = RasterizeToImageTask(
            compute_stage=compute_stage,
            rasterize_stage=_BlockingRasterizeStage(),
            raster_lock=lock,
            figure_factory=lambda: (object(), _FakeCanvas()),
        )

        # compute() itself must not require the lock at all.
        assert compute_stage.compute(None) == _FakeRenderData(value=1)

        runner = threading.Thread(target=task.run, kwargs={"state": None})
        runner.start()
        try:
            assert inside_rasterize.wait(timeout=2)
            # From this (different) thread's perspective, the lock must be unavailable right now.
            assert lock.acquire(blocking=False) is False
        finally:
            proceed.set()
            runner.join(timeout=2)


def _make_fake_scheduler():
    """A synchronous fake ITaskScheduler: submit() runs the analyzer immediately
    and returns a MagicMock standing in for the AnalysisWorker, with
    `finished`/`error` signals pre-wired as simple callback registries.
    """

    class _FakeWorker:
        def __init__(self):
            self._finished_cbs = []
            self._error_cbs = []
            self.finished = MagicMock()
            self.finished.connect = self._finished_cbs.append
            self.error = MagicMock()
            self.error.connect = self._error_cbs.append

        def emit_finished(self, results):
            for cb in self._finished_cbs:
                cb(results)

        def emit_error(self, message):
            for cb in self._error_cbs:
                cb(message)

    scheduler = MagicMock()
    workers = []

    def _submit(analyzer, state=None):
        worker = _FakeWorker()
        workers.append((analyzer, state, worker))
        return worker

    scheduler.submit.side_effect = _submit
    return scheduler, workers


class TestRenderPipelineController:
    def _make_scheduler(self):
        return _make_fake_scheduler()

    def test_request_submits_compute_stage_and_applies_result_on_finish(self, qapp):
        scheduler, workers = self._make_scheduler()
        compute_stage = _FakeComputeStage(value=5)
        rasterize_stage = _FakeRasterizeStage()
        lock = RasterLock("test")
        target = object()

        controller = RenderPipelineController(
            compute_stage=compute_stage,
            rasterize_stage=rasterize_stage,
            raster_lock=lock,
            task_scheduler=scheduler,
            target_factory=lambda: target,
        )
        received = []
        controller.result_ready.connect(received.append)

        controller.request(state=None)
        assert len(workers) == 1
        _, _, worker = workers[0]
        worker.emit_finished({"render_data": _FakeRenderData(value=5)})

        assert rasterize_stage.rasterize_calls == [(target, _FakeRenderData(value=5))]
        assert received == [_FakeRenderData(value=5)]

    def test_a_stale_result_from_a_superseded_request_is_dropped(self, qapp):
        """Two rapid requests; the FIRST one's compute happens to finish
        LAST (e.g. it was slower). Only the second (most recent) request's
        result may ever be applied — an out-of-order stale result must not
        visually revert a newer, already-applied one.
        """
        scheduler, workers = self._make_scheduler()
        rasterize_stage = _FakeRasterizeStage()
        lock = RasterLock("test")
        target = object()

        controller = RenderPipelineController(
            compute_stage=_FakeComputeStage(),
            rasterize_stage=rasterize_stage,
            raster_lock=lock,
            task_scheduler=scheduler,
            target_factory=lambda: target,
        )
        received = []
        controller.result_ready.connect(received.append)

        controller.request(state="first")
        controller.request(state="second")
        assert len(workers) == 2
        _, _, first_worker = workers[0]
        _, _, second_worker = workers[1]

        # Second (newer) request's compute finishes first...
        second_worker.emit_finished({"render_data": _FakeRenderData(value=2)})
        # ...then the stale first request finishes late.
        first_worker.emit_finished({"render_data": _FakeRenderData(value=1)})

        assert rasterize_stage.rasterize_calls == [(target, _FakeRenderData(value=2))]
        assert received == [_FakeRenderData(value=2)]

    def test_compute_failure_emits_compute_failed_not_result_ready(self, qapp):
        scheduler, workers = self._make_scheduler()
        controller = RenderPipelineController(
            compute_stage=_FailingComputeStage("test"),
            rasterize_stage=_FakeRasterizeStage(),
            raster_lock=RasterLock("test"),
            task_scheduler=scheduler,
            target_factory=lambda: object(),
        )
        failures = []
        results = []
        controller.compute_failed.connect(failures.append)
        controller.result_ready.connect(results.append)

        controller.request(state=None)
        _, _, worker = workers[0]
        worker.emit_error("boom")

        assert failures == ["boom"]
        assert results == []

    def test_a_stale_error_from_a_superseded_request_is_ignored(self, qapp):
        scheduler, workers = self._make_scheduler()
        controller = RenderPipelineController(
            compute_stage=_FakeComputeStage(),
            rasterize_stage=_FakeRasterizeStage(),
            raster_lock=RasterLock("test"),
            task_scheduler=scheduler,
            target_factory=lambda: object(),
        )
        failures = []
        controller.compute_failed.connect(failures.append)

        controller.request(state="first")
        controller.request(state="second")
        _, _, first_worker = workers[0]

        first_worker.emit_error("stale failure")

        assert failures == []


class TestRenderPipelineControllerCrashReporting:
    def test_no_crash_reporter_by_default_rasterize_failure_still_just_logs(self, qapp):
        scheduler, workers = _make_fake_scheduler()

        class _FailingRasterizeStage(RasterizeStage):
            def rasterize(self, target, data):
                raise ValueError("rasterize boom")

        controller = RenderPipelineController(
            compute_stage=_FakeComputeStage(),
            rasterize_stage=_FailingRasterizeStage(),
            raster_lock=RasterLock("test"),
            task_scheduler=scheduler,
            target_factory=lambda: object(),
        )
        results = []
        controller.result_ready.connect(results.append)

        controller.request(state=None)
        _, _, worker = workers[0]
        worker.emit_finished({"render_data": _FakeRenderData(value=1)})  # must not raise

        assert results == []  # rasterize failed, so no result was ever applied

    def test_reports_to_crash_reporter_when_rasterize_fails(self, qapp):
        scheduler, workers = _make_fake_scheduler()
        reporter = MagicMock()

        class _FailingRasterizeStage(RasterizeStage):
            def rasterize(self, target, data):
                raise ValueError("rasterize boom")

        controller = RenderPipelineController(
            compute_stage=_FakeComputeStage(plugin_id="flow_cytometry"),
            rasterize_stage=_FailingRasterizeStage(),
            raster_lock=RasterLock("test"),
            task_scheduler=scheduler,
            target_factory=lambda: object(),
            crash_reporter=reporter,
        )

        controller.request(state=None)
        _, _, worker = workers[0]
        worker.emit_finished({"render_data": _FakeRenderData(value=1)})

        reporter.report_error.assert_called_once()
        args, kwargs = reporter.report_error.call_args
        assert "Render pipeline rasterize failed" in args[0]
        assert isinstance(kwargs["exception"], ValueError)
        assert kwargs["plugin_id"] == "flow_cytometry"
        assert kwargs["fatal"] is False

    def test_reports_to_crash_reporter_when_compute_fails(self, qapp):
        scheduler, workers = _make_fake_scheduler()
        reporter = MagicMock()

        controller = RenderPipelineController(
            compute_stage=_FailingComputeStage("flow_cytometry"),
            rasterize_stage=_FakeRasterizeStage(),
            raster_lock=RasterLock("test"),
            task_scheduler=scheduler,
            target_factory=lambda: object(),
            crash_reporter=reporter,
            plugin_id="flow_cytometry",
        )

        controller.request(state=None)
        _, _, worker = workers[0]
        worker.emit_error("boom")

        reporter.report_error.assert_called_once_with(
            "Render pipeline compute failed: boom",
            exception=None,
            plugin_id="flow_cytometry",
            fatal=False,
        )

    def test_plugin_id_defaults_to_the_compute_stages_plugin_id(self, qapp):
        scheduler, _workers = _make_fake_scheduler()
        reporter = MagicMock()

        controller = RenderPipelineController(
            compute_stage=_FakeComputeStage(plugin_id="flow_cytometry"),
            rasterize_stage=_FakeRasterizeStage(),
            raster_lock=RasterLock("test"),
            task_scheduler=scheduler,
            target_factory=lambda: object(),
            crash_reporter=reporter,
        )

        assert controller._plugin_id == "flow_cytometry"
