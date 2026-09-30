"""Plugin state management for Karcytics SDK.

Provides base class for plugin state that integrates with Karcytics's
undo/redo history system. Enables serialization and deserialization
of plugin states for workflow persistence.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class PluginState(ABC):
    """Base state class for plugin analysis state.

    Subclass this in your plugin and use @dataclass for automatic serialization.
    Enables undo/redo via `PluginBase.push_state()` and `UndoHistory`.

    All fields in your state should be simple types (str, int, float, list, dict)
    to ensure proper serialization. Complex objects should be stored as paths
    or serializable representations.

    This class strictly prevents dynamic attribute assignment. All attributes
    must be declared as fields in the subclass dataclass.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        fields = getattr(type(self), "__dataclass_fields__", {})
        if name not in fields and not hasattr(self, name):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'. "
                "Dynamic attribute assignment is strictly prohibited on PluginState objects."
            )
        super().__setattr__(name, value)

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dictionary for serialization.

        Returns:
            Dictionary representation of the state
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginState:
        """Reconstruct state from dictionary.

        Args:
            data: Dictionary previously produced by to_dict()

        Returns:
            New instance of this state class
        """
        return cls(**data)
