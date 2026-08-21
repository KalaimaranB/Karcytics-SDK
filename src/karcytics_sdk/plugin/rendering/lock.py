"""Shared, process-wide lock for non-thread-safe rasterization backends.

Promotes a pattern that previously lived as a private ``_mpl_lock.py`` inside
the flow-cytometry plugin into a reusable SDK primitive: matplotlib's Agg
backend shares C-level state and is not thread-safe, so every canvas paint
and every background render task touching the same backend must serialize
through the exact same lock instance, not merely "a lock of the same type".

Only the final rasterize step needs this lock. Data preparation (gating,
transforms, density/histogram computation — see ``pipeline.py``) is pure
numpy/pandas work and stays fully parallel; holding this lock around that
work too turns it into an unnecessary bottleneck rather than a narrow safety
rail around the one C extension that actually needs it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer

if TYPE_CHECKING:
    from karcytics_sdk.interfaces.i_crash_reporter import ICrashReporter

logger = logging.getLogger(__name__)


class RasterLock:
    """Reentrant, named lock guarding one rasterization backend's shared state.

    Reentrant (wraps ``threading.RLock``) because a Qt canvas's own
    ``paintEvent()`` can internally re-invoke ``draw()`` on the same thread
    that already holds the lock (matplotlib's Qt backend does this via
    ``_draw_idle()``). A plain ``Lock`` would treat that inner acquire as
    contended and defer it via retry, which silently drops the real paint.
    """

    def __init__(self, name: str = "default") -> None:
        self.name = name
        self._lock = threading.RLock()

    def acquire(self, blocking: bool = True) -> bool:
        """Acquire the lock, blocking by default."""
        return self._lock.acquire(blocking)

    def release(self) -> None:
        """Release the lock. Must be called by whichever acquire() succeeded."""
        self._lock.release()

    def __enter__(self) -> RasterLock:
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._lock.release()

    def try_run(
        self,
        action: Callable[[], None],
        on_busy_retry: Callable[[], None],
        retry_ms: int = 50,
        crash_reporter: ICrashReporter | None = None,
        plugin_id: str | None = None,
    ) -> None:
        """Runs `action` under a non-blocking acquire of this lock.

        For use on the Qt main thread (e.g. inside `paintEvent()`/`draw()`
        overrides), where blocking to wait for a background render task to
        release the lock would freeze the UI. If the lock is currently held
        elsewhere, `on_busy_retry` is scheduled via `QTimer.singleShot`
        instead of blocking, and `action` does not run on this call — the
        retry callback is expected to call back into `try_run` (directly or
        indirectly) itself. Exceptions raised by `action` are caught and
        logged rather than propagated, since this is meant to run inside Qt
        event handlers where an uncaught exception would otherwise abort
        event delivery.

        `crash_reporter` is optional and defaults to `None` (log-only,
        today's behavior) — pass it explicitly when the caller knows it's
        running isolated and has no other route back to the Hub's
        diagnostics engine (see `ICrashReporter`'s docstring).
        """
        if not self._lock.acquire(blocking=False):
            QTimer.singleShot(retry_ms, on_busy_retry)
            return
        try:
            action()
        except Exception as exc:
            logger.exception("Exception during locked raster action (lock=%s)", self.name)
            if crash_reporter is not None:
                crash_reporter.report_error(
                    f"Raster action failed (lock={self.name})",
                    exception=exc,
                    plugin_id=plugin_id,
                    fatal=False,
                )
        finally:
            self._lock.release()


MPL_RASTER_LOCK = RasterLock(name="matplotlib-agg")
"""Process-wide lock for the matplotlib Agg backend.

A plain importable singleton — not a `PluginContext` capability — matching
the SDK's existing convention for other process-wide services
(`runtime_services.task_scheduler`, `runtime_services.event_bus`). There is
no meaningful per-plugin access-control question for a lock guarding a
shared C extension's memory safety, so gating it behind a manifest
`requires` declaration would add ceremony without a real capability-scoping
benefit.

Scoped to a single OS process's Agg state: an isolated plugin
(`PluginManifest.process_model == "isolated"`) runs in its own interpreter
and therefore already gets its own independent `MPL_RASTER_LOCK` instance —
no cross-process coordination is needed here. FCS/file-I/O daemon
subprocesses (e.g. `PluginDaemon`) do no matplotlib rasterization, so they
never contend for this lock at all.
"""
