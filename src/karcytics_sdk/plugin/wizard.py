"""Wizard UI framework for Karcytics SDK.

Provides step-based wizard components for creating multi-step interfaces.
Each step can have its own UI and validation logic.
"""

from abc import ABC, abstractmethod

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from karcytics.ui.theme import Colors, Fonts, theme_manager
except ImportError:
    # See base.py: theme_fallback is the SDK's canonical fallback for an
    # isolated plugin .venv, where theme_changed actually fires on theme
    # switches instead of silently dropping every connection.
    from .theme_fallback import Colors, Fonts, theme_manager


class StepIndicator(QWidget):
    """Vertical stepper showing numbered circles with labels.

    Displays the wizard steps with visual indicators showing completed,
    current, and upcoming steps.
    """

    step_clicked = Signal(int)

    def __init__(self, steps: list[str], parent=None) -> None:
        """Create a step indicator.

        Args:
            steps: List of step labels
            parent: Parent widget
        """
        super().__init__(parent)
        self._steps = steps
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._circles: list[QLabel] = []
        self._texts: list[QLabel] = []
        self._current_idx = 0
        self._build()
        theme_manager.theme_changed.connect(self._refresh_styles)
        self.set_current(0)

    def _build(self) -> None:
        """Build the step indicator UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        for i, label in enumerate(self._steps):
            row_widget = QWidget()
            row_widget.setCursor(Qt.CursorShape.PointingHandCursor)
            row_widget.mousePressEvent = lambda e, idx=i: self.step_clicked.emit(idx)

            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            circle = QLabel(str(i + 1))
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            circle.setFixedSize(22, 22)

            text = QLabel(label)
            text.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

            row.addWidget(circle)
            row.addWidget(text, stretch=1)

            self._circles.append(circle)
            self._texts.append(text)

            layout.addWidget(row_widget)

            # Connector line
            if i < len(self._steps) - 1:
                line_wrap = QHBoxLayout()
                line_wrap.setContentsMargins(10, 0, 0, 0)
                connector = QLabel()
                connector.setFixedSize(2, 8)
                connector.setStyleSheet(f"background: {Colors.BORDER}; border-radius: 1px;")
                line_wrap.addWidget(connector)
                line_wrap.addStretch()
                layout.addLayout(line_wrap)

    @staticmethod
    def _circle_css(active: bool, done: bool) -> str:
        """Get CSS for circle based on state."""
        if done:
            return (
                f"background: {Colors.ACCENT_SUCCESS}; color: {Colors.BG_DARKEST};"
                f" border-radius: 12px; font-size: 11px; font-weight: 700; border: none;"
            )
        if active:
            return (
                f"background: {Colors.ACCENT_PRIMARY}; color: {Colors.BG_DARKEST};"
                f" border-radius: 12px; font-size: 11px; font-weight: 700; border: none;"
            )
        return (
            f"background: {Colors.BG_MEDIUM}; color: {Colors.FG_DISABLED};"
            f" border-radius: 12px; font-size: 11px; font-weight: 600;"
            f" border: 1px solid {Colors.BORDER};"
        )

    def set_current(self, idx: int) -> None:
        """Update visual state for the given step index.

        Args:
            idx: Index of the current step
        """
        self._current_idx = idx
        for i, (circle, text) in enumerate(zip(self._circles, self._texts, strict=False)):
            if i < idx:
                circle.setText("✓")
                circle.setStyleSheet(self._circle_css(False, True))
                text.setStyleSheet(f"font-size: {Fonts.SIZE_SMALL}px; color: {Colors.ACCENT_SUCCESS};")
            elif i == idx:
                circle.setText(str(i + 1))
                circle.setStyleSheet(self._circle_css(True, False))
                text.setStyleSheet(
                    f"font-size: {Fonts.SIZE_SMALL}px; font-weight: 700; color: {Colors.ACCENT_PRIMARY};"
                )
            else:
                circle.setText(str(i + 1))
                circle.setStyleSheet(self._circle_css(False, False))
                text.setStyleSheet(f"font-size: {Fonts.SIZE_SMALL}px; color: {Colors.FG_DISABLED};")

    def _refresh_styles(self) -> None:
        """Re-apply current theme styles when the theme updates."""
        self.set_current(self._current_idx)
        for _i, widget in enumerate(self.findChildren(QWidget)):
            if isinstance(widget, QWidget):
                widget.update()


class WizardStep(ABC):
    """Base class for a wizard step.

    Each step is self-contained: builds its own UI, runs its own logic when
    the user clicks Next, and can validate user input before advancing.

    Example:
        >>> class InputStep(WizardStep):
        ...     label = "Input Parameters"
        ...
        ...     def build_page(self, panel: WizardPanel) -> QWidget:
        ...         page = QWidget()
        ...         layout = QVBoxLayout(page)
        ...         self.threshold_spin = QDoubleSpinBox()
        ...         layout.addWidget(QLabel("Threshold:"))
        ...         layout.addWidget(self.threshold_spin)
        ...         return page
        ...
        ...     def on_next(self, panel: WizardPanel) -> bool:
        ...         panel.state['threshold'] = self.threshold_spin.value()
        ...         return True  # Allow advancing
    """

    label: str = "Step"
    is_terminal: bool = False

    @abstractmethod
    def build_page(self, panel: "WizardPanel") -> QWidget:
        """Create and return the page widget for this step.

        Args:
            panel: The WizardPanel parent

        Returns:
            QWidget to display for this step
        """
        pass

    def on_enter(self) -> None:
        """Called when step becomes active.

        Override to initialize UI or load data when entering this step.
        """
        pass

    @abstractmethod
    def on_next(self, panel: "WizardPanel") -> bool:
        """Run this step's logic when user clicks Next.

        Validate input and update panel state as needed.

        Args:
            panel: The WizardPanel parent

        Returns:
            True to advance to next step, False to block navigation
        """
        pass

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        """Wrap page in a frameless scroll area.

        Useful when your step content might be larger than the available space.

        Args:
            page: Widget to wrap

        Returns:
            QScrollArea containing the page
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _row(label_text: str, widget: QWidget, *, label_width: int = 130) -> QHBoxLayout:
        """Return a QHBoxLayout with a fixed-width label and widget.

        Useful for creating consistent form-like layouts with aligned labels.

        Args:
            label_text: Text for the label
            widget: Widget to place next to the label
            label_width: Width of the label in pixels

        Returns:
            QHBoxLayout with the label and widget
        """
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setFixedWidth(label_width)
        row.addWidget(lbl)
        row.addWidget(widget)
        return row


