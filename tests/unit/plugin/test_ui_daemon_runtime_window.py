"""Unit tests for ClosableMainWindow — the window base ui_daemon_runtime.run()
hosts a plugin's panel in. Its whole job is to notice a *native* close (the
user clicking the OS window's close button) and fire a callback before the
process would otherwise just exit silently — see the Interpreter Isolation
Plan's status-widget "Closed" state, which depends on the Hub actually
hearing about this.
"""

from karcytics_sdk.plugin.ui_daemon_runtime import ClosableMainWindow


def test_native_close_invokes_callback(qapp):  # noqa: ARG001
    calls = []
    window = ClosableMainWindow(on_close=lambda: calls.append(True))

    window.close()

    assert calls == [True]


def test_close_callback_fires_exactly_once(qapp):  # noqa: ARG001
    calls = []
    window = ClosableMainWindow(on_close=lambda: calls.append(True))

    window.close()
    window.close()

    assert len(calls) == 1


def test_programmatic_close_via_hub_request_skips_callback(qapp):  # noqa: ARG001
    """When the Hub itself asked the window to close (an `exit`/
    `close_requested` request, not the user clicking the native close
    button), the worker is already telling the Hub directly via that
    request's response — firing window_closed as well would be a redundant,
    possibly out-of-order second notification for the same event.
    """
    calls = []
    window = ClosableMainWindow(on_close=lambda: calls.append(True))

    window.close_without_notifying_hub()

    assert calls == []


class _Guard:
    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.asked = 0

    def confirm_close(self) -> bool:
        self.asked += 1
        return self.allow


def test_panel_can_veto_a_user_close(qapp):  # noqa: ARG001
    """Unsaved changes + Cancel: the window stays open and the Hub isn't told."""
    calls = []
    window = ClosableMainWindow(on_close=lambda: calls.append(True))
    window._close_guard = _Guard(allow=False)
    window.show()

    assert window.close() is False
    assert window.isVisible()
    assert calls == []

    window._close_guard.allow = True
    assert window.close() is True
    assert calls == [True]


def test_hub_requested_close_is_never_vetoed(qapp):  # noqa: ARG001
    guard = _Guard(allow=False)
    window = ClosableMainWindow(on_close=lambda: None)
    window._close_guard = guard
    window.show()

    assert window.close_without_notifying_hub() is True
    assert guard.asked == 0


def test_panel_without_confirm_close_never_blocks(qapp):  # noqa: ARG001
    window = ClosableMainWindow(on_close=lambda: None)
    window._close_guard = object()
    window.show()
    assert window.close() is True
