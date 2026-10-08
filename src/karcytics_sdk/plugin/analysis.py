"""Analysis base classes for Karcytics SDK.

Provides abstract interfaces for plugin analysis logic and background
worker support for running analyses in separate threads without blocking
the UI.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from .signals import PluginSignals
from .state import PluginState

logger = logging.getLogger(__name__)


class AnalysisBase(ABC):
    """Abstract base for all plugin analysis logic.

    Separates analysis logic from UI, enabling:
    - Easy unit testing of analysis algorithms
    - Reusable analysis in command-line scripts
    - Background thread execution via AnalysisWorker
    - State + results serialization

    The plugin UI should delegate all scientific computation to a class
    inheriting from this, keeping the UI layer thin and testable.

    Example:
        >>> class MyAnalyzer(AnalysisBase):
        ...     def run(self, state: MyState) -> dict:
        ...         # Your analysis logic here
        ...         results = process_image(state.image_path)
        ...         return {"bands": results}
        ...
        ...     def validate(self, state: MyState) -> tuple[bool, str]:
        ...         if not state.image_path:
        ...             return False, "Image path is required"
        ...         return True, ""
    """

    def __init__(self, plugin_id: str):
        """Initialize the analyzer.

        Args:
            plugin_id: Unique identifier for the plugin
        """
        self.plugin_id = plugin_id
        self.signals = PluginSignals()
        self._is_cancelled = False

    def cancel(self):
        """Request the analysis to stop."""
        self._is_cancelled = True

    def is_cancelled(self) -> bool:
        """Check if analysis was cancelled. Subclasses should check this frequently in loops."""
        return self._is_cancelled

    @abstractmethod
    def run(self, state: PluginState | None = None) -> dict[str, Any]:
        """Execute the analysis with the given state.

        This method should contain all scientific computation and should
        NOT interact with PySide6 widgets or UI.

        Args:
            state: Plugin state containing analysis parameters

        Returns:
            Dictionary with analysis results. Keys should match state fields
            or be documented in your plugin's README.

        Raises:
            ValueError: If state is invalid or analysis fails
        """
        pass

    def validate(self, state: PluginState) -> tuple[bool, str]:
        """Validate that the state has all required data for analysis.

        Called before running analysis. Override to implement custom validation.

        Args:
            state: Plugin state to validate

        Returns:
            Tuple of (is_valid, error_message). If valid, error_message should be empty.
        """
        return True, ""


class AnalysisWorker(QObject):
    """Background worker for running analysis in a separate thread.

    Prevents long-running analysis from blocking the UI. Emits signals
    to notify the UI of progress and completion.

    Example:
        >>> analyzer = MyAnalyzer("my_plugin")
        >>> worker = AnalysisWorker(analyzer, state)
        >>> thread = QThread()
        >>> worker.moveToThread(thread)
        >>> worker.finished.connect(on_analysis_done)
        >>> worker.error.connect(on_analysis_error)
        >>> thread.started.connect(worker.run)
        >>> thread.start()
    """

    finished = Signal(object)  # Emitted with results dict on success
    error = Signal(str)  # Emitted with error message on failure
    progress = Signal(int)  # Emitted with 0-100 progress value
    cancelled = Signal()  # Emitted if analysis was cancelled

    def __init__(self, analyzer: AnalysisBase, state: PluginState | None, parent: QObject | None = None):
        """Initialize the worker.

        Args:
            analyzer: AnalysisBase subclass instance
            state: PluginState instance with analysis parameters
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self.analyzer = analyzer
        self.state = state

        # Link the analyzer signals to our worker proxy signals
        self.analyzer.signals.analysis_progress.connect(self.progress.emit)

    def cancel(self):
        """Request the worker to cancel the analysis."""
        self.analyzer.cancel()

    def run(self) -> None:
        """Execute the analysis and emit results or error signal.

        This method is called when the worker is moved to a QThread and
        the thread is started.
        """
        try:
            results = self.analyzer.run(self.state)

            # Safety check: Has the underlying C++ object been deleted?
            try:
                if self.analyzer.is_cancelled():
                    self.cancelled.emit()
                else:
                    self.finished.emit(results)
            except RuntimeError as e:
                if "deleted" in str(e):
                    logger.debug("AnalysisWorker: Cannot emit finished/cancelled; object already deleted.")
                else:
                    raise

        except Exception as e:
            # Safety check for error emission
            try:
                if self.analyzer.is_cancelled():
                    self.cancelled.emit()
                else:
                    self.error.emit(str(e))
                    logger.exception("Analysis failed in background worker")
            except RuntimeError as re:
                if "deleted" in str(re):
                    logger.debug("AnalysisWorker: Cannot emit error; object already deleted.")
                else:
                    logger.exception("Analysis failed and error emission crashed")


class AnalysisRunnable(QRunnable):
    """Wrapper to execute an AnalysisWorker within a QThreadPool.

    This adapter allows the QObject-based AnalysisWorker to run in the
    standard QThreadPool management system.
    """

    def __init__(self, worker: AnalysisWorker):
        """Initialize the runnable wrapper with a target worker.

        Args:
            worker: The background AnalysisWorker controller containing the job.
        """
        super().__init__()
        self.worker: AnalysisWorker | None = worker
        # QThreadPool will take ownership and delete this runnable after run()
        self.setAutoDelete(True)

    def run(self) -> None:
        """Execution entry point for the thread pool."""
        try:
            # AnalysisWorker.run() handles error catching and signal emission
            if self.worker is not None:
                self.worker.run()
        except RuntimeError as e:
            # Common during shutdown: the worker's C++ object was deleted
            if "deleted" in str(e):
                logger.debug("Background runnable aborted: worker object was deleted.")
            else:
                logger.exception(f"Runtime error in background runnable: {e}")
        except Exception as e:
            # Fallback for truly catastrophic failures inside the worker itself
            logger.exception(f"Critical failure in background runnable: {e}")
        finally:
            self.worker = None
