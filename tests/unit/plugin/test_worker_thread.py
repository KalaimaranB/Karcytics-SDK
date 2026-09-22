"""Unit tests for OneShotWorkerThread.

Covers the crash this class exists to prevent: a QThread with no Qt parent
being garbage-collected while still running (`QThread: Destroyed while
thread '' is still running`). See karcytics_plugins.flow_cytometry's
statistics_explorer.ComputeWorker and comparisons.worker.ComparisonsWorker,
which both carried this bug independently before being migrated onto this
base class.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject

from karcytics_sdk.plugin.worker_thread import OneShotWorkerThread


class _EchoWorker(OneShotWorkerThread):
    def __init__(self, value, parent: QObject):
        super().__init__(parent)
        self._value = value

    def _execute(self):
        return self._value


class _FailingWorker(OneShotWorkerThread):
    def _execute(self):
        raise ValueError("boom")


class TestOneShotWorkerThread:
    def test_requires_a_qt_parent_to_anchor_its_lifetime(self, qapp):
        owner = QObject()
        worker = _EchoWorker(42, owner)

        assert worker.parent() is owner

    def test_success_path_emits_finished_ok_with_the_result(self, qapp, qtbot):
        owner = QObject()
        worker = _EchoWorker({"answer": 42}, owner)

        with qtbot.waitSignal(worker.finished_ok, timeout=8000) as blocker:
            worker.start()

        assert blocker.args == [{"answer": 42}]

    def test_error_path_emits_finished_err_not_finished_ok(self, qapp, qtbot):
        owner = QObject()
        worker = _FailingWorker(owner)
        finished_ok_calls = []
        worker.finished_ok.connect(finished_ok_calls.append)

        with qtbot.waitSignal(worker.finished_err, timeout=8000) as blocker:
            worker.start()

        assert "boom" in blocker.args[0]
        assert finished_ok_calls == []

    def test_stop_and_wait_blocks_until_a_running_worker_finishes(self, qapp, qtbot):
        owner = QObject()
        worker = _EchoWorker(1, owner)

        worker.start()
        worker.stop_and_wait(timeout_ms=8000)

        assert not worker.isRunning()

    def test_stop_and_wait_is_a_no_op_when_never_started(self, qapp):
        owner = QObject()
        worker = _EchoWorker(1, owner)

        worker.stop_and_wait(timeout_ms=1000)  # must not raise/hang

        assert not worker.isRunning()
