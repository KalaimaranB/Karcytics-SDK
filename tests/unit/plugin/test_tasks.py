"""submit_task: per-task callbacks on the GUI thread, always cleaned up."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from karcytics_sdk.plugin import AnalysisBase
from karcytics_sdk.plugin.managed_task import FunctionalTask
from karcytics_sdk.plugin.runtime_services import LocalTaskScheduler
from karcytics_sdk.plugin.tasks import _active_watchers, submit_task


class _Task(AnalysisBase):
    def __init__(self, *, result=None, fail=False, progress=(), wait: threading.Event | None = None):
        super().__init__("test")
        self._result = result if result is not None else {"value": 1}
        self._fail = fail
        self._progress = progress
        self._wait = wait

    def run(self, state=None):
        for value in self._progress:
            self.signals.analysis_progress.emit(value)
        if self._wait is not None:
            self._wait.wait(5)
        if self._fail:
            raise ValueError("boom")
        return self._result


def _receivers(scheduler: LocalTaskScheduler) -> int:
    return sum(
        scheduler.receivers(sig)
        for sig in ("2task_finished(QString,PyObject)", "2task_error(QString,QString)", "2task_progress(QString,int)")
    )


def test_on_finished_gets_this_tasks_results_on_the_gui_thread(qtbot):
    scheduler = LocalTaskScheduler()
    got = []
    threads = []

    def on_finished(results):
        got.append(results)
        threads.append(QThread.currentThread())

    submit_task(_Task(result={"v": 7}), scheduler=scheduler, on_finished=on_finished)

    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got == [{"v": 7}]
    assert threads[0] is QApplication.instance().thread()


def test_ignores_other_tasks_results(qtbot):
    scheduler = LocalTaskScheduler()
    mine, theirs = [], []

    submit_task(_Task(result={"who": "theirs"}), scheduler=scheduler, on_finished=theirs.append)
    submit_task(_Task(result={"who": "mine"}), scheduler=scheduler, on_finished=mine.append)

    qtbot.waitUntil(lambda: bool(mine) and bool(theirs), timeout=5000)
    assert mine == [{"who": "mine"}]
    assert theirs == [{"who": "theirs"}]


def test_on_error_runs_once_and_on_finished_never(qtbot):
    scheduler = LocalTaskScheduler()
    errors, finished = [], []

    submit_task(_Task(fail=True), scheduler=scheduler, on_finished=finished.append, on_error=errors.append)

    qtbot.waitUntil(lambda: bool(errors), timeout=5000)
    qtbot.wait(50)
    assert errors == ["boom"]
    assert finished == []


def test_progress_is_forwarded_before_finished(qtbot):
    scheduler = LocalTaskScheduler()
    events = []

    submit_task(
        _Task(progress=(10, 60)),
        scheduler=scheduler,
        on_progress=lambda v: events.append(("progress", v)),
        on_finished=lambda r: events.append(("finished", r)),
    )

    qtbot.waitUntil(lambda: any(e[0] == "finished" for e in events), timeout=5000)
    assert events == [("progress", 10), ("progress", 60), ("finished", {"value": 1})]


def test_every_connection_is_removed_however_the_task_ends(qtbot):
    scheduler = LocalTaskScheduler()
    baseline = _receivers(scheduler)
    done = []

    submit_task(_Task(), scheduler=scheduler, on_finished=done.append)
    submit_task(_Task(fail=True), scheduler=scheduler, on_error=done.append)

    qtbot.waitUntil(lambda: len(done) == 2, timeout=5000)
    assert _receivers(scheduler) == baseline
    assert not _active_watchers


def test_cancel_calls_on_cancelled_and_cleans_up(qtbot):
    scheduler = LocalTaskScheduler()
    baseline = _receivers(scheduler)
    gate = threading.Event()
    cancelled, finished = [], []

    handle = submit_task(
        _Task(wait=gate),
        scheduler=scheduler,
        on_cancelled=lambda: cancelled.append(True),
        on_finished=finished.append,
    )
    handle.cancel()
    gate.set()

    qtbot.waitUntil(lambda: bool(cancelled), timeout=5000)
    assert handle.done
    assert finished == []
    assert _receivers(scheduler) == baseline


def test_functional_task_gives_progress_callback_to_functions_that_take_one(qtbot):
    scheduler = LocalTaskScheduler()
    progress, finished = [], []

    def download(progress_callback):
        progress_callback(30)
        time.sleep(0.01)
        return {"path": "/models/x"}

    submit_task(
        FunctionalTask(download, "test"),
        scheduler=scheduler,
        on_progress=progress.append,
        on_finished=finished.append,
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=5000)
    assert progress == [30]
    assert finished == [{"path": "/models/x"}]


def test_functional_task_still_calls_zero_argument_functions(qtbot):
    scheduler = LocalTaskScheduler()
    finished = []

    submit_task(FunctionalTask(lambda: 42, "test"), scheduler=scheduler, on_finished=finished.append)

    qtbot.waitUntil(lambda: bool(finished), timeout=5000)
    assert finished == [{"result": 42, "status": "success"}]
