"""Reusable QThread base for one-shot background work with typed result signals.

Plugins that need to run a single synchronous computation off the UI thread
(a stats crunch, a plot render) and report back a result-or-error commonly
reach for a bespoke ``QThread`` subclass. Two independent implementations of
that pattern (flow-cytometry's statistics and comparisons workers) both
carried the same latent crash: the ``QThread`` was constructed with no Qt
parent, so its only lifetime anchor was a plain Python attribute on the
owning widget. If that attribute ever got overwritten or the owning widget
was garbage-collected while the thread was still executing, CPython would
finalize the still-running ``QThread`` object -- which Qt treats as fatal
("QThread: Destroyed while thread '' is still running"), crashing the
process.

``OneShotWorkerThread`` centralizes the fix: a required Qt parent anchors
the thread to the owning widget's C++ object lifetime, and
:meth:`stop_and_wait` gives callers a single, safe way to block on
completion from a teardown path (e.g. a ``destroyed`` handler) instead of
letting Python's garbage collector race the OS thread.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)


class OneShotWorkerThread(QThread):
    """Base class for a QThread that runs one job once and reports back.

    Subclasses implement :meth:`_execute` with their computation; ``run()``
    is provided and takes care of catching exceptions and emitting
    ``finished_ok``/``finished_err``.

    Example:
        >>> class ComputeWorker(OneShotWorkerThread):
        ...     def __init__(self, owner, parent: QObject):
        ...         super().__init__(parent)
        ...         self._owner = owner
        ...
        ...     def _execute(self):
        ...         return self._owner.compute_stats()
        >>> worker = ComputeWorker(self, self)
        >>> worker.finished_ok.connect(self._on_done)
        >>> worker.finished_err.connect(self._on_error)
        >>> worker.start()

    A Qt ``parent`` is required (not optional) -- omitting it reintroduces
    exactly the crash this class exists to prevent.
    """

    finished_ok = pyqtSignal(object)
    finished_err = pyqtSignal(str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    @abstractmethod
    def _execute(self) -> Any:
        """Perform the background work and return its result.

        Runs on the worker thread. Must not touch Qt widgets directly.
        """
        raise NotImplementedError

    def run(self) -> None:
        try:
            result = self._execute()
        except Exception as exc:  # noqa: BLE001 - reported via signal, not re-raised
            logger.exception("%s failed in background thread", type(self).__name__)
            self.finished_err.emit(str(exc))
        else:
            self.finished_ok.emit(result)

    def stop_and_wait(self, timeout_ms: int = 30_000) -> None:
        """Block until this thread finishes, if it's still running.

        Call this from a teardown path (widget ``destroyed``/close handler)
        before the last reference to the worker goes away, so Qt never has
        to garbage-collect a ``QThread`` mid-run. These workers don't support
        cooperative cancellation, so this waits out the in-flight job rather
        than interrupting it.
        """
        if self.isRunning():
            self.wait(timeout_ms)
