"""UI Panel implementation for the Hello World blueprint plugin."""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from karcytics_sdk.plugin import PluginBase, PluginState
from karcytics_sdk.plugin.components import PrimaryButton, SecondaryButton
from karcytics_sdk.plugin.dialogs import show_info, show_warning


class HelloWorldState(PluginState):
    """Simple state subclass for the Hello World plugin."""

    user_name: str = ""


class MyFirstPanel(PluginBase):
    """Main QWidget representing the hello world developer interface."""

    def __init__(self, parent=None):
        """Initialize the panel layout and set up theme hooks.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(plugin_id="hello_world", parent=parent)
        self.state = HelloWorldState()

        # Create Layouts
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Title Header
        self.title_label = QLabel("🧬 Welcome to Karcytics SDK!")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(self.title_label)

        # Description
        self.desc_label = QLabel(
            "This is a minimal blueprint showing how to construct plugin interfaces "
            "using standardized semantic buttons and dialog prompts."
        )
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        # Input Field Form
        form_layout = QHBoxLayout()
        form_layout.addWidget(QLabel("Your Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Enter your name here...")
        form_layout.addWidget(self.name_input)
        layout.addLayout(form_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        self.greet_btn = PrimaryButton("Greet Me")
        self.clear_btn = SecondaryButton("Clear")

        btn_layout.addWidget(self.greet_btn)
        btn_layout.addWidget(self.clear_btn)
        layout.addLayout(btn_layout)

        # Connect Slots
        self.greet_btn.clicked.connect(self.on_greet)
        self.clear_btn.clicked.connect(self.on_clear)

        # Register input change for state undo/redo capability
        self.name_input.textChanged.connect(self.on_name_changed)

    @Slot()
    def on_greet(self) -> None:
        """Handle the greet action, displaying a pop-up confirmation dialog."""
        name = self.name_input.text().strip()
        if not name:
            show_warning(self, "Name Missing", "Please enter your name in the input field first!")
            return

        self.state.user_name = name
        self.push_state()  # Register this action into the Undo/Redo stack

        show_info(
            self,
            "Greetings!",
            f"Hello, {name}! You have successfully integrated a custom Karcytics plugin widget.",
        )

    @Slot()
    def on_clear(self) -> None:
        """Clear the input field and reset local plugin state."""
        self.name_input.clear()
        self.state.user_name = ""
        self.push_state()

    @Slot(str)
    def on_name_changed(self, text: str) -> None:
        """Track text changes silently in the background state object.

        Args:
            text: Current value of the input field.
        """
        self.state.user_name = text
