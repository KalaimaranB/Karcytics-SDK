"""Qt-free timing helpers for plugin code that must not import PySide6 itself
(e.g. an analysis layer), but still needs to run work on the GUI thread.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .signals import move_to_gui_thread


class Debouncer(QObject):
    """Run `callback` on the GUI thread once `trigger()` calls stop arriving.

    Each `trigger(interval_ms)` restarts the countdown, so a burst of calls
    (a gate handle being dragged) runs `callback` once, `interval_ms` after
    the last of them. `trigger()` and `cancel()` are safe from any thread;
    `callback` always runs on the GUI thread, which is what makes work it
    submits to the task scheduler safe under PySide6. A plain
    `threading.Timer` ran its callback on its own short-lived thread instead.
    """

    _trigger_requested = Signal(int)
    _cancel_requested = Signal()

    def __init__(self, callback: Callable[[], object]) -> None:
        super().__init__()
        move_to_gui_thread(self)
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        # Bound methods of this QObject: queued onto its (GUI) thread when
        # emitted from any other one, direct otherwise.
        self._trigger_requested.connect(self._start)
        self._cancel_requested.connect(self._timer.stop)

    def trigger(self, interval_ms: int) -> None:
        """(Re)start the countdown; `callback` runs `interval_ms` after the last call."""
        self._trigger_requested.emit(interval_ms)

    def cancel(self) -> None:
        """Drop a pending run, if any."""
        self._cancel_requested.emit()

    def is_pending(self) -> bool:
        return self._timer.isActive()

    def _start(self, interval_ms: int) -> None:
        self._timer.start(interval_ms)

    def _fire(self) -> None:
        self._callback()
