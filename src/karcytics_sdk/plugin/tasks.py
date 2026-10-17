"""Submit a background task and get called back for *that* task only.

The scheduler broadcasts every task's lifecycle (`task_finished`,
`task_error`, `task_progress`, keyed by task id) to every listener. Plugins
used to wrap each submit in nested closures that filtered by id and
disconnected themselves, a pattern copied across flow, western-blot and
cytometrics. Copies drifted: some never disconnected on error or
cancellation, leaking a connected slot per failed task.

`submit_task` does that bookkeeping once. Its watcher is a `QObject` on the
scheduler's (GUI) thread and listens through bound methods, so callbacks run
on the GUI thread and may touch widgets.

    submit_task(
        analyzer,
        state,
        on_finished=self._on_segmented,
        on_error=self._on_failed,
        on_progress=self.progress_bar.setValue,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Slot

from .analysis import AnalysisBase
from .signals import move_to_gui_thread, safe_disconnect
from .state import PluginState

FinishedCallback = Callable[[dict], Any]
ErrorCallback = Callable[[str], Any]
ProgressCallback = Callable[[int], Any]
CancelledCallback = Callable[[], Any]

# Watchers stay alive here until their task ends; nothing else references them.
_active_watchers: set[_TaskWatcher] = set()


class TaskHandle:
    """What `submit_task` returns: the task's id and a way to cancel it."""

    def __init__(self, task_id: str, worker: Any, watcher: _TaskWatcher) -> None:
        self.task_id = task_id
        self._worker = worker
        self._watcher = watcher

    @property
    def done(self) -> bool:
        return self._watcher.done

    def cancel(self) -> None:
        """Ask the task to stop. `on_cancelled` runs if it honours the request."""
        cancel = getattr(self._worker, "cancel", None)
        if callable(cancel):
            cancel()


class _TaskWatcher(QObject):
    def __init__(
        self,
        scheduler: Any,
        on_finished: FinishedCallback | None,
        on_error: ErrorCallback | None,
        on_progress: ProgressCallback | None,
        on_cancelled: CancelledCallback | None,
    ) -> None:
        super().__init__()
        move_to_gui_thread(self)
        self._scheduler = scheduler
        self._on_finished = on_finished
        self._on_error = on_error
        self._on_progress = on_progress
        self._on_cancelled = on_cancelled
        self.task_id: str | None = None
        self.done = False
        # Results that arrive before the task id is known (a task finishing
        # synchronously inside submit) are held and replayed in `bind`.
        self._early: list[tuple[str, str, Any]] = []

        scheduler.task_finished.connect(self._finished)
        scheduler.task_error.connect(self._error)
        scheduler.task_progress.connect(self._progress)

    def bind(self, task_id: str, worker: Any) -> None:
        self.task_id = task_id
        cancelled = getattr(worker, "cancelled", None)
        if cancelled is not None:
            cancelled.connect(self._cancelled)
        early, self._early = self._early, []
        for kind, tid, payload in early:
            if kind == "finished":
                self._finished(tid, payload)
            elif kind == "error":
                self._error(tid, payload)
            else:
                self._progress(tid, payload)

    def _matches(self, kind: str, task_id: str, payload: Any) -> bool:
        if self.done:
            return False
        if self.task_id is None:
            self._early.append((kind, task_id, payload))
            return False
        return task_id == self.task_id

    @Slot(str, object)
    def _finished(self, task_id: str, results: Any) -> None:
        if self._matches("finished", task_id, results):
            self._end()
            if self._on_finished is not None:
                self._on_finished(results if isinstance(results, dict) else {"result": results})

    @Slot(str, str)
    def _error(self, task_id: str, message: str) -> None:
        if self._matches("error", task_id, message):
            self._end()
            if self._on_error is not None:
                self._on_error(message)

    @Slot(str, int)
    def _progress(self, task_id: str, value: int) -> None:
        if self._matches("progress", task_id, value) and self._on_progress is not None:
            self._on_progress(value)

    @Slot()
    def _cancelled(self) -> None:
        if not self.done:
            self._end()
            if self._on_cancelled is not None:
                self._on_cancelled()

    def _end(self) -> None:
        self.done = True
        safe_disconnect(self._scheduler.task_finished, self._finished)
        safe_disconnect(self._scheduler.task_error, self._error)
        safe_disconnect(self._scheduler.task_progress, self._progress)
        _active_watchers.discard(self)


def submit_task(  # noqa: PLR0913 - one keyword-only callback per task outcome
    analyzer: AnalysisBase,
    state: PluginState | None = None,
    *,
    on_finished: FinishedCallback | None = None,
    on_error: ErrorCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_cancelled: CancelledCallback | None = None,
    scheduler: Any = None,
) -> TaskHandle:
    """Submit `analyzer` and call back on the GUI thread for this task only.

    Exactly one of `on_finished` / `on_error` / `on_cancelled` runs, once;
    `on_progress` may run any number of times before it. All connections
    are removed when the task ends, however it ends.

    Args:
        analyzer: The task to run.
        state: Snapshot passed to `analyzer.run`.
        on_finished: Called with the task's result dict.
        on_error: Called with the error message.
        on_progress: Called with 0-100 as the task reports progress.
        on_cancelled: Called if the task stops because it was cancelled.
        scheduler: Defaults to this process's `task_scheduler`.
    """
    if scheduler is None:
        from .runtime_services import task_scheduler as scheduler

    watcher = _TaskWatcher(scheduler, on_finished, on_error, on_progress, on_cancelled)
    _active_watchers.add(watcher)
    try:
        worker = scheduler.submit(analyzer, state)
    except Exception:
        watcher._end()
        raise
    task_id = str(getattr(worker, "task_id", ""))
    watcher.bind(task_id, worker)
    return TaskHandle(task_id, worker, watcher)
