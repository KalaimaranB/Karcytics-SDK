"""Karcytics SDK Logging Utilities.

Provides Karcytics-aware loggers that automatically attach plugin metadata
and route to the central diagnostic engine.
"""

import logging
import logging.handlers
from pathlib import Path

# Third-party libraries whose own DEBUG-level logging is compiler/rendering
# internals, not anything a plugin author would ever want to see. Since
# configure_plugin_logging() sets the *root* logger to DEBUG (so a plugin's
# own unqualified logging.debug() calls reach the log file), any of these
# libraries left at their default (unset) level would inherit DEBUG too and
# flood the log — e.g. numba dumps full bytecode-flow/SSA analysis for every
# function it JIT-compiles. Pinning them back to WARNING here keeps root at
# DEBUG for plugin code while silencing dependency-internal noise.
_NOISY_THIRD_PARTY_LOGGERS = ("numba", "llvmlite", "matplotlib")


class PluginLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that injects plugin_id into every log record."""

    def __init__(self, logger: logging.Logger, plugin_id: str):
        """Initialize the logger adapter with a specific plugin identifier.

        Args:
            logger: The parent Python logger instance to adapt.
            plugin_id: The unique identifier of the target plugin.
        """
        super().__init__(logger, {"plugin_id": plugin_id})
        self.plugin_id = plugin_id

    def process(self, msg, kwargs):
        """Process log messages to inject the plugin metadata context.

        Args:
            msg: The actual log message content.
            kwargs: Thread or level attributes associated with the log record.

        Returns:
            A tuple of (processed_message, kwargs) containing the updated extra dict.
        """
        # Ensure plugin_id is in the extra dict for the handler to find
        extra = kwargs.get("extra", {})
        extra["plugin_id"] = self.plugin_id
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str, plugin_id: str | None = None) -> logging.Logger | PluginLoggerAdapter:
    """Get a logger instance, optionally adapted for a specific plugin.

    Args:
        name: The name of the logger (usually __name__)
        plugin_id: The ID of the plugin this logger belongs to

    Returns:
        A logging.Logger or PluginLoggerAdapter instance.
    """
    logger = logging.getLogger(name)
    if plugin_id:
        return PluginLoggerAdapter(logger, plugin_id)
    return logger


def configure_plugin_logging(plugin_id: str, log_dir: Path | None = None) -> Path:
    """Give an isolated plugin worker its own durable, local log file.

    Without this, an isolated worker's root logger has no handlers at all:
    Python's own last-resort handler only ever prints WARNING+ to stderr, so
    every `.debug()`/`.info()` call a plugin makes is silently dropped, not
    just unrelayed. `PluginDaemon._stderr_reader_loop` on the Hub side only
    tails whatever *does* reach stderr, and only the last 200 lines, so
    nothing survives a crash on disk regardless. This adds a rotating file
    handler under `~/.karcytics/logs/plugin_workers/<plugin_id>.log` (kept
    distinct from the Hub-relayed `logs/plugins/<plugin_id>.log` path so the
    two processes never contend as concurrent writers on one file) and a
    stderr handler so DEBUG+ actually reaches the existing relay too.
    Idempotent — safe to call more than once for the same process.
    """
    root = logging.getLogger()
    if getattr(root, "_karcytics_plugin_logging_configured", False):
        return root._karcytics_plugin_log_file  # type: ignore[attr-defined]

    if log_dir is None:
        log_dir = Path.home() / ".karcytics" / "logs" / "plugin_workers"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{plugin_id}.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root.setLevel(logging.DEBUG)
    for noisy_logger_name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root._karcytics_plugin_logging_configured = True  # type: ignore[attr-defined]
    root._karcytics_plugin_log_file = log_file  # type: ignore[attr-defined]
    return log_file
