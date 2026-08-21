"""Unit tests for PluginBase.create_worker/start_worker/create_render_pipeline.

These close a doc/code drift: docs/Dev_Handbook.md documents
`self.create_worker(engine, self.state)` / `self.start_worker(worker)` as
part of PluginBase's public API, but PluginBase previously had no such
methods at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from karcytics_sdk.plugin.analysis import AnalysisBase, AnalysisWorker
from karcytics_sdk.plugin.base import _IN_PROCESS, PluginBase
from karcytics_sdk.plugin.rendering.pipeline import RasterizeStage, RenderComputeStage, RenderData
from karcytics_sdk.plugin.state import PluginState


class _FakeAnalysisWorker:
    """Stand-in for AnalysisWorker: records signal connections, lets the test fire them synchronously."""

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


def _synchronous_fake_scheduler():
    """A fake ITaskScheduler that doesn't touch the real QThreadPool at all —
    avoids relying on the process-wide thread pool, which under a full
    test-suite run can be contended by unrelated tests' background tasks.
    """
    scheduler = MagicMock()
    workers = []

    def _submit(analyzer, state=None):
        worker = _FakeAnalysisWorker()
        workers.append(worker)
        return worker

    scheduler.submit.side_effect = _submit
    return scheduler, workers


@dataclass
class _DummyState(PluginState):
    value: int = 0


class _DummyPlugin(PluginBase):
    def __init__(self):
        super().__init__(plugin_id="test_plugin")
        self.state = _DummyState()

    def get_state(self) -> PluginState:
        return self.state

    def set_state(self, state: PluginState) -> None:
        self.state = state


class _DummyAnalyzer(AnalysisBase):
    def __init__(self, *, fail: bool = False):
        super().__init__("test_plugin")
        self.fail = fail

    def run(self, state=None):
        if self.fail:
            raise ValueError("boom")
        return {"answer": 42}


@dataclass
class _FakeRenderData(RenderData):
    value: int = 0


class _DummyComputeStage(RenderComputeStage):
    def compute(self, state=None) -> _FakeRenderData:
        return _FakeRenderData(value=1)


class _DummyRasterizeStage(RasterizeStage):
    def __init__(self):
        self.calls = []

    def rasterize(self, target, data):
        self.calls.append((target, data))


class TestCreateWorker:
    def test_returns_an_analysis_worker_wrapping_the_analyzer(self, qapp):
        plugin = _DummyPlugin()
        analyzer = _DummyAnalyzer()

        worker = plugin.create_worker(analyzer, plugin.state)

        assert isinstance(worker, AnalysisWorker)
        assert worker.analyzer is analyzer
        assert worker.state is plugin.state

    def test_worker_is_not_dispatched_until_start_worker_is_called(self, qapp):
        """create_worker() alone must not run anything — signals must be
        connectable before the analyzer starts running on another thread.
        """
        plugin = _DummyPlugin()
        analyzer = _DummyAnalyzer()
        results = []

        worker = plugin.create_worker(analyzer, None)
        worker.finished.connect(results.append)

        assert results == []  # nothing has run yet


class TestStartWorker:
    def test_dispatches_the_exact_worker_instance_and_it_completes(self, qapp, qtbot):
        plugin = _DummyPlugin()
        worker = plugin.create_worker(_DummyAnalyzer())

        with qtbot.waitSignal(worker.finished, timeout=8000) as blocker:
            returned = plugin.start_worker(worker)

        assert returned is worker  # the caller's signal connections stay valid
        assert blocker.args == [{"answer": 42}]

    def test_signals_connected_before_start_worker_are_honored(self, qapp, qtbot):
        plugin = _DummyPlugin()
        worker = plugin.create_worker(_DummyAnalyzer())
        received = []
        worker.finished.connect(received.append)

        with qtbot.waitSignal(worker.finished, timeout=8000):
            plugin.start_worker(worker)

        assert received == [{"answer": 42}]

    def test_error_path_emits_error_not_finished(self, qapp, qtbot):
        plugin = _DummyPlugin()
        worker = plugin.create_worker(_DummyAnalyzer(fail=True))

        with qtbot.waitSignal(worker.error, timeout=8000) as blocker:
            plugin.start_worker(worker)

        assert "boom" in blocker.args[0]


class TestCreateRenderPipeline:
    def test_builds_a_controller_wired_to_the_given_stages(self, qapp):
        plugin = _DummyPlugin()
        rasterize_stage = _DummyRasterizeStage()
        target = object()
        scheduler, workers = _synchronous_fake_scheduler()

        controller = plugin.create_render_pipeline(
            compute_stage=_DummyComputeStage("test_plugin"),
            rasterize_stage=rasterize_stage,
            target_factory=lambda: target,
            task_scheduler=scheduler,
        )
        received = []
        controller.result_ready.connect(received.append)

        controller.request(state=None)
        assert len(workers) == 1
        workers[0].emit_finished({"render_data": _FakeRenderData(value=1)})

        assert rasterize_stage.calls == [(target, _FakeRenderData(value=1))]
        assert received == [_FakeRenderData(value=1)]

    def test_defaults_to_the_process_wide_task_scheduler_when_none_is_given(self, qapp):
        from karcytics_sdk.plugin.runtime_services import task_scheduler as default_task_scheduler

        plugin = _DummyPlugin()

        controller = plugin.create_render_pipeline(
            compute_stage=_DummyComputeStage("test_plugin"),
            rasterize_stage=_DummyRasterizeStage(),
            target_factory=lambda: object(),
        )

        assert controller._task_scheduler is default_task_scheduler

    def test_passes_the_plugins_crash_reporter_and_plugin_id_through(self, qapp):
        plugin = _DummyPlugin()
        scheduler, _workers = _synchronous_fake_scheduler()

        controller = plugin.create_render_pipeline(
            compute_stage=_DummyComputeStage("test_plugin"),
            rasterize_stage=_DummyRasterizeStage(),
            target_factory=lambda: object(),
            task_scheduler=scheduler,
        )

        assert controller._crash_reporter is plugin.crash_reporter
        assert controller._plugin_id == "test_plugin"


class TestCrashReporter:
    """Verifies PluginBase resolves self.crash_reporter per the in-process-vs-isolated
    split: `None` for in-process plugins (they already reach Sentry for free via
    logger.exception() + the Hub's AutoReportHandler), the isolated
    DiagnosticsForwarder otherwise (their only route back to the Hub at all).

    This SDK's own test environment never has `karcytics.ui.theme` importable
    (that package lives in the Hub, not the SDK), so `_IN_PROCESS` is always
    False here — the same condition a real isolated flow-cytometry plugin
    process runs under. Assertions branch on `_IN_PROCESS` so this test keeps
    working correctly if that ever changes (e.g. run inside the Hub's own
    test suite where the Hub package really is importable).
    """

    def test_crash_reporter_matches_the_in_process_vs_isolated_split(self, qapp):
        from karcytics_sdk.plugin.runtime_services import diagnostics

        plugin = _DummyPlugin()

        if _IN_PROCESS:
            assert plugin.crash_reporter is None
        else:
            assert plugin.crash_reporter is diagnostics
