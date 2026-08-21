"""Base plugin class for Karcytics SDK.

Provides the main PluginBase class that all plugins should inherit from,
with integrated state management and undo/redo support.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtWidgets import QWidget

try:
    from karcytics.ui.theme import Colors, theme_manager

    _IN_PROCESS = True
except ImportError:
    # In an isolated plugin .venv, karcytics.ui.theme is never importable.
    # theme_fallback provides the SDK's canonical fallback: Colors reads live
    # (via DynamicColors) and theme_manager.theme_changed actually fires when
    # the Hub pushes a new theme (see ui_daemon_runtime.py's theme_changed
    # handler). Same pattern already used by components.py and cyto_character.py.
    from .theme_fallback import Colors, theme_manager

    _IN_PROCESS = False

from .analysis import AnalysisBase, AnalysisRunnable, AnalysisWorker
from .events import CentralEventBus
from .signals import PluginSignals
from .state import PluginState

if TYPE_CHECKING:
    from karcytics_sdk.interfaces.i_crash_reporter import ICrashReporter
    from karcytics_sdk.interfaces.i_task_scheduler import ITaskScheduler

    from .rendering.lock import RasterLock
    from .rendering.pipeline import RasterizeStage, RenderComputeStage, RenderPipelineController


class PluginBase(QWidget):
    """Abstract base class for all Karcytics plugins.

    This class implements the KarcyticsPlugin Protocol.
    """

    """Abstract base class for all Karcytics plugins.

    Provides:
    - Standard signals (status, state_changed, analysis_*, etc)
    - History management integration for undo/redo
    - State serialization/deserialization
    - Consistent plugin interface

    All plugins must inherit from this class and implement get_state() and set_state().

    Example:
        >>> class MyPlugin(PluginBase):
        ...     def __init__(self, plugin_id: str):
        ...         super().__init__(plugin_id)
        ...         self.state = MyState()
        ...         self.analyzer = MyAnalyzer(plugin_id)
        ...         # Build UI...
        ...
        ...     def get_state(self) -> PluginState:
        ...         return self.state
        ...
        ...     def set_state(self, state: PluginState) -> None:
        ...         self.state = state
        ...         self.update_ui()
    """

    def __init__(self, plugin_id: str, parent=None):
        """Initialize the plugin.

        Args:
            plugin_id: Unique identifier for this plugin
            parent: Parent QWidget (usually None for top-level plugins)
        """
        super().__init__(parent)
        self.signals = PluginSignals()

        self.plugin_id = plugin_id

        # Initialize context-aware logger
        from .logging import get_logger

        self.logger = get_logger(f"plugin.{plugin_id}", plugin_id)

        self._history = None
        self._current_state = None

        # Isolated plugins have no automatic route back to the Hub's
        # diagnostics engine (in-process plugins get one for free via
        # logger.exception() + the Hub's AutoReportHandler) — resolve the
        # explicit forwarder only when actually running isolated.
        self.crash_reporter: ICrashReporter | None = None
        if not _IN_PROCESS:
            from .runtime_services import diagnostics as _isolated_diagnostics

            self.crash_reporter = _isolated_diagnostics

        # Connect to global theme engine
        theme_manager.theme_changed.connect(self._apply_theme_styles)

    @property
    def history(self):
        """Lazy-loaded HistoryManager to avoid circular dependencies."""
        if not hasattr(self, "_history") or self._history is None:
            try:
                from karcytics.core.history_manager import HistoryManager

                self._history = HistoryManager()
            except ImportError:

                class MockHistoryManager:
                    def get_module_history(self, *args, **kwargs):
                        class MockHistory:
                            def push(self, *args):
                                pass

                            def undo(self):
                                return None

                            def redo(self):
                                return None

                            @property
                            def undo_stack(self):
                                return [1, 2]

                            @property
                            def redo_stack(self):
                                return []

                        return MockHistory()

                self._history = MockHistoryManager()
        return self._history

    @history.setter
    def history(self, value):
        self._history = value

    def publish_event(self, topic: str, data: Any = None) -> None:
        """Publish an event to the Central Event Bus."""
        CentralEventBus.publish(topic, data)

    def subscribe_event(self, topic: str, callback: Callable[[Any], None]) -> None:
        """Subscribe to an event on the Central Event Bus."""
        CentralEventBus.subscribe(topic, callback)

    def __getattr__(self, name: str):
        """Proxy signal access to self.signals for convenience.

        Allows using `self.state_changed.emit()` instead of
        `self.signals.state_changed.emit()`.
        """
        if hasattr(self, "signals") and hasattr(self.signals, name):
            return getattr(self.signals, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    @abstractmethod
    def get_state(self) -> PluginState:
        """Return the current analysis state.

        Must be implemented by subclasses. Called by Karcytics core to capture
        plugin state for undo/redo and workflow persistence.

        Returns:
            Current PluginState instance
        """
        pass

    @abstractmethod
    def set_state(self, state: PluginState) -> None:
        """Set the plugin state and update UI accordingly.

        Must be implemented by subclasses. Called by Karcytics core to restore
        plugin state during undo/redo and workflow loading.

        Args:
            state: PluginState instance to restore
        """
        pass

    def push_state(self) -> None:
        """Save current state to undo history.

        Call this whenever the user makes a destructive edit (e.g., changing
        a parameter, drawing on an image). Karcytics will emit state_changed signal
        and automatically capture this state for undo/redo.
        """
        state_dict = self.get_state().to_dict()
        self.history.get_module_history(self.plugin_id).push(state_dict)
        self.state_changed.emit()

    def undo(self) -> None:
        """Undo to previous state."""
        history = self.history.get_module_history(self.plugin_id)
        prev_state_dict = history.undo()
        if prev_state_dict:
            state = self.get_state().__class__.from_dict(prev_state_dict)
            self.set_state(state)
            self.state_changed.emit()

    def redo(self) -> None:
        """Redo to next state."""
        history = self.history.get_module_history(self.plugin_id)
        next_state_dict = history.redo()
        if next_state_dict:
            state = self.get_state().__class__.from_dict(next_state_dict)
            self.set_state(state)
            self.state_changed.emit()

    def can_undo(self) -> bool:
        """Check if undo is available.

        Returns:
            True if there are states to undo to
        """
        history = self.history.get_module_history(self.plugin_id)
        return len(history.undo_stack) > 1

    def can_redo(self) -> bool:
        """Check if redo is available.

        Returns:
            True if there are states to redo to
        """
        history = self.history.get_module_history(self.plugin_id)
        return len(history.redo_stack) > 0

    # ── Resource Lifecycle (RAII) ────────────────────────────────────

    def cleanup(self) -> None:
        """Automatic Resource Cleansing.

        Uses ResourceInspector to break references to heavy objects in both
        the plugin instance and its state. This helps the GC reclaim memory
        immediately when a tab is closed.
        """
        try:
            from karcytics.core.resource_inspector import ResourceInspector
        except ImportError:

            class ResourceInspector:
                @staticmethod
                def get_heavy_resources(*args, **kwargs):
                    return []

        # 1. Clean PluginState
        state = self.get_state()
        if state:
            heavy_in_state = ResourceInspector.get_heavy_resources(state)
            for name, _ in heavy_in_state:
                setattr(state, name, None)

        # 2. Clean Plugin Instance attributes
        heavy_in_instance = ResourceInspector.get_heavy_resources(self)
        for name, _ in heavy_in_instance:
            if name != "state":  # Don't wipe the state container itself
                setattr(self, name, None)

        self.state_changed.emit()
        self.status_message.emit("Resources released.")

    def shutdown(self) -> None:
        """Default shutdown. Subclasses should override if managing GPU models."""
        pass

    # ── Background work ─────────────────────────────────────────────

    def create_worker(self, analyzer: AnalysisBase, state: PluginState | None = None) -> AnalysisWorker:
        """Build an `AnalysisWorker` wrapping `analyzer`, ready for `start_worker()` dispatch.

        Kept as a separate call from `start_worker()` specifically so
        callers can connect to the worker's signals (`progress`, `finished`,
        `error`, `cancelled`) before the analyzer starts running on another
        thread — connecting after `start_worker()` risks missing an
        emission that fires before the connection is made.
        """
        return AnalysisWorker(analyzer, state, parent=self)

    def start_worker(self, worker: AnalysisWorker) -> AnalysisWorker:
        """Dispatch a worker built by `create_worker()` onto the shared `QThreadPool`.

        Uses `AnalysisRunnable` directly — the same `QThreadPool` adapter
        `runtime_services.LocalTaskScheduler.submit()` uses internally —
        rather than routing through a task scheduler's `submit(analyzer,
        state)`, because that call always builds its own fresh
        `AnalysisWorker` from an analyzer/state pair and has no way to
        accept an already-constructed worker. The whole point of the
        `create_worker()`/`start_worker()` split is that callers connect
        signals on the exact worker instance `create_worker()` returned
        before dispatch, so that same instance must be the one that runs.
        """
        from PyQt6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(AnalysisRunnable(worker))
        return worker

    def create_render_pipeline(
        self,
        compute_stage: RenderComputeStage,
        rasterize_stage: RasterizeStage,
        target_factory: Callable[[], Any],
        raster_lock: RasterLock | None = None,
        task_scheduler: ITaskScheduler | None = None,
    ) -> RenderPipelineController:
        """Build a `RenderPipelineController` pairing `compute_stage` with `rasterize_stage`.

        For plugins that want the async-compute/locked-rasterize split
        (see `rendering.pipeline`) without adopting
        `rendering.LayeredMatplotlibCanvas` wholesale — e.g. a one-off
        "export image" action. `target_factory` builds the object
        `rasterize_stage.rasterize()` draws onto (e.g. a fresh matplotlib
        `Axes`) each time a request completes. `task_scheduler` defaults to
        the process-wide `runtime_services.task_scheduler` singleton but is
        overridable — e.g. with a synchronous fake in tests — mirroring
        `rendering.LayeredMatplotlibCanvas`'s own constructor for the same
        reason.
        """
        from .rendering.lock import MPL_RASTER_LOCK
        from .rendering.pipeline import RenderPipelineController

        if task_scheduler is None:
            from .runtime_services import task_scheduler as default_task_scheduler

            task_scheduler = default_task_scheduler

        return RenderPipelineController(
            compute_stage=compute_stage,
            rasterize_stage=rasterize_stage,
            raster_lock=raster_lock or MPL_RASTER_LOCK,
            task_scheduler=task_scheduler,
            target_factory=target_factory,
            parent=self,
            crash_reporter=self.crash_reporter,
            plugin_id=self.plugin_id,
        )

    # ── Two-phase loading protocol ────────────────────────────────────
    #
    # Opt-in protocol for panels that support smooth animated loading.
    # Karcytics's PluginLoaderManager detects it via ``hasattr(panel, 'panel_ready')``.
    #
    # To implement, subclass must ALSO declare these class-level PyQt signals:
    #
    #   panel_ready = pyqtSignal()
    #       Emitted after Phase 2 heavy widgets are built.
    #       PluginLoaderManager updates the loader message to "Loading workspace data…".
    #
    #   data_ready = pyqtSignal()   # OPTIONAL
    #       Emitted when ALL background data processing is done and the first
    #       meaningful render is complete.  PluginLoaderManager cross-fades the
    #       GalacticLoader into the fully-populated workspace only after this fires.
    #       If omitted, the loader cross-fades immediately after ``panel_ready``.
    #       SIGNAL ORDER RULE: panel_ready MUST ALWAYS be emitted before data_ready.
    #       A 45-second safety timeout is always armed as a fallback.
    #
    # Phase 2 best practices
    # -----------------------
    # Avoid building all heavy widgets in one synchronous block.  Instead chain
    # construction via ``QTimer.singleShot(0, next_step)`` so the Qt event loop
    # (and thus the QML GalacticLoader animation) gets a frame between each widget.
    #
    # Deferred workflow loading
    # -------------------------
    # PluginLoaderManager stores the pending workflow payload on
    # ``panel._deferred_workflow_payload`` before calling ``begin_async_init``.
    # Check for that attribute at the end of your Phase 2 chain and call
    # ``self.load_workflow(self._deferred_workflow_payload, ...)`` there so that
    # ``panel_ready`` is emitted AFTER FCS data is in memory.

    def begin_async_init(self) -> None:
        """Override to implement the two-phase loading protocol.

        Called by ``PluginLoaderManager`` immediately after the skeleton panel
        is added to the layout.  The default no-op preserves backward compatibility
        for panels that do not need the protocol.

        Subclasses that override this method MUST also declare:
        - ``panel_ready = pyqtSignal()`` on the class
        - Optionally: ``data_ready = pyqtSignal()`` for async data gating

        See the class-level docstring above for full protocol details.
        """
        pass  # Default: no-op — detection key is hasattr(panel, 'panel_ready')

    def _apply_theme_styles(self) -> None:
        """Re-applies theme-aware styles to the plugin.

        Subclasses should override this if they have complex custom styling.
        """
        # Force a re-evaluation of the base stylesheet
        self.setStyleSheet(f"background: {Colors.BG_DARKEST}; color: {Colors.FG_PRIMARY};")

        # Propagate to children if they have their own theme handlers
        from PyQt6.QtWidgets import QWidget

        for child in self.findChildren(QWidget):
            if hasattr(child, "_apply_theme_styles") and child is not self:
                child._apply_theme_styles()
            elif hasattr(child, "refresh_styles"):
                child.refresh_styles()

            # Re-evaluate local stylesheets to pick up {Colors.VAR} changes
            if child.styleSheet():
                child.setStyleSheet(child.styleSheet())
            child.update()

    def closeEvent(self, event):
        """Triggers automatic cleanup when the plugin widget is closed."""
        self.cleanup()
        super().closeEvent(event)

    # ── Protocol Compliance ───────────────────────────────────────────

    @property
    def __version__(self) -> str:
        return "1.0.0"  # Default for base plugins

    @property
    def __plugin_id__(self) -> str:
        return self.plugin_id

    @classmethod
    def get_panel_class(cls):
        """Standard protocol requirement: return the class itself."""
        return cls