class WizardPanel(QWidget):
    """Runtime shell for a list of WizardStep objects.

    Manages navigation between steps, maintains step indicator, and handles
    the Back/Next buttons. Also proxies PluginSignals for compatibility.

    Example:
        >>> steps = [InputStep(), AnalysisStep(), ResultsStep()]
        >>> panel = WizardPanel(steps, "My Analysis Wizard")
        >>> panel.state_changed.connect(on_state_changed)
    """

    # Proxy signals from PluginSignals for convenience
    status_message = Signal(str)
    state_changed = Signal()
    analysis_started = Signal()
    analysis_progress = Signal(int)
    analysis_complete = Signal()
    analysis_error = Signal(str)

    def __init__(self, steps: list[WizardStep], title: str = "", parent=None) -> None:
        """Create a wizard panel.

        Args:
            steps: List of WizardStep instances
            title: Title to display at the top
            parent: Parent widget
        """
        super().__init__(parent)

        self._steps = steps
        self._idx = 0
        self._max_idx = 0
        self._canvas = None

        self._setup_ui(title)

        for step in self._steps:
            if hasattr(step, "on_panel_ready"):
                step.on_panel_ready(self)

    def _setup_ui(self, title: str) -> None:
        """Build the wizard UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        if title:
            lbl = QLabel(f"🧬  {title}")
            lbl.setObjectName("stepTitle")
            lbl.setMinimumHeight(28)
            layout.addWidget(lbl)

        labels = [s.label for s in self._steps]
        self._indicator = StepIndicator(labels)
        self._indicator.step_clicked.connect(self.go_to_step)
        layout.addWidget(self._indicator)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {Colors.BORDER};")
        layout.addWidget(sep)

        self._stacked = QStackedWidget()
        for step in self._steps:
            page = step.build_page(self)
            if isinstance(page, QScrollArea):
                self._stacked.addWidget(page)
            else:
                self._stacked.addWidget(WizardStep._scroll(page))
        layout.addWidget(self._stacked)

        sep2 = QWidget()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {Colors.BORDER};")
        layout.addWidget(sep2)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._btn_back = QPushButton("← Back")
        self._btn_back.clicked.connect(self.go_back)
        button_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton("Next →")
        self._btn_next.clicked.connect(self.go_next)
        button_layout.addWidget(self._btn_next)

        layout.addLayout(button_layout)
        self._update_buttons()

        theme_manager.theme_changed.connect(self._apply_theme_styles)
        self._apply_theme_styles()

    def _update_buttons(self) -> None:
        """Update button states based on current step."""
        self._btn_back.setEnabled(self._idx > 0)
        current = self._steps[self._idx]
        if current.is_terminal:
            self._btn_next.setText("Done ✓")
        else:
            self._btn_next.setText("Next →" if self._idx < len(self._steps) - 1 else "Done ✓")

    def _apply_theme_styles(self) -> None:
        """Reapply theme-aware styles to the wizard panel widgets."""
        self.setStyleSheet(f"QWidget {{ background: {Colors.BG_DARKEST}; color: {Colors.FG_PRIMARY}; }}")
        self._btn_back.setStyleSheet(
            f"QPushButton {{ background: {Colors.BG_MEDIUM}; color: {Colors.FG_PRIMARY}; border: 1px solid {Colors.BORDER}; border-radius: 6px; padding: 10px 14px; }}"
            f"QPushButton:hover {{ background: {Colors.BG_LIGHT}; }}"
        )
        self._btn_next.setStyleSheet(
            f"QPushButton {{ background: {Colors.ACCENT_PRIMARY}; color: {Colors.BG_DARKEST}; border: none; border-radius: 6px; padding: 10px 14px; }}"
            f"QPushButton:hover {{ background: {Colors.ACCENT_PRIMARY_HOVER}; }}"
        )
        self._indicator.set_current(self._idx)

        # Force recursion into child widgets that might have custom styling
        for widget in self.findChildren(QWidget):
            if hasattr(widget, "_apply_theme_styles") and widget is not self:
                widget._apply_theme_styles()
            elif hasattr(widget, "refresh_styles"):
                widget.refresh_styles()

            # Explicitly refresh any labels or frames that might be using cached theme colors
            if isinstance(widget, (QLabel, QFrame)):
                widget.update()

    def go_back(self) -> None:
        """Go to previous step."""
        if self._idx > 0:
            self._idx -= 1
            self._stacked.setCurrentIndex(self._idx)
            self._indicator.set_current(self._idx)
            self._update_buttons()
            self._steps[self._idx].on_enter()

    def go_next(self) -> None:
        """Attempt to go to next step (calls current step's on_next)."""
        if self._steps[self._idx].on_next(self) and self._idx < len(self._steps) - 1:
            self._idx += 1
            self._max_idx = max(self._max_idx, self._idx)
            self._stacked.setCurrentIndex(self._idx)
            self._indicator.set_current(self._idx)
            self._update_buttons()
            self._steps[self._idx].on_enter()

    def go_to_step(self, idx: int) -> None:
        """Allow jumping to a completed step.

        Args:
            idx: Index of step to jump to
        """
        if idx <= self._max_idx:
            self._idx = idx
            self._stacked.setCurrentIndex(self._idx)
            self._indicator.set_current(self._idx)
            self._update_buttons()
            self._steps[self._idx].on_enter()

    @property
    def current_step(self) -> WizardStep:
        """Get the current step."""
        return self._steps[self._idx]

    @property
    def current_index(self) -> int:
        """Get the current step index."""
        return self._idx
