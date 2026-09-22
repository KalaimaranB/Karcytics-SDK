"""File I/O utilities for Karcytics SDK.

Provides convenient wrappers for JSON serialization and configuration
management for plugins.
"""

import json
import logging
from pathlib import Path
from typing import Any

from .preferences import PreferenceManagerProtocol

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# JSON UTILITIES
# ──────────────────────────────────────────────────────────────────────────────


def load_json(path: str) -> dict[str, Any]:
    """Load JSON from file.

    Args:
        path: File path

    Returns:
        Parsed JSON dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON is invalid
    """
    with open(path) as f:
        return json.load(f)


def save_json(path: str, data: dict[str, Any], pretty: bool = True) -> None:
    """Save JSON to file.

    Args:
        path: File path
        data: Dictionary to save
        pretty: If True, indent for readability (2 spaces)
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2 if pretty else None)


def read_binary(path: Path | str) -> bytes:
    """Read binary data from a file."""
    with open(path, "rb") as f:
        return f.read()


def write_binary(path: Path | str, data: bytes) -> None:
    """Write binary data to a file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ──────────────────────────────────────────────────────────────────────────────
# PLUGIN CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────


class PluginConfig(PreferenceManagerProtocol):
    """Simple configuration management for plugins.

    Stores settings in JSON in ~/.karcytics/plugin_configs/{plugin_id}.json

    This is useful for persisting user settings across sessions, like
    the last used parameters or paths.

    One instance per `plugin_id` per process (see `__new__`): two independent
    `PluginConfig` objects for the same plugin would each cache their own
    snapshot of the JSON file in `.data`, and whichever calls `.save()` last
    would silently overwrite the other's unsaved changes with its own stale
    snapshot. A plugin's own config wrapper (e.g. a `FlowConfig` holding
    `PluginConfig("flow_cytometry")`) and any SDK feature that also reaches
    for `PluginConfig(same_plugin_id)` — the workflow-autosave preference,
    for instance — now always share the exact same object and `.data` dict.

    Example:
        >>> config = PluginConfig('my_plugin')
        >>> config.set('threshold', 0.5)
        >>> config.set('last_image_dir', '/path/to/images')
        >>> threshold = config.get('threshold', default=0.0)
        >>> config.save()
    """

    _instances: dict[str, "PluginConfig"] = {}

    def __new__(cls, plugin_id: str) -> "PluginConfig":
        instance = cls._instances.get(plugin_id)
        if instance is None:
            instance = super().__new__(cls)
            cls._instances[plugin_id] = instance
        return instance

    def __init__(self, plugin_id: str):
        """Initialize config manager.

        Args:
            plugin_id: Unique plugin identifier (used for filename)
        """
        if getattr(self, "_initialized", False):
            return
        self.plugin_id = plugin_id
        self.config_dir = Path.home() / ".karcytics" / "plugin_configs"
        self.config_file = self.config_dir / f"{plugin_id}.json"
        self.data: dict[str, Any] = {}
        self.load()
        self._initialized = True

    def load(self) -> None:
        """Load config from disk.

        Called automatically in __init__. Call again to reload from disk.
        """
        if self.config_file.exists():
            try:
                self.data = load_json(str(self.config_file))
            except Exception as e:
                logger.warning(f"Failed to load config for {self.plugin_id}: {e}")
                self.data = {}
        else:
            self.data = {}

    def save(self) -> None:
        """Save config to disk.

        Raises:
            IOError: If file cannot be written
        """
        try:
            save_json(str(self.config_file), self.data)
        except Exception as e:
            logger.error(f"Failed to save config for {self.plugin_id}: {e}")

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value.

        Args:
            key: Configuration key
            value: Configuration value (should be JSON-serializable)
        """
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value.

        Args:
            key: Configuration key
            default: Value to return if key doesn't exist

        Returns:
            Configuration value or default
        """
        return self.data.get(key, default)

    def has(self, key: str) -> bool:
        """Check if a key exists.

        Args:
            key: Configuration key

        Returns:
            True if key exists
        """
        return key in self.data

    def clear(self) -> None:
        """Clear all config values (does not delete file until save())."""
        self.data.clear()

    def __getitem__(self, key: str):
        """Get value using dictionary syntax."""
        return self.data[key]

    def __setitem__(self, key: str, value: Any):
        """Set value using dictionary syntax."""
        self.data[key] = value


# Alias for unified preference manager terminology
PluginPreferenceManager = PluginConfig

# ──────────────────────────────────────────────────────────────────────────────
# PLUGIN LOGGING
# ──────────────────────────────────────────────────────────────────────────────


def get_plugin_logger(plugin_id: str) -> logging.Logger:
    """Get a logger for a plugin.

    Args:
        plugin_id: Plugin identifier

    Returns:
        Configured logger with plugin name prefixed to 'karcytics.plugins'

    Example:
        >>> logger = get_plugin_logger("my_plugin")
        >>> logger.info("Plugin initialized")  # Logs to "karcytics.plugins.my_plugin"
    """
    return logging.getLogger(f"karcytics.plugins.{plugin_id}")
