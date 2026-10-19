"""Karcytics SDK — Software Development Kit for Karcytics desktop plugins.

Provides two main namespaces:
    - plugin: Clean API for plugin development with zero unnecessary dependencies.
    - host: Host-facing APIs for the Core application and SDK signing CLI.
"""

import importlib

__all__ = ["plugin", "host", "interfaces"]


def __getattr__(name: str):
    """Import subpackages on first access.

    `plugin` pulls in PySide6 widgets, so importing it eagerly here would make
    every `karcytics_sdk.*` import — including the CLI — need a GUI-capable
    system (libEGL on Linux).
    """
    if name in __all__:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
