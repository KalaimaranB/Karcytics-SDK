"""UI Panel implementing a multi-step themed Guided Wizard."""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from karcytics_sdk.plugin import PluginBase, PluginState
from karcytics_sdk.plugin.components import PrimaryButton, SecondaryButton
from karcytics_sdk.plugin.dialogs import show_info
from karcytics_sdk.plugin.wizard import WizardPanel, WizardStep

from .config import WizardSettings


class WizardState(PluginState):
    """Model tracking guided choices through the step-by-step forms."""

    role: str = "Researcher"
    gpu_acceleration: bool = False
    finished: bool = False


# ── Step 1: Identity Selection Onboarding ──────────────────────────────────
class RoleStep(WizardStep):
    """Guided Step 1: Selecting the developer primary workstation role."""

    label = "Select Workspace Role"
    role_combo: QComboBox

    def __init__(self, state: WizardState):
        """Initialize the step interface mapping options to state.

        Args:
            state: Shared WizardState instance.
        """
        self.state = state

    def build_page(self, panel: WizardPanel) -> QWidget:
        """Create and return the page widget for this step."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Step 1: Configure your active workstation profile."))

        # Dropdown input selector
        self.role_combo = QComboBox()
        self.role_combo.addItems(["Researcher", "Lab Technician", "Principal Investigator", "QA Engineer"])
        self.role_combo.setCurrentText(self.state.role)
        layout.addWidget(self.role_combo)

        # Connect text changes
        self.role_combo.currentTextChanged.connect(self.on_role_changed)
        return page

    @Slot(str)
    def on_role_changed(self, text: str) -> None:
        """Update role inside state dynamically on changes.

        Args:
            text: Selected dropdown role string.
        """
        self.state.role = text

    def on_next(self, panel: WizardPanel) -> bool:
        """Validate input and advance to next step.

        Returns:
            True to advance.
        """
        return True


# ── Step 2: System Performance Options ─────────────────────────────────────
class OptionStep(WizardStep):
    """Guided Step 2: System performance configurations."""

    label = "Performance Preferences"
    gpu_check: QCheckBox

    def __init__(self, state: WizardState):
        """Initialize performance selections.

        Args:
            state: Shared WizardState instance.
        """
        self.state = state

    def build_page(self, panel: WizardPanel) -> QWidget:
        """Create and return the page widget for this step."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Step 2: Choose your processing calculation preferences."))

        # Checkbox input selector
        self.gpu_check = QCheckBox("Enable GPU Acceleration")
        self.gpu_check.setChecked(self.state.gpu_acceleration)
        layout.addWidget(self.gpu_check)

        # Connect checkbox changes
        self.gpu_check.toggled.connect(self.on_gpu_toggled)
        return page

    @Slot(bool)
    def on_gpu_toggled(self, checked: bool) -> None:
        """Update GPU settings inside state dynamically.

        Args:
            checked: Checkbox boolean state.
        """
        self.state.gpu_acceleration = checked

    def on_next(self, panel: WizardPanel) -> bool:
        """Validate input and advance.

        Returns:
            True to proceed.
        """
        return True


# ── Step 3: Onboarding Summary ─────────────────────────────────────────────
class SummaryStep(WizardStep):
    """Guided Step 3: Summarize choices and commit configurations."""

    label = "Verification & Finish"
    summary_lbl: QLabel

    def __init__(self, state: WizardState):
        """Initialize confirmation screen.

        Args:
            state: Shared WizardState instance.
        """
        self.state = state

    def build_page(self, panel: WizardPanel) -> QWidget:
        """Create and return the page widget for this step."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.summary_lbl = QLabel()
        self.summary_lbl.setWordWrap(True)
        layout.addWidget(self.summary_lbl)
        return page

    def on_enter(self) -> None:
        """Triggered automatically when the wizard transitions to this step."""
        gpu_status = "ENABLED" if self.state.gpu_acceleration else "DISABLED"
        summary_text = (
            f"Workstation Profile Summary:\n\n"
            f"Workspace Role: {self.state.role}\n"
            f"GPU Hardware Acceleration: {gpu_status}\n\n"
            f"Press 'Finish' to save these selections permanently onto your machine config."
        )
        if self.summary_lbl is not None:
            self.summary_lbl.setText(summary_text)

    def on_next(self, panel: WizardPanel) -> bool:
        """Perform ultimate checks before completing the wizard journey.

        Returns:
            True to finish.
        """
        self.state.finished = True
        return True


# ── GuidedWizardPanel: Main Wizard Orchestrator ────────────────────────────
class GuidedWizardPanel(PluginBase):
    """Main QWidget representing the themed wizard blueprint."""

    def __init__(self, parent=None):
        """Initialize the multi-page wizard panel using standard configs.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(plugin_id="themed_wizard", parent=parent)
        self.state = WizardState()
        self.settings = WizardSettings()

        # Load existing local disk configurations
        self.state.role = self.settings.developer_role
        self.state.gpu_acceleration = self.settings.enable_gpu
        self.state.finished = self.settings.setup_completed

        # Setup layout
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header Title
        self.title_lbl = QLabel("🧙‍♂️ Onboarding Guided Setup Wizard")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.title_lbl)

        # Instantiate WizardPanel helper
        self.wizard = WizardPanel()

        # Add steps
        self.wizard.add_step(RoleStep(self.state))
        self.wizard.add_step(OptionStep(self.state))
        self.wizard.add_step(SummaryStep(self.state))

        layout.addWidget(self.wizard)

        # Action controllers
        btn_layout = QHBoxLayout()
        self.prev_btn = SecondaryButton("Back")
        self.next_btn = PrimaryButton("Next")

        btn_layout.addWidget(self.prev_btn)
        btn_layout.addWidget(self.next_btn)
        layout.addLayout(btn_layout)

        # Connect slots
        self.prev_btn.clicked.connect(self.on_prev)
        self.next_btn.clicked.connect(self.on_next)

        # Bind wizard-specific transitions to button updates
        self.wizard.step_changed.connect(self.update_wizard_buttons)
        self.update_wizard_buttons()

    def update_wizard_buttons(self) -> None:
        """Synchronize button text and accessibility with the active wizard index."""
        current_step = self.wizard.current_index()
        total_steps = self.wizard.step_count()

        self.prev_btn.setEnabled(current_step > 0)

        if current_step == total_steps - 1:
            self.next_btn.setText("Finish")
        else:
            self.next_btn.setText("Next")

    @Slot()
    def on_prev(self) -> None:
        """Step back to the previous wizard panel."""
        self.wizard.prev_step()

    @Slot()
    def on_next(self) -> None:
        """Validate choices and progress forward, or commit parameters on finish."""
        current_step = self.wizard.current_index()
        total_steps = self.wizard.step_count()

        # Perform step-specific input validations
        if not self.wizard.validate_current_step():
            return

        if current_step == total_steps - 1:
            # We are on the Summary screen: Commit to local disk
            self.settings.developer_role = self.state.role
            self.settings.enable_gpu = self.state.gpu_acceleration
            self.settings.setup_completed = self.state.finished

            # Record this event in the local undo/redo stack
            self.push_state()

            show_info(
                self,
                "Wizard Completed!",
                f"Your configurations have been persisted locally! (Role: {self.state.role})",
            )
            # Restart setup
            self.wizard.set_index(0)
        else:
            self.wizard.next_step()

    def _apply_theme_styles(self) -> None:
        """Override standard theme styles to adapt layout dynamically.

        Demonstrates real-time responsive branding based on parent app preferences.
        """
        super()._apply_theme_styles()
        # Repaint components or labels here customly if needed
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #9B5DE5;")
