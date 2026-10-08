"""Entry point for the Guided Themed Wizard blueprint plugin."""

from PySide6.QtWidgets import QWidget

__version__ = "1.0.0"
__plugin_id__ = "themed_wizard"


def get_panel_class() -> type[QWidget]:
    """Return the main QWidget UI panel for the plugin.

    Returns:
        The GuidedWizardPanel QWidget subclass.
    """
    from .wizard import GuidedWizardPanel

    return GuidedWizardPanel


def initialize(context):
    """V2 Plugin Entry Point."""
    panel_class = get_panel_class()
    return panel_class
