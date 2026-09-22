"""Unit tests for karcytics_sdk.plugin.autosave.WorkflowAutosaveController.

Covers the shared 15-minute workflow-autosave/reminder loop every plugin
gets via `PluginBase.setup_workflow_autosave()` — no plugin implements this
timer/preference/toast logic itself.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from karcytics_sdk.plugin.autosave import PREFERENCE_KEY, WorkflowAutosaveController
from karcytics_sdk.plugin.io import PluginConfig


@pytest.fixture
def config(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield PluginConfig("autosave_test_plugin")


def _controller(config, has_saved_once=lambda: True, save=None, interval_ms=None):
    if save is None:

        def save(on_done):
            on_done(True)

    kwargs = {"config": config}
    if interval_ms is not None:
        kwargs["interval_ms"] = interval_ms

    return WorkflowAutosaveController("autosave_test_plugin", has_saved_once, save, **kwargs)


class TestPreference:
    def test_defaults_to_disabled(self, config, qapp):  # noqa: ARG001
        controller = _controller(config)
        assert controller.enabled is False

    def test_set_enabled_persists_through_the_config(self, config, qapp):  # noqa: ARG001
        controller = _controller(config)
        controller.set_enabled(True)

        assert controller.enabled is True
        assert config.get(PREFERENCE_KEY) is True

    def test_shares_state_with_another_controller_over_the_same_config(self, config, qapp):  # noqa: ARG001
        first = _controller(config)
        second = _controller(config)

        first.set_enabled(True)

        assert second.enabled is True


class TestTick:
    def test_enabled_and_saved_once_triggers_save_and_info_toast(self, config, qapp):  # noqa: ARG001
        controller = _controller(config, has_saved_once=lambda: True)
        controller.set_enabled(True)

        with patch("karcytics_sdk.plugin.autosave.show_toast") as mock_toast:
            controller._on_tick()

        mock_toast.assert_called_once()
        _, kwargs = mock_toast.call_args
        assert kwargs["icon"] == "ℹ️"

    def test_disabled_shows_warning_toast_and_does_not_save(self, config, qapp):  # noqa: ARG001
        saved = []

        def save(on_done):
            saved.append(True)
            on_done(True)

        controller = _controller(config, has_saved_once=lambda: True, save=save)
        controller.set_enabled(False)

        with patch("karcytics_sdk.plugin.autosave.show_toast") as mock_toast:
            controller._on_tick()

        assert saved == []
        mock_toast.assert_called_once()
        _, kwargs = mock_toast.call_args
        assert kwargs["icon"] == "⚠️"
        assert "hasn't been saved" in mock_toast.call_args.args[0]

    def test_enabled_but_never_saved_shows_warning_toast(self, config, qapp):  # noqa: ARG001
        controller = _controller(config, has_saved_once=lambda: False)
        controller.set_enabled(True)

        with patch("karcytics_sdk.plugin.autosave.show_toast") as mock_toast:
            controller._on_tick()

        _, kwargs = mock_toast.call_args
        assert kwargs["icon"] == "⚠️"

    def test_save_failure_shows_warning_toast_not_info(self, config, qapp):  # noqa: ARG001
        def save(on_done):
            on_done(False)

        controller = _controller(config, has_saved_once=lambda: True, save=save)
        controller.set_enabled(True)

        with patch("karcytics_sdk.plugin.autosave.show_toast") as mock_toast:
            controller._on_tick()

        _, kwargs = mock_toast.call_args
        assert kwargs["icon"] == "⚠️"

    def test_save_raising_is_treated_as_a_failed_autosave(self, config, qapp):  # noqa: ARG001
        def save(on_done):
            raise RuntimeError("boom")

        controller = _controller(config, has_saved_once=lambda: True, save=save)
        controller.set_enabled(True)

        with patch("karcytics_sdk.plugin.autosave.show_toast") as mock_toast:
            controller._on_tick()  # must not raise

        _, kwargs = mock_toast.call_args
        assert kwargs["icon"] == "⚠️"


class TestLifecycle:
    def test_start_and_stop_toggle_the_timer(self, config, qapp):  # noqa: ARG001
        controller = _controller(config)
        assert controller._timer.isActive() is False

        controller.start()
        assert controller._timer.isActive() is True

        controller.stop()
        assert controller._timer.isActive() is False

    def test_start_is_idempotent(self, config, qapp):  # noqa: ARG001
        controller = _controller(config)
        controller.start()
        controller.start()

        assert controller._timer.isActive() is True

    def test_notify_saved_restarts_an_active_timer(self, config, qapp):  # noqa: ARG001
        controller = _controller(config, interval_ms=100)
        controller.start()

        controller.notify_saved()

        assert controller._timer.isActive() is True
        assert controller._timer.remainingTime() > 0

    def test_notify_saved_is_a_noop_when_not_running(self, config, qapp):  # noqa: ARG001
        controller = _controller(config)
        controller.notify_saved()  # must not raise or start the timer

        assert controller._timer.isActive() is False
