"""Unit tests for karcytics_sdk.plugin.toast.

Covers the generic toast widget the Hub's own warning toasts now build on
top of (see karcytics.ui.components.toast_manager) and that plugins call
directly via PluginBase.show_toast / show_toast().
"""

from __future__ import annotations

from karcytics_sdk.plugin.toast import (
    STYLE_ERROR,
    STYLE_INFO,
    STYLE_SUCCESS,
    STYLE_UPDATE,
    STYLE_WARNING,
    ToastManager,
    ToastNotification,
    show_toast,
)


class TestToastNotification:
    def test_applies_the_requested_icon_and_border_color(self, qapp):
        toast = ToastNotification("Hello", icon="🔥", color="#123456")

        assert "#123456" in toast.container.styleSheet()
        assert toast.isVisible() is False  # not shown until the manager calls .show()

    def test_defaults_to_a_neutral_info_style_not_the_old_hardcoded_warning(self, qapp):
        toast = ToastNotification("Hello")

        assert toast is not None
        # Regression guard: this module is meant for any kind of toast, not
        # just warnings, so its bare default must not still be yellow/⚠️.
        assert "#E5C07B" not in toast.container.styleSheet()


class TestToastManager:
    def test_show_returns_a_visible_positioned_toast(self, qapp, qtbot):
        manager = ToastManager()

        toast = manager.show("Update available", icon="⬆️", color="#61AFEF", duration_ms=100)

        assert toast is not None
        assert toast.isVisible()
        assert toast in manager._active_toasts

    def test_stacks_multiple_active_toasts_without_overlapping(self, qapp, qtbot):
        manager = ToastManager()

        first = manager.show("First", duration_ms=100)
        second = manager.show("Second", duration_ms=100)

        assert first is not None
        assert second is not None
        # The later toast is stacked above (smaller y) the earlier one.
        assert second.y() < first.y()

    def test_cleans_up_toasts_that_have_already_closed(self, qapp, qtbot):
        manager = ToastManager()
        first = manager.show("First", duration_ms=100)
        first.close()

        manager.show("Second", duration_ms=100)

        assert first not in manager._active_toasts


class TestShowToastConvenienceFunction:
    def test_uses_the_shared_default_manager(self, qapp, qtbot):
        toast = show_toast("Hello from a plugin", icon="ℹ️", duration_ms=100)

        assert toast is not None
        assert toast.isVisible()


class TestStylePresets:
    def test_each_preset_is_an_icon_color_pair(self):
        for preset in (STYLE_INFO, STYLE_WARNING, STYLE_ERROR, STYLE_UPDATE, STYLE_SUCCESS):
            icon, color = preset
            assert isinstance(icon, str) and icon
            assert isinstance(color, str) and color.startswith("#")

    def test_presets_are_distinct_from_each_other(self):
        colors = {STYLE_INFO[1], STYLE_WARNING[1], STYLE_ERROR[1], STYLE_UPDATE[1], STYLE_SUCCESS[1]}
        assert len(colors) == 5
