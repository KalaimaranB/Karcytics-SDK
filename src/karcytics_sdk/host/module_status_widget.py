"""Hub-owned status widget for an isolated module's slot in the workspace.

An isolated module's real UI is a standalone window in its own process — the
Hub's own workspace area never hosts plugin content for one. What it hosts
instead is this: a small widget that mirrors the daemon's lifecycle so the
user always has something to look at and act on in `main_module_layout`,
driven entirely by `PluginUIDaemon` signals, with no plugin-specific code.

State machine::

    Spawning -> Running -> Crashed
        |  (cancel)          | (reopen)
        v                    |
      Closed <----------------

- Spawning: daemon starting, shown immediately on construction.
- Running: the `ready` event arrived; the module's own window is up.
- Crashed: the process exited without us asking it to, at any point.
- Closed: the user cancelled a spawn, or the module's window told us it was
  closed natively (`window_closed`), or we deliberately shut it down.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from karcytics_sdk.plugin.daemon import PluginUIDaemon


class _SerialWorker(QObject):
    """Runs submitted callables one at a time, strictly in submission order,
    on a single dedicated background thread.

    A widget's `cancel()` and a later `start()` (reopen) both need to touch
    the same daemon, and both must never race each other for it: whichever
    of two independently-scheduled `QThread`s happened to reach
    `daemon._start_lock` first used to decide the outcome, which meant a
    stale cancel could occasionally win and kill the process a newer,
    already-successful start had just spawned. A single FIFO worker per
    widget removes the race by construction — order of submission *is* order
    of execution, no scheduler involved.

    Uses a plain `threading.Thread`, not `QThread`, deliberately: a `QThread`
    destroyed while `isRunning()` is a fatal Qt error that aborts the
    process, which is exactly what happened when this worker's thread used
    to be parented to the widget it served. A daemon `threading.Thread` has
    no such hazard — it is simply abandoned if nothing ever joins it, which
    is safe by design.
    """

    result_ready = pyqtSignal(bool, object, object)  # ok, result_or_error, context

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[tuple[Callable[[], Any], Any]] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[[], Any], context: Any) -> None:
        self._queue.put((fn, context))

    def _loop(self) -> None:
        while True:
            fn, context = self._queue.get()
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self.result_ready.emit(False, str(exc), context)
            else:
                self.result_ready.emit(True, result, context)


class ModuleStatusWidget(QWidget):
    """Mirrors an isolated module's daemon lifecycle in the Hub's workspace.

    Also speaks the Ready Gate protocol
    `karcytics.ui.windows.workspace.plugin_loader.PluginLoaderManager`
    already uses for any panel it can't build synchronously (`panel_ready`,
    `data_ready`, `begin_async_init()`) — that's what lets the Hub reuse its
    existing loading choreography for isolated modules verbatim instead of
    forking a parallel, isolated-only code path there.
    """

    STATE_SPAWNING = "spawning"
    STATE_RUNNING = "running"
    STATE_CRASHED = "crashed"
    STATE_CLOSED = "closed"

    state_changed = pyqtSignal(str)
    panel_ready = pyqtSignal()
    data_ready = pyqtSignal()

    def __init__(
        self,
        daemon: PluginUIDaemon,
        module_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._daemon = daemon
        self._module_name = module_name
        self._generation = 0
        self._shutdown_requested = False
        self._state = self.STATE_SPAWNING
        self.error_message: str | None = None
        self._data_ready_emitted = False

        # Not parented to `self` — see `_SerialWorker`'s docstring. It
        # outlives this widget on purpose: a call already queued when the
        # Hub discards this widget (e.g. the user switched modules mid-call)
        # finishes naturally instead of aborting the process.
        self._worker = _SerialWorker()
        self._worker.result_ready.connect(self._on_async_finished)

        self._build_ui()

        # Connected before the first start() so nothing emitted during
        # startup can be missed — a queued signal connected after the fact
        # simply never fires for emissions that already happened.
        self._daemon.event_received.connect(self._on_daemon_event)
        self._daemon.process_exited.connect(self._on_process_exited)

        # Deferred, not emitted here directly: every real caller (see
        # PluginLoaderManager.instantiate_module_panel) connects to
        # panel_ready *after* construction returns — a synchronous emit
        # from inside __init__ would already be over before that connection
        # exists. QTimer.singleShot(0, ...) fires on the next event-loop
        # tick, after the caller's own synchronous connect() has happened.
        QTimer.singleShot(0, self.panel_ready.emit)

        self.start()

    # -- public state -----------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    # -- UI -----------------------------------------------------------------

    def _build_ui(self) -> None:
        """A dimming scrim over whatever page it's floated on (see
        `_instantiate_isolated_overlay` — it sits directly on `root_stack`,
        not a dedicated page, so without one the Hub's own content behind it
        would read as still-interactive) plus one small message card, not a
        Spawning/Running/Crashed status dashboard: this widget's whole job
        is to block the Hub's current page for an isolated module (whose
        real content is a separate window) with one line of text and one
        action.

        Deliberately hardcoded neutral colors, not `karcytics.ui.theme
        .Colors` — this widget stays host-agnostic (see the class
        docstring), and a translucent dark scrim reads correctly over any
        host's page regardless of that host's own palette.
        """
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(self)
        card.setObjectName("moduleStatusCard")
        card.setStyleSheet(
            "#moduleStatusCard {"
            "  background-color: rgba(28, 30, 36, 235);"
            "  border: 1px solid rgba(255, 255, 255, 35);"
            "  border-radius: 14px;"
            "}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 30, 36, 30)
        card_layout.setSpacing(16)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setMaximumWidth(360)
        self._status_label.setStyleSheet("color: #f2f2f2; background: transparent; border: none; font-size: 14px;")

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,30); border-radius: 2px; border: none; height: 4px; } QProgressBar::chunk { background-color: #3b82f6; border-radius: 2px; }"
        )

        # One button whose label/action follows the current state
        # (Cancel while spawning, Bring to Front once running, Reopen after
        # a crash or close) rather than three buttons only one of which is
        # ever visible at a time.
        self._action_button = QPushButton()
        self._action_button.clicked.connect(self._on_action_clicked)

        card_layout.addWidget(self._status_label)
        card_layout.addWidget(self._progress_bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        card_layout.addWidget(self._action_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        outer.addWidget(card)

        self._render_state()

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(8, 9, 12, 165))
        super().paintEvent(event)

    def _on_action_clicked(self) -> None:
        if self._state == self.STATE_SPAWNING:
            self.cancel()
        elif self._state == self.STATE_RUNNING:
            self.bring_to_front()
        else:
            self.start()

    def _render_state(self) -> None:
        messages = {
            self.STATE_SPAWNING: f"Starting {self._module_name}…",
            self.STATE_RUNNING: (f"{self._module_name} is open in its own window — close it to return here."),
            self.STATE_CRASHED: (f"{self._module_name} crashed — {self.error_message or 'unknown error'}"),
            self.STATE_CLOSED: f"{self._module_name} closed.",
        }
        actions = {
            self.STATE_SPAWNING: "Cancel",
            self.STATE_RUNNING: "Bring to Front",
            self.STATE_CRASHED: "Reopen",
            self.STATE_CLOSED: "Reopen",
        }
        self._status_label.setText(messages[self._state])
        self._action_button.setText(actions[self._state])
        if self._state == self.STATE_SPAWNING:
            self._progress_bar.show()
        else:
            self._progress_bar.hide()

    def _set_state(self, state: str, error_message: str | None = None) -> None:
        self._state = state
        self.error_message = error_message
        self._render_state()
        self.state_changed.emit(state)

        # First resolution only (success or failure) — the Ready Gate is a
        # one-shot handshake for the *initial* load. A later cancel/reopen
        # cycle re-enters Spawning/Running without the loader overlay
        # involved at all, so it has nothing to gate a second time.
        if not self._data_ready_emitted and state in (self.STATE_RUNNING, self.STATE_CRASHED):
            self._data_ready_emitted = True
            self.data_ready.emit()

    def begin_async_init(self) -> None:
        """Satisfies `PluginLoaderManager`'s Ready Gate interface check
        (`hasattr(panel, "begin_async_init")`).

        A no-op: unlike an in-process panel, this widget's async work (the
        daemon spawn) already began in `__init__` — there is no distinct
        "not yet started" state for this call to advance out of.
        """

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """(Re)spawn the daemon and enter the Spawning state.

        Deliberately does *not* clear `_shutdown_requested` here, even
        though this is the "un-cancel" action. A cancel-then-reopen submits
        this start's ensure_started() to the worker behind the cancel's own
        shutdown() (FIFO — see `_SerialWorker`), so at the moment this
        method runs, the old process hasn't been killed yet: clearing the
        flag now, before that kill has even happened, is what previously
        let its (still-pending) `process_exited` be misread as an
        unexpected crash of *this* attempt instead of the expected
        consequence of the cancel. It's cleared instead once this
        generation's spawn is actually confirmed — see `_on_start_finished`.
        """
        self._generation += 1
        generation = self._generation
        self._set_state(self.STATE_SPAWNING)

        self._worker.submit(self._daemon.ensure_started, ("start", generation))

    def _on_start_finished(self, generation: int, ok: bool, error: Any) -> None:
        if generation != self._generation:
            return  # superseded by a cancel or a later start() — ignore.
        if ok:
            self._shutdown_requested = False
        else:
            self._set_state(self.STATE_CRASHED, str(error))
        # On success, the Running transition comes from the `ready` event,
        # not from here — ensure_started() returning doesn't itself prove
        # the widget has processed that event yet.

    def cancel(self) -> None:
        """Abort a spawn in progress (or tear down a running module) and
        invalidate any in-flight start so a stale completion can't
        resurrect it.

        The state transition to Closed is immediate (optimistic — the user
        clicked Cancel, so the UI reflects that at once); the actual
        `daemon.shutdown()` is submitted to this widget's `_SerialWorker`
        rather than called here, so it can't block the GUI thread waiting on
        `daemon._start_lock` (shared with `ensure_started()` — see that
        method's docstring in daemon.py) and — because the worker is FIFO —
        it's guaranteed to run before any *later* start()/reopen this widget
        submits, never racing one for the lock.
        """
        self._generation += 1
        self._shutdown_requested = True
        self._set_state(self.STATE_CLOSED)
        self._worker.submit(self._daemon.shutdown, ("shutdown", None))

    def bring_to_front(self) -> None:
        self._worker.submit(lambda: self._daemon.call("focus", {}), ("focus", None))

    def shutdown(self) -> None:
        """Synchronously terminate the daemon.

        Called by the Hub's own main-window `closeEvent()` during final app
        teardown (`if hasattr(wizard_panel, "shutdown"): wizard_panel.shutdown()`)
        — unlike `cancel()`, a user-initiated action mid-session that must
        never block the GUI thread, this runs synchronously and directly:
        the whole app is exiting right after this call returns, so routing
        it through `_SerialWorker` risks the process exiting before a
        queued shutdown ever runs, leaving the daemon subprocess orphaned.
        """
        self._shutdown_requested = True
        self._daemon.shutdown()

    def push_theme(self, colors: dict[str, str]) -> None:
        """Forward a color palette to the daemon's isolated window via its
        "theme_changed" request.

        Deliberately opaque to what the palette represents (dark/light,
        accent, ...) — that's the Hub's concern; this widget only relays
        whatever `colors` it's given, and only once a window actually
        exists to receive it. Uses `daemon.call()`, not `send_event()`:
        the worker's `RequestDispatcher` only understands
        `{"method": ..., "kwargs": ...}` request frames (see
        `ui_daemon_runtime.py`'s `theme_changed` handler, registered the
        same way `bring_to_front()`'s `focus` call is below) — it has no
        handler for an unsolicited `{"kind": "event"}` frame from the Hub,
        so a `send_event()` here was silently dropped on arrival and never
        reached the handler that actually applies the new colors. Blocking
        is fine: this already runs on `_worker`'s background thread, same as
        `bring_to_front()`'s call below.
        """
        if sip.isdeleted(self) or self._state != self.STATE_RUNNING:
            return
        self._worker.submit(lambda: self._daemon.call("theme_changed", {"colors": colors}), ("theme", None))

    def _on_async_finished(self, ok: bool, result: Any, context: Any) -> None:
        """Single bound-method landing spot for every call this widget's
        `_SerialWorker` runs.

        Because the worker outlives this widget on purpose (see
        `ModuleStatusWidget.__init__`), this signal can legitimately arrive
        after the Hub has already discarded the widget that submitted the
        call — e.g. the user switched to another module while a `focus`
        call was still queued. `sip.isdeleted` guards every slot reached
        this way so a stale completion is dropped instead of touching a
        deleted QWidget.
        """
        if sip.isdeleted(self):
            return
        kind, generation = context
        if kind == "start":
            self._on_start_finished(generation, ok, result)
        elif kind in ("focus", "shutdown", "theme"):
            pass  # fire-and-forget; nothing in the UI depends on the result.

    def _on_daemon_event(self, topic: str, payload: Any) -> None:  # noqa: ARG002
        if sip.isdeleted(self):
            return
        if topic == "ready":
            if self._state == self.STATE_SPAWNING:
                self._set_state(self.STATE_RUNNING)
        elif topic == "loading_progress":
            if self._state == self.STATE_SPAWNING and isinstance(payload, dict):
                msg = payload.get("message")
                if msg:
                    self._status_label.setText(f"Starting {self._module_name}…\n{msg}")
        elif topic == "window_closed":
            self._set_state(self.STATE_CLOSED)

    def _on_process_exited(self) -> None:
        if sip.isdeleted(self):
            return
        if self._shutdown_requested:
            return
        if self._state in (self.STATE_SPAWNING, self.STATE_RUNNING):
            self._set_state(self.STATE_CRASHED, "Module process exited unexpectedly.")
