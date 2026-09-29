"""Structural ribbon layouts for plugins."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QSpacerItem, QWidget

from .components import BioCancelButton, BioRunButton
from .theme_fallback import theme_manager

_TOOLBAR_CONTAINER_QSS = "background: {BG_DARK}; border-bottom: 1px solid {BORDER};"


class ThemedToolbarContainer(QWidget):
    """Base for a themed toolbar/ribbon container with no opinions beyond
    objectName-scoped background/border theming — no layout, no Run/Cancel
    state machine. `BioRibbon` is one specialization of this; a plugin ribbon
    that doesn't need Run/Cancel can inherit this directly instead of
    duplicating the same container stylesheet by hand.

    Uses `theme_manager.apply_style()` rather than a manual
    `_apply_theme_styles()` re-invocation method: it registers this widget
    once and re-applies the template automatically on every future theme
    change, so subclasses never need their own re-theming boilerplate for
    the container itself.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(self.__class__.__name__)
        theme_manager.apply_style(self, f"QWidget#{self.objectName()} {{ {_TOOLBAR_CONTAINER_QSS} }}")


class BioRibbon(ThemedToolbarContainer):
    """Base class for ribbons that handles horizontal layouts and execution buttons.

    Plugins should inherit from this to create toolbars. It provides default
    visual management of Run/Cancel buttons and overridable methods for hooking
    in background threads.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 5, 10, 5)
        self._layout.setSpacing(10)

        # Action Buttons
        self.run_button = BioRunButton("🧬 Run", self)
        self.cancel_button = BioCancelButton("⏹ Cancel", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()

        self.run_button.clicked.connect(self.start_run)
        self.cancel_button.clicked.connect(self.cancel_run)

    def add_separator(self) -> None:
        """Adds a vertical separator line."""
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self._layout.addWidget(line)

    def add_widget(self, widget: QWidget) -> None:
        """Adds a widget to the ribbon."""
        self._layout.addWidget(widget)

    def add_stretch(self) -> None:
        """Pushes subsequent widgets to the right."""
        self._layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

    def start_run(self) -> None:
        """Triggered when Run is clicked. Visually updates state and calls on_run_started."""
        self.run_button.setEnabled(False)
        self.run_button.hide()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.on_run_started()

    def cancel_run(self) -> None:
        """Triggered when Cancel is clicked. Visually updates state and calls on_run_cancelled."""
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.run_button.setEnabled(True)
        self.run_button.show()
        self.on_run_cancelled()

    def finish_run(self) -> None:
        """Called externally to finish a run cleanly."""
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.run_button.setEnabled(True)
        self.run_button.show()
        self.on_run_finished()

    # --- Overridable Hooks ---
    def on_run_started(self) -> None:
        """Override to start background threads."""
        pass

    def on_run_cancelled(self) -> None:
        """Override to cancel background threads."""
        pass

    def on_run_finished(self) -> None:
        """Override to handle cleanup after run finishes."""
        pass
