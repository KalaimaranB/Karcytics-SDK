"""Real, permanent-home services for a plugin process pinned to
`process_model = "isolated"`.

An isolated plugin's own standalone `.venv` never depends on the Hub's own
`karcytics` package — it isn't importable there at all, in any mode — so a
call site written as `from karcytics.core.task_scheduler import
task_scheduler` (or `event_bus`, `diagnostics`, `tutorial_manager`) was
always going to be wrong once that plugin runs isolated, not just
"incompatible until shimmed". This module is where those imports actually
resolve, honestly: `task_scheduler`, `event_bus`, `diagnostics`, and
`tutorial_manager` here are real, directly-importable singletons a plugin's
own code imports by their true location — not `sys.modules` injected under
the Hub's namespace to make an old import statement keep working unchanged.

`tutorial_manager` here is a real `AcademyManager` (see `academy.py`), wired
to this process's own local `CentralEventBus` and its own per-plugin
persistence directory. It runs every course a plugin registers that only
touches the plugin's own live UI — `InteractionStep`/`VerificationStep`/
`ActionStep`, which is everything every course shipped in this codebase
uses today. The one thing it genuinely can't do is advance a
`WaitForEventStep` gated on an event only the Hub can see (there is no
Hub->plugin event push channel yet); no plugin course uses that step type,
so this is a real, complete engine for every course that exists.
"""

from __future__ import annotations

import logging
import os
import uuid
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QMetaObject, QObject, Qt, QThreadPool, Signal, Slot

from .academy import AcademyManager
from .analysis import AnalysisBase, AnalysisRunnable, AnalysisWorker
from .events import CentralEventBus
from .ui_daemon_runtime import send_event

if TYPE_CHECKING:
    from .state import PluginState

logger = logging.getLogger(__name__)


class LocalTaskScheduler(QObject):
    """A plugin process's own task scheduler.

    Mirrors the Hub's `TaskScheduler` signal contract exactly
    (`task_started`/`task_finished`/`task_error`/`task_progress`, all keyed by
    `task_id`) — plugin code assumes a shared scheduler broadcasts every
    task's lifecycle to *any* listener, not just the caller that submitted it
    (e.g. a canvas re-render triggered by any background task completing).
    The Hub's `TaskScheduler` centralizes a single `QThreadPool` specifically
    to stop multiple concurrently-loaded plugins from exhausting it against
    each other; that concern doesn't apply here, since an isolated process
    only ever hosts one module.
    """

    task_started = Signal(str)
    task_finished = Signal(str, object)
    task_error = Signal(str, str)
    task_progress = Signal(str, int)

    def __init__(self) -> None:
        super().__init__()
        self._pool: Any = QThreadPool.globalInstance()
        assert self._pool is not None
        self._active_workers: dict[str, AnalysisWorker] = {}
        self._retired_workers: list[AnalysisWorker] = []

    def submit(self, analyzer: AnalysisBase, state: PluginState | None = None) -> AnalysisWorker:
        task_id = str(uuid.uuid4())

        worker = AnalysisWorker(analyzer, state, parent=self)
        worker.task_id = task_id  # type: ignore[attr-defined]

        worker.finished.connect(partial(self._on_task_finished, task_id))
        worker.error.connect(partial(self._on_task_error, task_id))
        worker.progress.connect(partial(self.task_progress.emit, task_id))

        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)

        self._active_workers[task_id] = worker

        runnable = AnalysisRunnable(worker)
        self.task_started.emit(task_id)
        self._pool.start(runnable)

        return worker

    def cancel_all(self) -> None:
        self._pool.clear()

    def _on_task_finished(self, task_id: str, results: dict) -> None:
        self.task_finished.emit(task_id, results)
        self._cleanup(task_id)

    def _on_task_error(self, task_id: str, error_msg: str) -> None:
        self.task_error.emit(task_id, error_msg)
        logger.error("Background task %s failed: %s", task_id, error_msg)
        self._cleanup(task_id)

    def _cleanup(self, task_id: str) -> None:
        # This runs *during* the worker's finished/error emission. Under
        # PySide6 a slot that is not a bound QObject method (the lambdas
        # callers such as the render pipeline connect to `worker.finished`)
        # is delivered through the sender, so letting the worker be destroyed
        # here silently drops every such slot still queued behind this one.
        # Detach it as before, but hold a reference until `_release_retired`
        # runs — a queued call, so it lands after those pending slots.
        worker = self._active_workers.pop(task_id, None)
        if worker is None:
            return
        try:
            import shiboken6

            if shiboken6.isValid(worker):
                worker.setParent(None)
        except (ImportError, AttributeError, RuntimeError):
            pass
        self._retired_workers.append(worker)
        QMetaObject.invokeMethod(self, "_release_retired", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _release_retired(self) -> None:
        self._retired_workers.clear()


class _TaskSchedulerProxy:
    """Lazily builds the process-wide `LocalTaskScheduler` on first use.

    Mirrors the Hub's own `TaskSchedulerProxy`: constructing a `QObject`
    before a `QApplication` fully exists can crash on some platforms, so
    every call site sharing this one proxy instance is safe to import at
    module load time even before `QApplication` is constructed.
    """

    def __init__(self) -> None:
        self._instance: LocalTaskScheduler | None = None

    def _get_instance(self) -> LocalTaskScheduler:
        if self._instance is None:
            self._instance = LocalTaskScheduler()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_instance(), name)


