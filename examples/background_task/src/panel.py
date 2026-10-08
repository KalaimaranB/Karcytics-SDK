"""UI Panel implementing thread orchestration for background simulations."""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from karcytics_sdk.plugin import PluginBase
from karcytics_sdk.plugin.components import PrimaryButton, SecondaryButton
from karcytics_sdk.plugin.logging import get_logger

from .engine import SimulationEngine
from .state import CalculationState

logger = get_logger(__name__, "background_task")


class BackgroundTaskPanel(PluginBase):
    """Main QWidget managing the non-blocking numerical calculation lifecycle."""

    def __init__(self, parent=None):
        """Initialize UI forms, input ranges, and connect action triggers.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(plugin_id="background_task", parent=parent)
        self.state = CalculationState()
        self.active_worker = None

        # Standard Layout
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header Title
        self.header = QLabel("⚙️ High-Performance Concurrency")
        self.header.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.header)

        # Inputs Form
        inputs_layout = QHBoxLayout()

        inputs_layout.addWidget(QLabel("Iterations:"))
        self.iter_spin = QSpinBox()
        self.iter_spin.setRange(5, 100)
        self.iter_spin.setValue(self.state.iterations)
        inputs_layout.addWidget(self.iter_spin)

        inputs_layout.addWidget(QLabel("Delay (ms):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(50, 1000)
        self.delay_spin.setSingleStep(50)
        self.delay_spin.setValue(self.state.delay_ms)
        inputs_layout.addWidget(self.delay_spin)

        layout.addLayout(inputs_layout)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.start_btn = PrimaryButton("Start Analysis")
        self.cancel_btn = SecondaryButton("Cancel")
        self.cancel_btn.setEnabled(False)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        # Progress Tracking
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel("Status: Idle")
        layout.addWidget(self.status_lbl)

        # Log Terminal Outputs
        layout.addWidget(QLabel("Calculation Diagnostic Console:"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("font-family: Courier New, monospace; background: #1e1e1e; color: #a9b7c6;")
        layout.addWidget(self.console)

        # Connections
        self.start_btn.clicked.connect(self.on_start)
        self.cancel_btn.clicked.connect(self.on_cancel)
        self.iter_spin.valueChanged.connect(self.on_iter_changed)
        self.delay_spin.valueChanged.connect(self.on_delay_changed)

    def log_console(self, text: str) -> None:
        """Write a formatted message to the on-screen logger console.

        Args:
            text: Message string to output.
        """
        self.console.append(text)
        logger.debug(text)

    @Slot()
    def on_start(self) -> None:
        """Trigger background thread execution mapping inputs to workers."""
        self.log_console("Preparing background mathematical worker thread...")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setText("Status: Processing...")

        # 1. Instantiate the Engine subclass
        engine = SimulationEngine()

        # 2. Allocate the worker via PluginBase helper
        self.active_worker = self.create_worker(engine, self.state)

        # 3. Bind worker signals to local UI slots
        self.active_worker.signals.progress.connect(self.on_worker_progress)
        self.active_worker.signals.completed.connect(self.on_worker_success)
        self.active_worker.signals.failed.connect(self.on_worker_failure)

        # 4. Delegate to thread pool
        self.start_worker(self.active_worker)
        self.log_console("Worker dispatched to QThreadPool successfully.")

    @Slot()
    def on_cancel(self) -> None:
        """Signal target background worker to abort calculations early."""
        if self.active_worker:
            self.log_console("Cancellation request received. Aborting...")
            self.active_worker.cancel()
            self.cancel_btn.setEnabled(False)

    @Slot(int, str)
    def on_worker_progress(self, progress: int, msg: str) -> None:
        """Update progress values on the UI bar dynamically.

        Args:
            progress: Integer value from 0 to 100.
            msg: Descriptive update label text.
        """
        self.progress_bar.setValue(progress)
        self.status_lbl.setText(f"Status: {msg}")
        self.log_console(f"[Progress {progress}%] {msg}")

    @Slot(object)
    def on_worker_success(self, results: dict) -> None:
        """Assemble finished results, update state history, and release buttons.

        Args:
            results: Dictionary returned from SimulationEngine.run.
        """
        self.state.results = results.get("results", [])
        self.push_state()  # Commit complete state to undo history

        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_lbl.setText("Status: Completed!")
        self.log_console(f"SUCCESS: Calculated {len(self.state.results)} results.")
        self.log_console(f"Output list: {self.state.results[:3]}... [Truncated]")

    @Slot(str)
    def on_worker_failure(self, error_msg: str) -> None:
        """Handle errors cleanly, preventing application segmentation crashes.

        Args:
            error_msg: String description of the exception.
        """
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_lbl.setText("Status: Failed!")
        self.log_console(f"ERROR: Calculation worker crashed with exception: {error_msg}")

    @Slot(int)
    def on_iter_changed(self, value: int) -> None:
        """Update iterations values inside state dynamically.

        Args:
            value: Current spinbox count value.
        """
        self.state.iterations = value

    @Slot(int)
    def on_delay_changed(self, value: int) -> None:
        """Update delay parameters inside state dynamically.

        Args:
            value: Current delay duration in milliseconds.
        """
        self.state.delay_ms = value
