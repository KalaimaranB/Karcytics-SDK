"""Entry point for the Hello World blueprint plugin."""

from PySide6.QtWidgets import QWidget

__version__ = "1.0.0"
__plugin_id__ = "hello_world"


def get_panel_class() -> type[QWidget]:
    """Return the main QWidget UI panel for the plugin.

    Returns:
        The MyFirstPanel QWidget subclass.
    """
    from .ui import MyFirstPanel

    return MyFirstPanel


def initialize(context):
    """V2 Plugin Entry Point."""
    panel_class = get_panel_class()
    return panel_class
