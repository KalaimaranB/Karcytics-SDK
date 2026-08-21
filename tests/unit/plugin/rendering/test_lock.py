"""Unit tests for karcytics_sdk.plugin.rendering.lock."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from karcytics_sdk.plugin.rendering.lock import MPL_RASTER_LOCK, RasterLock


class TestRasterLockBasics:
    def test_acquire_release_roundtrip(self):
        lock = RasterLock("test")
        assert lock.acquire() is True
        lock.release()

    def test_context_manager_releases_on_exit(self):
        lock = RasterLock("test")
        with lock:
            pass

        # RLock is reentrant for the SAME thread, so the only way to prove
        # __exit__ actually released it is to check from a different thread.
        results = []

        def _try_from_other_thread():
            acquired = lock.acquire(blocking=False)
            results.append(acquired)
            if acquired:
                lock.release()

        t = threading.Thread(target=_try_from_other_thread)
        t.start()
        t.join(timeout=2)

        assert results == [True]

    def test_is_reentrant_on_the_same_thread(self):
        """A canvas's paintEvent() calling draw() internally on the same thread must not deadlock."""
        lock = RasterLock("test")
        with lock:
            # Re-entering on the same thread must succeed immediately, not block.
            acquired = lock.acquire(blocking=False)
            assert acquired is True
            lock.release()

    def test_a_different_thread_cannot_acquire_while_held(self):
        lock = RasterLock("test")
        lock.acquire()
        try:
            results = []

            def _try_from_other_thread():
                results.append(lock.acquire(blocking=False))

            t = threading.Thread(target=_try_from_other_thread)
            t.start()
            t.join(timeout=2)

            assert results == [False]
        finally:
            lock.release()


class TestTryRun:
    def test_runs_action_immediately_when_lock_is_free(self):
        lock = RasterLock("test")
        calls = []

        lock.try_run(lambda: calls.append("action"), lambda: calls.append("retry"))

        assert calls == ["action"]

    def test_releases_the_lock_after_a_successful_run(self):
        lock = RasterLock("test")
        lock.try_run(lambda: None, lambda: None)

        assert lock.acquire(blocking=False) is True
        lock.release()

    def test_schedules_retry_instead_of_blocking_when_lock_is_busy(self, qtbot):
        # RLock is reentrant for the SAME thread, so the lock must be held by
        # a DIFFERENT thread to exercise the "busy" path at all.
        lock = RasterLock("test")
        holder_acquired = threading.Event()
        holder_release = threading.Event()

        def _hold():
            lock.acquire()
            holder_acquired.set()
            holder_release.wait(timeout=2)
            lock.release()

        holder = threading.Thread(target=_hold)
        holder.start()
        assert holder_acquired.wait(timeout=2)

        try:
            calls = []
            lock.try_run(lambda: calls.append("action"), lambda: calls.append("retry"), retry_ms=10)

            # try_run must return immediately (non-blocking) rather than
            # waiting for the lock — the whole point of this method existing
            # instead of a plain `with lock:` is to never block the Qt main
            # thread inside a paintEvent()/draw() override.
            assert calls == []

            qtbot.wait(50)
            assert calls == ["retry"]
        finally:
            holder_release.set()
            holder.join(timeout=2)

    def test_exceptions_from_action_are_caught_and_logged_not_raised(self, caplog):
        lock = RasterLock("test")

        def _boom():
            raise ValueError("boom")

        lock.try_run(_boom, lambda: None)  # must not raise

        assert lock.acquire(blocking=False) is True  # lock was still released via `finally`
        lock.release()

    def test_a_busy_lock_still_releases_from_the_thread_that_held_it(self):
        """Sanity check that try_run's non-blocking probe never disturbs the real holder's lock state."""
        lock = RasterLock("test")
        lock.acquire()
        lock.try_run(lambda: None, lambda: None)  # should defer via retry, not touch our hold
        lock.release()
        assert lock.acquire(blocking=False) is True
        lock.release()


class TestTryRunCrashReporting:
    def test_no_crash_reporter_by_default_still_just_logs(self, caplog):
        lock = RasterLock("test")

        def _boom():
            raise ValueError("boom")

        lock.try_run(_boom, lambda: None)  # must not raise, no crash_reporter given

    def test_reports_to_crash_reporter_when_action_raises(self):
        lock = RasterLock("test")
        reporter = MagicMock()
        boom = ValueError("boom")

        def _boom():
            raise boom

        lock.try_run(_boom, lambda: None, crash_reporter=reporter, plugin_id="flow_cytometry")

        reporter.report_error.assert_called_once_with(
            "Raster action failed (lock=test)",
            exception=boom,
            plugin_id="flow_cytometry",
            fatal=False,
        )

    def test_does_not_call_crash_reporter_when_action_succeeds(self):
        lock = RasterLock("test")
        reporter = MagicMock()

        lock.try_run(lambda: None, lambda: None, crash_reporter=reporter, plugin_id="flow_cytometry")

        reporter.report_error.assert_not_called()


class TestModuleLevelSingleton:
    def test_mpl_raster_lock_is_a_raster_lock(self):
        assert isinstance(MPL_RASTER_LOCK, RasterLock)

    def test_mpl_raster_lock_is_a_shared_singleton_across_imports(self):
        from karcytics_sdk.plugin.rendering.lock import MPL_RASTER_LOCK as again

        assert MPL_RASTER_LOCK is again

    def test_mpl_raster_lock_is_named(self):
        assert MPL_RASTER_LOCK.name == "matplotlib-agg"


def test_try_run_retry_eventually_succeeds_once_the_holder_releases(qtbot):
    """End-to-end: a background thread holds the lock briefly; try_run's
    QTimer retry must pick it up once the holder releases, without the
    caller ever blocking to wait for it.
    """
    lock = RasterLock("test")
    holder_acquired = threading.Event()

    def _hold_then_release():
        lock.acquire()
        holder_acquired.set()
        time.sleep(0.05)
        lock.release()

    threading.Thread(target=_hold_then_release).start()
    assert holder_acquired.wait(timeout=2)

    calls = []

    def _attempt():
        lock.try_run(lambda: calls.append("action"), _attempt, retry_ms=10)

    _attempt()
    assert calls == []

    qtbot.waitUntil(lambda: calls == ["action"], timeout=1000)
