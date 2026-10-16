"""Cross-thread bridge for calling into Qt-owned state from a background thread.

`CoreServicesServer` dispatches each RPC on one of `ThreadingHTTPServer`'s
worker threads, never the Qt GUI thread. A handler that only emits a Qt
signal is already safe as written — Qt auto-queues cross-thread
signal->slot delivery onto the receiver's own thread — but a handler that
directly touches widgets (`setStyleSheet`, opening a dialog, anything that
isn't "emit and return") is not: Qt requires those calls happen on the
thread that owns the `QApplication`. `QtThreadBridge` is the one place that
marshals such a call onto that thread and blocks the caller for its result.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal

from karcytics_sdk.plugin.signals import move_to_gui_thread


@dataclass
class _CallRequest:
    fn: Callable[[], Any]
    result_queue: queue.Queue[tuple[str, Any]]


class QtThreadBridge(QObject):
    """Construct once, on the Qt GUI thread, and reuse for every call.

    `run()` is safe to call from any thread, including the one that
    constructed this bridge: Qt's default `AutoConnection` resolves to a
    same-thread direct call in that case (so `run()` executes `fn()`
    synchronously within `emit()` itself, with no queueing round-trip) and
    to a cross-thread queued call otherwise, which is what lets a
    `CoreServicesServer` handler thread block until the GUI thread actually
    runs `fn()` and hands back its result.
    """

    _call_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        # The whole point is to run on the GUI thread, so never let a bridge
        # built elsewhere quietly run its calls on that other thread.
        move_to_gui_thread(self)
        self._call_requested.connect(self._on_call_requested)

    def _on_call_requested(self, request: _CallRequest) -> None:
        try:
            result = request.fn()
        except Exception as exc:  # noqa: BLE001
            request.result_queue.put(("error", exc))
        else:
            request.result_queue.put(("ok", result))

    def run(self, fn: Callable[[], Any], timeout: float = 10.0) -> Any:
        """Run `fn()` on this bridge's owning thread and return its result.

        Raises:
            Whatever `fn()` itself raised, re-raised on the calling thread.
            queue.Empty: If the GUI thread doesn't respond within `timeout`
                seconds (e.g. it's blocked in a modal dialog's own nested
                event loop).
        """
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._call_requested.emit(_CallRequest(fn, result_queue))
        status, payload = result_queue.get(timeout=timeout)
        if status == "error":
            raise payload
        return payload
