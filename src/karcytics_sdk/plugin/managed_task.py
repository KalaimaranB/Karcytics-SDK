"""Functional Task Utilities for Karcytics SDK.

Enables developers to run arbitrary functions (downloads, I/O, networking)
on the global TaskScheduler without needing to subclass AnalysisBase manually.
"""

import inspect
from collections.abc import Callable
from typing import Any

from .analysis import AnalysisBase
from .state import PluginState


class FunctionalTask(AnalysisBase):
    """A proxy that converts a callable into a Karcytics Analysis task.

    A function that takes a ``progress_callback`` keyword is given one that
    reports 0-100 through the task's ``analysis_progress`` signal (and so
    ``task_scheduler.task_progress`` / ``submit_task(on_progress=...)``).

    Example:
        >>> def my_download(progress_callback):
        ...     progress_callback(50)
        ...     return {"file": "data.fcs"}
        >>> task = FunctionalTask(my_download, "my_plugin")
        >>> task_scheduler.submit(task, None)
    """

    def __init__(self, func: Callable[..., Any], plugin_id: str = "unknown", name: str = "Utility Task"):
        """Initialize the task.

        Args:
            func: The function to execute. Should return a dict or None.
            plugin_id: The ID of the plugin owning this task (optional).
            name: Human-readable name for logging/UI.
        """
        super().__init__(plugin_id)
        self.func = func
        self.name = name
        try:
            self._accepts_progress = "progress_callback" in inspect.signature(func).parameters
        except (TypeError, ValueError):  # builtins without an introspectable signature
            self._accepts_progress = False

    def run(self, state: PluginState | None = None) -> dict:
        """Executes the wrapped function and returns standardized results."""
        try:
            if self._accepts_progress:
                result = self.func(progress_callback=self.signals.analysis_progress.emit)
            else:
                result = self.func()
            if isinstance(result, dict):
                return result
            return {"result": result, "status": "success"}
        except Exception as e:
            # The AnalysisWorker will catch this and emit the error signal
            raise e

    def __repr__(self):
        return f"<FunctionalTask: {self.name} ({self.plugin_id})>"
