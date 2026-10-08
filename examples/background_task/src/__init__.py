"""Entry point for the Multi-Threaded Worker blueprint plugin."""

from PySide6.QtWidgets import QWidget

__version__ = "1.0.0"
__plugin_id__ = "background_task"


def get_panel_class() -> type[QWidget]:
    """Return the main QWidget UI panel for the plugin.

    Returns:
        The BackgroundTaskPanel QWidget subclass.
    """
    from .panel import BackgroundTaskPanel

    return BackgroundTaskPanel


def initialize(context):
    """V2 Plugin Entry Point."""
    panel_class = get_panel_class()
    return panel_class
