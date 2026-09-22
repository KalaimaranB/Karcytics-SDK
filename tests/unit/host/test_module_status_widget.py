"""Unit tests for ModuleStatusWidget — the Hub-owned widget that occupies an
isolated module's slot in the workspace and mirrors what its daemon is
doing (Spawning / Running / Crashed / Closed), per the Interpreter Isolation
Plan's Phase 2 state machine. Integration-style: drives a real
PluginUIDaemon against a real ui_daemon_runtime.run() worker subprocess,
not a mock, so it also exercises the actual protocol end to end.
"""

import time
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from karcytics_sdk.host.core_services import CoreServicesServer
from karcytics_sdk.host.module_status_widget import ModuleStatusWidget
from karcytics_sdk.plugin.daemon import PluginUIDaemon


def _pump_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not condition():
        QApplication.processEvents()
        time.sleep(0.02)
    return condition()


@pytest.fixture(autouse=True)
def core_services():
    """Every worker `run()` spawns now blocks on confirming the Hub's real
    theme before building any UI (see `ui_daemon_runtime._confirm_hub_theme_or_exit`)
    — without a reachable `CoreServicesServer`, none of the daemons this file
    spawns would ever reach "ready".
    """
    server = CoreServicesServer()
    server.register("theme.get_current_colors", lambda _kwargs: {"BG_DARKEST": "#0a0a0a"})
    server.start()
    PluginUIDaemon.set_core_services(server.port, server.token)
    yield server
    PluginUIDaemon._core_services_port = None
    PluginUIDaemon._core_services_token = None
    server.stop()


@pytest.fixture
def fast_worker_script(tmp_path):
    script_path = tmp_path / "fast_worker.py"
    code = """
import sys
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow
from karcytics_sdk.plugin.ui_daemon_runtime import run

def build_panel():
    return QLabel("fast")

def _simulate_native_close(kwargs):
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow):
            w.close()
    return {"status": "ok"}

if __name__ == "__main__":
    run(build_panel, extra_handlers={"simulate_native_close": _simulate_native_close})
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


@pytest.fixture
def slow_worker_script(tmp_path):
    """Delays its ready event by sleeping in the panel factory — gives a
    test a real window to call cancel() during Spawning before ready lands.
    """
    script_path = tmp_path / "slow_worker.py"
    code = """
import time
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.ui_daemon_runtime import run

def build_panel():
    time.sleep(2.0)
    return QLabel("slow")

if __name__ == "__main__":
    run(build_panel)
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def _make_daemon(plugin_id, script_path):
    return PluginUIDaemon.get_instance(plugin_id, daemon_script_path=script_path)


@pytest.fixture
def make_widget():
    """Tracks every `ModuleStatusWidget` a test creates and `shutdown()`s
    each one afterward.

    Without this, a test that forgets (or, as in a couple of cases here,
    used to) call `PluginUIDaemon.stop_instance()`/`widget.shutdown()`
    itself leaks that widget's `_SerialWorker` background thread forever —
    it has no lifetime tied to the widget or the test, only to the process.
    Across a full test-suite run those accumulate until the interpreter
    aborts (`Fatal Python error: Aborted`) trying to create yet another
    thread. `shutdown()` is idempotent (both it and `PluginUIDaemon.stop_instance()`
    tolerate an already-terminated process), so calling it here on top of
    whatever cleanup a test already does itself is always safe.
    """
    widgets = []

    def _make(daemon, module_name="Demo Module"):
        widget = ModuleStatusWidget(daemon, module_name=module_name)
        widgets.append(widget)
        return widget

    yield _make

    for widget in widgets:
        widget.shutdown()


