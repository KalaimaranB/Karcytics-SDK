"""Standard plugin signals for Karcytics SDK.

This module defines all standard PyQt6 signals that plugins should emit
to communicate state changes and results to the Karcytics core application.
"""

from PyQt6.QtCore import QObject, pyqtSignal


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
    status_message = pyqtSignal(str)  # Short status for UI (e.g. "Processing...")
    log_message = pyqtSignal(str)  # Detailed log message

    # State Management
    state_changed = pyqtSignal()  # Plugin state changed
    undo_available = pyqtSignal(bool)  # Whether undo is available
    redo_available = pyqtSignal(bool)  # Whether redo is available
    undo_state_changed = pyqtSignal()  # Undo labels / availability / clean state changed

    # Analysis Results
    analysis_started = pyqtSignal()
    analysis_progress = pyqtSignal(int)  # 0-100
    analysis_complete = pyqtSignal()
    analysis_error = pyqtSignal(str)  # Error message

    # Data Changed
    data_changed = pyqtSignal()  # Some data changed (image, parameters, etc)
