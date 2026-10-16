"""Standard plugin signals for Karcytics SDK.

This module defines all standard PySide6 signals that plugins should emit
to communicate state changes and results to the Karcytics core application.
"""

import warnings
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal


def safe_disconnect(signal: Any, slot: Callable[..., Any] | None = None) -> bool:
    """Disconnect `slot` from `signal`, quietly doing nothing if it was not connected.

    For the "disconnect, then connect" idiom that keeps a slot connected
    exactly once. PySide6 reports a disconnect that finds nothing with a
    `RuntimeWarning` (and, for a deleted sender, a `RuntimeError`); neither
    is useful to a caller that only wants the slot gone.

    Args:
        signal: The bound signal, e.g. `button.clicked`.
        slot: The slot to remove. `None` removes every connection.

    Returns:
        True if a connection was removed.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            return bool(signal.disconnect(slot) if slot is not None else signal.disconnect())
        except (TypeError, RuntimeError):
            return False


def move_to_gui_thread(obj: QObject) -> None:
    """Give a newly built, parentless `obj` to the application's GUI thread.

    Call it right after constructing a long-lived `QObject` that may be built
    off the GUI thread (a lazy singleton first touched by a background
    thread, or anything created from a plain `threading.Thread`). Under
    PySide6, a slot that is not a bound `QObject` method (a lambda,
    `functools.partial`) is delivered through the sender's thread; if that
    thread has no running event loop, every such delivery is silently
    dropped. Does nothing when no application exists yet or `obj` is already
    on the GUI thread.
    """
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    if app is not None and obj.thread() is not app.thread():
        obj.moveToThread(app.thread())


class PluginSignals(QObject):
    """Standard signals that all plugins emit.

    Plugins should use these signals to ensure consistent communication
    with Karcytics core and other plugins.

    Attributes:
        status_message: Emitted with a short status string for the UI status bar
        log_message: Emitted with detailed log messages
        state_changed: Emitted when plugin state changes
        undo_available: Emitted with bool indicating if undo is available
        redo_available: Emitted with bool indicating if redo is available
        undo_state_changed: Emitted after any change to the undo history —
            availability, the undo/redo labels, or the clean (saved) state
        analysis_started: Emitted when analysis begins
        analysis_progress: Emitted with int (0-100) for progress
        analysis_complete: Emitted when analysis finishes
        analysis_error: Emitted with error message if analysis fails
        data_changed: Emitted when underlying data changes
    """

    # Status / Logging
    status_message = Signal(str)  # Short status for UI (e.g. "Processing...")
    log_message = Signal(str)  # Detailed log message

    # State Management
    state_changed = Signal()  # Plugin state changed
    undo_available = Signal(bool)  # Whether undo is available
    redo_available = Signal(bool)  # Whether redo is available
    undo_state_changed = Signal()  # Undo labels / availability / clean state changed

    # Analysis Results
    analysis_started = Signal()
    analysis_progress = Signal(int)  # 0-100
    analysis_complete = Signal()
    analysis_error = Signal(str)  # Error message

    # Data Changed
    data_changed = Signal()  # Some data changed (image, parameters, etc)