def test_widget_starts_in_spawning_state(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_spawning", fast_worker_script)
    widget = make_widget(daemon)

    assert widget.state == ModuleStatusWidget.STATE_SPAWNING

    _pump_until(lambda: widget.state != ModuleStatusWidget.STATE_SPAWNING)
    PluginUIDaemon.stop_instance("status_widget_spawning")


def test_widget_transitions_to_running_on_ready(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_running", fast_worker_script)
    widget = make_widget(daemon)

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    PluginUIDaemon.stop_instance("status_widget_running")


def test_widget_bring_to_front_calls_focus(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_focus", fast_worker_script)
    widget = make_widget(daemon)
    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    with patch.object(daemon, "call", wraps=daemon.call) as spy:
        widget.bring_to_front()
        _pump_until(lambda: spy.called)

    assert spy.call_args[0][0] == "focus"

    PluginUIDaemon.stop_instance("status_widget_focus")


def test_widget_transitions_to_crashed_on_unexpected_exit(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_crash", fast_worker_script)
    widget = make_widget(daemon)
    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    daemon._proc.kill()  # simulate the worker dying on its own, not via our shutdown

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_CRASHED)
    assert widget.error_message

    PluginUIDaemon.stop_instance("status_widget_crash")


def test_widget_transitions_to_closed_on_native_window_close(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_native_close", fast_worker_script)
    widget = make_widget(daemon)
    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    daemon.call("simulate_native_close", {})

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_CLOSED)

    PluginUIDaemon.stop_instance("status_widget_native_close")


def test_widget_cancel_during_spawn_terminates_process_and_sets_closed(qapp, slow_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_cancel", slow_worker_script)
    widget = make_widget(daemon)
    assert widget.state == ModuleStatusWidget.STATE_SPAWNING

    widget.cancel()

    assert widget.state == ModuleStatusWidget.STATE_CLOSED
    assert _pump_until(lambda: daemon._proc is None or daemon._proc.poll() is not None)

    # The slow worker's delayed ready, if it ever arrives after this, must
    # not resurrect a cancelled attempt back into Running.
    time.sleep(2.2)
    QApplication.processEvents()
    assert widget.state == ModuleStatusWidget.STATE_CLOSED

    PluginUIDaemon.stop_instance("status_widget_cancel")


def test_widget_reopen_after_cancel_reaches_running(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    """The generation guard must invalidate a superseded start attempt
    without blocking a *later*, successful one from landing normally.
    """
    daemon = _make_daemon("status_widget_reopen", fast_worker_script)
    widget = make_widget(daemon)
    widget.cancel()
    assert widget.state == ModuleStatusWidget.STATE_CLOSED

    widget.start()

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    PluginUIDaemon.stop_instance("status_widget_reopen")


def test_shutdown_terminates_the_daemon_synchronously(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    """Unlike cancel(), shutdown() must not need a pumped event loop to take
    effect — it's called from WorkspaceWindow.closeEvent() as the whole app
    is exiting, where nothing is left to pump _SerialWorker's queued call.
    """
    daemon = _make_daemon("status_widget_shutdown", fast_worker_script)
    widget = make_widget(daemon)
    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    widget.shutdown()

    assert daemon._proc is None or daemon._proc.poll() is not None


def test_push_theme_forwards_colors_once_running(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    """Regression test: push_theme() must go through daemon.call(), not
    daemon.send_event() — the worker's RequestDispatcher only understands
    {"method": ..., "kwargs": ...} request frames (see
    ui_daemon_runtime.py's theme_changed handler), so a send_event() frame
    is silently dropped on arrival and never reaches it. Spying on call()
    here (rather than send_event()) is what catches that class of bug,
    since it's wired to the same real subprocess the worker actually reads
    frames from.
    """
    daemon = _make_daemon("status_widget_theme", fast_worker_script)
    widget = make_widget(daemon)
    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)

    with patch.object(daemon, "call", wraps=daemon.call) as spy:
        widget.push_theme({"ACCENT_PRIMARY": "#2f81f7"})
        _pump_until(lambda: spy.called)

    assert spy.call_args[0] == ("theme_changed", {"colors": {"ACCENT_PRIMARY": "#2f81f7"}})

    PluginUIDaemon.stop_instance("status_widget_theme")


def test_push_theme_is_a_noop_before_running(qapp, slow_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_theme_early", slow_worker_script)
    widget = make_widget(daemon)
    assert widget.state == ModuleStatusWidget.STATE_SPAWNING

    with patch.object(daemon, "call") as spy:
        widget.push_theme({"ACCENT_PRIMARY": "#2f81f7"})
        QApplication.processEvents()

    spy.assert_not_called()

    widget.cancel()
    PluginUIDaemon.stop_instance("status_widget_theme_early")


# -- Ready Gate protocol ---------------------------------------------------
#
# karcytics.ui.windows.workspace.plugin_loader.PluginLoaderManager already has
# a generic async-init protocol for panels it can't construct synchronously:
# if a panel has `panel_ready`/`begin_async_init`, it stays behind the
# GalacticLoader overlay until `panel_ready` (and `data_ready`, if present)
# fire, instead of an immediate crossfade. ModuleStatusWidget speaking this
# same contract is what lets the Hub reuse that existing choreography for
# isolated modules verbatim, rather than forking a parallel isolated-only
# code path in plugin_loader.py.


def test_panel_ready_fires_for_a_listener_connected_after_construction(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    """plugin_loader.py always connects panel_ready *after* the panel is
    already fully constructed (construct panel, then wire signals) — so
    panel_ready must not be emitted synchronously inside __init__, or every
    real caller's connection would be made too late to ever see it.
    """
    daemon = _make_daemon("status_widget_panel_ready_late_connect", fast_worker_script)
    widget = make_widget(daemon)

    received = []
    widget.panel_ready.connect(lambda: received.append(True))

    assert _pump_until(lambda: received)

    PluginUIDaemon.stop_instance("status_widget_panel_ready_late_connect")


def test_begin_async_init_is_a_safe_noop(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    """The real daemon spawn already began in __init__ (this widget has no
    "not yet started" state to enter) — begin_async_init() only needs to
    exist and not raise or double-spawn anything, satisfying the interface
    plugin_loader.py checks for via hasattr().
    """
    daemon = _make_daemon("status_widget_begin_async_init", fast_worker_script)
    widget = make_widget(daemon)

    widget.begin_async_init()  # must not raise

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)
    PluginUIDaemon.stop_instance("status_widget_begin_async_init")


def test_data_ready_fires_once_on_first_running_transition(qapp, fast_worker_script, make_widget):  # noqa: ARG001
    daemon = _make_daemon("status_widget_data_ready_running", fast_worker_script)
    widget = make_widget(daemon)

    received = []
    widget.data_ready.connect(lambda: received.append(True))

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_RUNNING)
    assert _pump_until(lambda: received)
    assert received == [True]

    PluginUIDaemon.stop_instance("status_widget_data_ready_running")


def test_data_ready_fires_on_spawn_failure_so_loader_does_not_hang(qapp, tmp_path, make_widget):  # noqa: ARG001
    """If the daemon never starts, plugin_loader.py's ready-gate must not
    wait forever behind the loading overlay — data_ready has to fire on
    Crashed too, not only on a successful Running transition.
    """
    broken_script = tmp_path / "broken_worker.py"
    broken_script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    daemon = _make_daemon("status_widget_data_ready_crash", broken_script)
    widget = make_widget(daemon)

    received = []
    widget.data_ready.connect(lambda: received.append(True))

    assert _pump_until(lambda: widget.state == ModuleStatusWidget.STATE_CRASHED, timeout=15.0)
    assert _pump_until(lambda: received)
    assert received == [True]

    PluginUIDaemon.stop_instance("status_widget_data_ready_crash")
