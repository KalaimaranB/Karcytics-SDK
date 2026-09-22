"""Workflow autosave for Karcytics plugins.

A plugin opts in with `PluginBase.setup_workflow_autosave()` rather than
building this itself, so the 15-minute interval, the enabled/disabled
preference, and the info/warning toast choice are implemented exactly once
and shared by every module — the reason this lives in the SDK rather than
inside `karcytics_plugins.flow_cytometry`.

This module owns no knowledge of *how* a plugin saves its workflow: the
plugin supplies `has_saved_once` and `save` callables, since "save a
workflow" means something different per plugin (a project-manager-backed
save for one module might be a plain file write for another). That keeps
`WorkflowAutosaveController` itself closed for modification but open for
any plugin to extend by composition (Open/Closed), and free of a dependency
on any one module's persistence layer (Single Responsibility, Dependency
Inversion).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

from .io import PluginConfig
from .preferences import PreferenceManagerProtocol
from .toast import STYLE_INFO, STYLE_WARNING, show_toast

logger = logging.getLogger(__name__)

#: How often the reminder/autosave loop fires.
DEFAULT_INTERVAL_MS = 15 * 60 * 1000

#: The key this controller stores its on/off preference under, inside
#: whichever `PreferenceManagerProtocol` it's given (a plugin's own
#: `PluginConfig` by default).
PREFERENCE_KEY = "autosave_workflows"


class WorkflowAutosaveController(QObject):
    """Runs the "autosave every 15 minutes, or nudge if it can't" loop for one plugin.

    Every tick:
      - If the preference is on *and* the plugin reports a workflow has been
        saved manually at least once, calls `save(on_done)` and shows an
        info toast once `on_done(True)` fires (a warning toast instead if
        `on_done(False)` — the save itself failed).
      - Otherwise (preference off, or nothing saved yet) shows a warning
        toast asking the user to save. The loop keeps running either way.

    Call `notify_saved()` after any *manual* save so the countdown restarts
    from that point instead of firing again moments later.
    """

    def __init__(  # noqa: PLR0913 - a small DI-style constructor; each param is a distinct collaborator, not a group that bundles cleanly
        self,
        plugin_id: str,
        has_saved_once: Callable[[], bool],
        save: Callable[[Callable[[bool], None]], None],
        *,
        config: PreferenceManagerProtocol | None = None,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._plugin_id = plugin_id
        self._has_saved_once = has_saved_once
        self._save = save
        # Reuses the plugin's own PluginConfig instance for this plugin_id
        # (see PluginConfig.__new__) rather than risking a second, divergent
        # in-memory copy of the same JSON file.
        self._config = config if config is not None else PluginConfig(plugin_id)

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)

    # ── Preference ──────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self._config.get(PREFERENCE_KEY, False))

    def set_enabled(self, value: bool) -> None:
        self._config.set(PREFERENCE_KEY, bool(value))
        self._config.save()

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        """Begin the reminder/autosave loop. Safe to call repeatedly."""
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def notify_saved(self) -> None:
        """Restart the countdown — call after any successful save, manual or automatic."""
        if self._timer.isActive():
            self._timer.start()

    # ── Tick handling ───────────────────────────────────────────────

    def _on_tick(self) -> None:
        if self.enabled and self._has_saved_once():
            try:
                self._save(self._on_autosave_result)
            except Exception:
                logger.exception("Autosave failed for plugin '%s'.", self._plugin_id)
                self._on_autosave_result(False)
            return

        icon, color = STYLE_WARNING
        show_toast(
            "It's been 15 minutes and your workspace hasn't been saved yet.",
            icon=icon,
            color=color,
        )

    def _on_autosave_result(self, success: bool) -> None:
        if success:
            self.notify_saved()
            icon, color = STYLE_INFO
            show_toast("Workspace autosaved.", icon=icon, color=color)
        else:
            icon, color = STYLE_WARNING
            show_toast("Autosave failed — your workspace still has unsaved changes.", icon=icon, color=color)