task_scheduler: LocalTaskScheduler = _TaskSchedulerProxy()  # type: ignore[assignment]


class _EventTopicAccessor:
    """Stand-in for the Hub's real `KarcyticsEvent` enum.

    A plugin process has no access to that enum's actual values, and doesn't
    need them: `RemoteEventBus.emit()` only ever needs *a name* to forward as
    an event topic, and `subscribe()`/`unsubscribe()` are no-ops here (see
    `RemoteEventBus`), so the token's identity never matters beyond that.
    """

    def __getattr__(self, name: str) -> str:
        return name


class RemoteEventBus:
    """A plugin process's connection to the Hub's event fabric.

    `emit()`/`publish()` forward to the Hub as an "event" frame over the
    existing `PluginUIDaemon` transport — always worked, since a worker
    already pushes events unprompted (`window_closed`, theme acks, ...) over
    that same channel.

    `subscribe()`/`unsubscribe()` are real now too, built on the Hub->worker
    channel Phase 2 added: this process's own `client.call("event.subscribe",
    ...)` registers interest with the Hub's `core_services_bootstrap.py`,
    which then forwards a matching `KarcyticsEvent` by calling this worker's
    `dispatch_event` request (see `ui_daemon_runtime.py`). Only topics
    something here actually subscribed to are ever forwarded — the Hub never
    blind-broadcasts. `WaitForEventStep` is the reason this exists: it's the
    one `AcademyManager` step type that needs an event only the Hub can see.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Any]] = {}
        self._client: Any | None = None

    def _get_client(self) -> Any | None:
        if self._client is None:
            port = os.environ.get("KARCYTICS_CORE_SERVICES_PORT")
            token = os.environ.get("KARCYTICS_CORE_SERVICES_TOKEN")
            if not port or not token:
                return None
            from karcytics_sdk.host.core_services import CoreServicesClient

            self._client = CoreServicesClient(int(port), token=token)
        return self._client

    def emit(self, event_type: Any, *args: Any, **kwargs: Any) -> None:
        topic = getattr(event_type, "name", None) or str(event_type)
        payload = kwargs or (args[0] if len(args) == 1 else (args or None))
        send_event(topic.lower(), payload)

    def publish(self, topic: str, data: Any = None) -> None:
        send_event(topic, data)

    def subscribe(self, event_type: Any, callback: Any) -> None:
        topic = getattr(event_type, "name", None) or str(event_type)
        subs = self._subscribers.setdefault(topic, [])
        is_first_subscriber = not subs
        if callback not in subs:
            subs.append(callback)

        # Only the *first* local subscriber for this topic needs to tell the
        # Hub anything — every subsequent one is served from the same
        # forwarded event, not a second registration.
        if not is_first_subscriber:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            client.call("event.subscribe", topic=topic, plugin_id=os.environ.get("KARCYTICS_PLUGIN_ID", "unknown"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to subscribe to Hub event topic.",
                extra={"log_event": "event_subscribe_failed", "topic": topic, "error": str(exc)},
            )

    def unsubscribe(self, event_type: Any, callback: Any) -> None:
        topic = getattr(event_type, "name", None) or str(event_type)
        subs = self._subscribers.get(topic)
        if not subs or callback not in subs:
            return
        subs.remove(callback)
        if subs:
            return

        del self._subscribers[topic]
        client = self._get_client()
        if client is None:
            return
        try:
            client.call("event.unsubscribe", topic=topic, plugin_id=os.environ.get("KARCYTICS_PLUGIN_ID", "unknown"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to unsubscribe from Hub event topic.",
                extra={"log_event": "event_unsubscribe_failed", "topic": topic, "error": str(exc)},
            )

    def dispatch_event(self, topic: str, payload: Any) -> None:
        """Invoke every local subscriber for `topic` — called by
        `ui_daemon_runtime.py`'s `dispatch_event` request handler when the
        Hub forwards a matching event, never by this process itself.
        """
        for callback in list(self._subscribers.get(topic, ())):
            try:
                callback(payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Event subscriber raised.",
                    extra={"log_event": "event_dispatch_handler_failed", "topic": topic},
                )


class DiagnosticsForwarder:
    """A plugin process's connection to the Hub's diagnostics reporter.

    Signature matches the Hub's real `diagnostics.report_error()` exactly so
    call sites need no change beyond their import. `exception` crosses the
    wire as its string form — a live `BaseException` isn't msgpack-
    serializable, and the Hub-side handler only ever needs it for display.

    The traceback is captured here, in this process, via `traceback.
    format_exc()` — the same call the Hub's own `report_error()` makes, just
    executed where the real exception context still exists. Call this from
    inside the `except:` block handling `exception`, same as the in-process
    equivalent; called outside one, this just sends `None` for both fields,
    same as the Hub-side method does today.
    """

    def report_error(
        self,
        message: str,
        exception: BaseException | None = None,
        plugin_id: str | None = None,
        fatal: bool = False,
    ) -> None:
        import traceback

        send_event(
            "diagnostics_error",
            {
                "message": message,
                "exception": str(exception) if exception is not None else None,
                "traceback": traceback.format_exc() if exception is not None else None,
                "plugin_id": plugin_id,
                "fatal": fatal,
            },
        )


class _LocalAcademyEventBus:
    """Adapts this process's own topic-string `CentralEventBus` (single
    payload per `publish()`/`subscribe()`) to the multi-arg `AcademyEventBus`
    protocol `AcademyManager` expects.

    The payload always crosses `CentralEventBus` as a tuple of the original
    `emit(topic, *args)` args, unpacked back on receipt — never collapsed to
    a bare value or `None` for the single-arg case, since `AcademyManager`
    itself legitimately emits a single `None` argument (`current_step` when
    a course completes); collapsing that would be indistinguishable from
    "zero args" and crash `TutorialOverlay.render_step()`, which always
    requires its one `step` argument.

    Keeps a registry from `(topic, id(original_callback))` to the wrapper
    actually handed to `CentralEventBus`, since `unsubscribe()` needs to pass
    that exact wrapper back to remove it.
    """

    def __init__(self) -> None:
        self._wrapped: dict[tuple[str, int], Any] = {}

    def subscribe(self, topic: str, callback: Any) -> None:
        def _wrapper(payload: tuple[Any, ...]) -> None:
            callback(*payload)

        self._wrapped[(topic, id(callback))] = _wrapper
        CentralEventBus.subscribe(topic, _wrapper)

    def unsubscribe(self, topic: str, callback: Any) -> None:
        wrapper = self._wrapped.pop((topic, id(callback)), None)
        if wrapper is not None:
            CentralEventBus.unsubscribe(topic, wrapper)

    def emit(self, topic: str, *args: Any) -> None:
        CentralEventBus.publish(topic, args)


def _academy_persistence_dir() -> Path:
    plugin_id = os.environ.get("KARCYTICS_PLUGIN_ID", "unknown")
    return Path.home() / ".karcytics" / "plugin_configs" / plugin_id / "academy"


KarcyticsEvent = _EventTopicAccessor()
event_bus = RemoteEventBus()
diagnostics = DiagnosticsForwarder()

# Exposed separately (not just as `tutorial_manager`'s private `_event_bus`)
# so `ui_daemon_runtime.py` can build a `TutorialOverlay` subscribed to the
# exact same bus `tutorial_manager` itself emits on — see its Help-menu
# "Academy" wiring.
academy_event_bus = _LocalAcademyEventBus()
tutorial_manager = AcademyManager(event_bus=academy_event_bus, persistence_dir=_academy_persistence_dir())


__all__ = [
    "LocalTaskScheduler",
    "task_scheduler",
    "KarcyticsEvent",
    "RemoteEventBus",
    "event_bus",
    "DiagnosticsForwarder",
    "diagnostics",
    "tutorial_manager",
    "academy_event_bus",
]
