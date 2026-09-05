"""End-to-end test of ui_daemon_runtime.run(), driven from the Hub side via
PluginUIDaemon exactly as a real plugin's ui_daemon.py would be — proves the
extracted runtime actually satisfies the PluginUIDaemon protocol, not just
its own unit tests in isolation.
"""

import time

import pytest

from karcytics_sdk.host.core_services import CoreServicesServer
from karcytics_sdk.plugin.daemon import PluginUIDaemon

FAKE_HUB_COLORS = {"BG_DARKEST": "#0a0a0a", "ACCENT_PRIMARY": "#2f81f7"}


@pytest.fixture(autouse=True)
def core_services():
    """Every worker `run()` spawns now blocks on confirming the Hub's real
    theme before building any UI (see `ui_daemon_runtime._confirm_hub_theme_or_exit`)
    — without a reachable `CoreServicesServer` answering `theme.get_current_colors`,
    no worker in this file would ever reach "ready" at all. Autouse because
    that gate applies to every test here except the one deliberately testing
    its failure path.

    `PluginUIDaemon.set_core_services` writes process-wide ClassVars, so they
    must be cleared afterward rather than left to leak into other test
    modules' daemons.
    """
    server = CoreServicesServer()
    server.register("theme.get_current_colors", lambda _kwargs: dict(FAKE_HUB_COLORS))
    server.start()
    PluginUIDaemon.set_core_services(server.port, server.token)
    yield server
    PluginUIDaemon._core_services_port = None
    PluginUIDaemon._core_services_token = None
    server.stop()


@pytest.fixture
def minimal_worker_script(tmp_path):
    """A plugin's entire ui_daemon.py, reduced to what the runtime needs:
    a zero-arg panel factory. No plugin-specific shimming — that's exactly
    the point of the extraction.
    """
    script_path = tmp_path / "minimal_ui_daemon.py"
    code = """
import sys
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow
from karcytics_sdk.plugin.ui_daemon_runtime import run

def build_panel():
    return QLabel("hello from an isolated module")

def _simulate_native_close(kwargs):
    # Stands in for the user clicking the OS window's own close button —
    # calls the real .close() (not close_without_notifying_hub()), so this
    # exercises the same closeEvent path a real native close would.
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow):
            w.close()
    return {"status": "ok"}

if __name__ == "__main__":
    run(
        build_panel,
        window_title="Minimal Test Module",
        extra_handlers={"simulate_native_close": _simulate_native_close},
    )
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


@pytest.fixture
def theme_aware_worker_script(tmp_path):
    """A panel with `_apply_theme_styles()`, reporting back the color it
    actually sees via `theme_fallback.Colors` each time it's called — proves
    a `theme_changed` request's `colors` payload really reaches the panel,
    not just that the request gets a `{"status": "ok"}` response.
    """
    script_path = tmp_path / "theme_worker.py"
    code = """
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.theme_fallback import Colors
from karcytics_sdk.plugin.ui_daemon_runtime import run, send_event

class Panel(QLabel):
    def _apply_theme_styles(self):
        send_event("theme_applied", {"accent": Colors.ACCENT_PRIMARY})

def build_panel():
    return Panel("theme aware")

if __name__ == "__main__":
    run(build_panel, window_title="Theme Test Module")
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


@pytest.fixture
def theme_gate_worker_script(tmp_path):
    """A panel that reports the color it sees from `theme_fallback.Colors`
    at construction time — *before* `run()` ever calls `send_event("ready")`
    — proving the Hub's real color already landed before any UI was built,
    not applied later via a separate `theme_changed` event.
    """
    script_path = tmp_path / "theme_gate_worker.py"
    code = """
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.theme_fallback import Colors
from karcytics_sdk.plugin.ui_daemon_runtime import run, send_event

class Panel(QLabel):
    def __init__(self):
        super().__init__("theme gate")
        send_event("startup_accent", {"accent": Colors.ACCENT_PRIMARY})

def build_panel():
    return Panel()

if __name__ == "__main__":
    run(build_panel, window_title="Theme Gate Test Module")
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


@pytest.fixture
def slow_phase_2_worker_script(tmp_path):
    """A panel whose begin_async_init() blocks for longer than any
    reasonable Ready Gate timeout — regression coverage for the bug where
    calling it before send_event("ready") let a slow Phase 2 import (in
    practice: matplotlib/umap/sklearn) delay ready long enough for the
    Hub's real 45s Ready Gate timer to fire before the window ever appeared
    ready.
    """
    script_path = tmp_path / "slow_phase_2_worker.py"
    code = """
import time
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.ui_daemon_runtime import run, send_event

class Panel(QLabel):
    def begin_async_init(self):
        time.sleep(2.0)
        send_event("phase_2_done", {})

def build_panel():
    return Panel("slow phase 2")

if __name__ == "__main__":
    run(build_panel, window_title="Slow Phase 2 Test Module")
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


@pytest.fixture
def project_aware_worker_script(tmp_path):
    """A panel with an extra `report_project_manager` handler that reads
    `self.window().project_manager` back out — same access pattern
    flow_cytometry's own `WorkspaceIOHandler._get_project_manager()` uses —
    to prove `run()` actually populates that attribute from the Hub's
    `project.get_info` reply, not just that `RemoteProjectManager` works in
    isolation.
    """
    script_path = tmp_path / "project_worker.py"
    code = """
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow
from karcytics_sdk.plugin.ui_daemon_runtime import run

def build_panel():
    return QLabel("project aware")

def _report_project_manager(kwargs):
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow):
            pm = getattr(w, "project_manager", None)
            if pm is None:
                return {"has_pm": False}
            return {
                "has_pm": True,
                "project_name": pm.project_name,
                "project_dir": str(pm.project_dir),
                "assets_dir": str(pm.assets_dir),
            }
    return {"has_pm": False}

if __name__ == "__main__":
    run(
        build_panel,
        window_title="Project Aware Test Module",
        extra_handlers={"report_project_manager": _report_project_manager},
    )
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_project_manager_available_when_hub_has_a_project(core_services, project_aware_worker_script, tmp_path):
    project_dir = tmp_path / "proj"
    core_services.register(
        "project.get_info",
        lambda _kwargs: {
            "project_dir": str(project_dir),
            "assets_dir": str(project_dir / "assets"),
            "project_name": "My Project",
        },
    )

    plugin_id = "test_runtime_project_manager_present"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=project_aware_worker_script)

    result = daemon.call("report_project_manager", {})

    assert result == {
        "has_pm": True,
        "project_name": "My Project",
        "project_dir": str(project_dir),
        "assets_dir": str(project_dir / "assets"),
    }

    PluginUIDaemon.stop_instance(plugin_id)


def test_project_manager_is_none_when_hub_reports_no_active_project(core_services, project_aware_worker_script):
    core_services.register("project.get_info", lambda _kwargs: None)

    plugin_id = "test_runtime_project_manager_none"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=project_aware_worker_script)

    result = daemon.call("report_project_manager", {})

    assert result == {"has_pm": False}

    PluginUIDaemon.stop_instance(plugin_id)


def test_project_manager_fetch_failure_degrades_to_none_without_crashing(core_services, project_aware_worker_script):
    """The `core_services` fixture never registers `project.get_info`, so
    this exercises the same path an older Hub (or one still starting up)
    would hit — the worker must still reach "ready" and simply report no
    project, not crash the way a missing theme handler is allowed to.
    """
    plugin_id = "test_runtime_project_manager_unreachable"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=project_aware_worker_script)

    result = daemon.call("report_project_manager", {})

    assert result == {"has_pm": False}

    PluginUIDaemon.stop_instance(plugin_id)


@pytest.fixture
def menu_aware_worker_script(tmp_path):
    """A panel with its own state and a plugin-supplied `configure_menus`
    that wires a custom top-level menu straight to a real panel method —
    proves `run()` calls it with the actual panel (not a stub), after the
    panel exists, the way a real plugin needing different menus than
    another plugin would use it.
    """
    script_path = tmp_path / "menu_worker.py"
    code = """
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow
from karcytics_sdk.plugin.ui_daemon_runtime import run

class Panel(QLabel):
    def __init__(self):
        super().__init__("menu aware")
        self.bump_count = 0

    def bump(self):
        self.bump_count += 1

def build_panel():
    return Panel()

def configure_menus(window, panel):
    analysis_menu = window.menuBar().addMenu("&Analysis")
    action = QAction("Do Thing", window)
    action.triggered.connect(panel.bump)
    analysis_menu.addAction(action)

def _trigger_analysis_action(kwargs):
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow):
            for menu_action in w.menuBar().actions():
                if menu_action.text() == "&Analysis":
                    for item in menu_action.menu().actions():
                        if item.text() == "Do Thing":
                            item.trigger()
                            return {"bump_count": w.centralWidget().bump_count}
    return {"bump_count": None}

if __name__ == "__main__":
    run(
        build_panel,
        window_title="Menu Aware Test Module",
        configure_menus=configure_menus,
        extra_handlers={"trigger_analysis_action": _trigger_analysis_action},
    )
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_configure_menus_receives_the_real_panel(core_services, menu_aware_worker_script):  # noqa: ARG001
    plugin_id = "test_runtime_configure_menus"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=menu_aware_worker_script)

    result = daemon.call("trigger_analysis_action", {})

    assert result == {"bump_count": 1}

    PluginUIDaemon.stop_instance(plugin_id)


@pytest.fixture
def broken_configure_menus_worker_script(tmp_path):
    """A `configure_menus` that raises — the window must still come up and
    reach "ready" rather than taking the whole worker down with it.
    """
    script_path = tmp_path / "broken_menu_worker.py"
    code = """
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.ui_daemon_runtime import run

def build_panel():
    return QLabel("still here")

def configure_menus(window, panel):
    raise RuntimeError("plugin menu code is broken")

if __name__ == "__main__":
    run(build_panel, window_title="Broken Menu Test Module", configure_menus=configure_menus)
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_configure_menus_raising_does_not_crash_the_worker(
    core_services,
    broken_configure_menus_worker_script,  # noqa: ARG001
):
    plugin_id = "test_runtime_configure_menus_raises"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=broken_configure_menus_worker_script)

    daemon.ensure_started(timeout=10.0)
    result = daemon.call("focus", {})
    assert result.get("status") == "ok"

    PluginUIDaemon.stop_instance(plugin_id)


def test_runtime_sends_ready_with_geometry(minimal_worker_script):
    """The ready event must be connected *before* ensure_started() runs —
    it fires once, during startup, off the reader thread; connecting
    afterward (as start_instance()'s synchronous return would allow) races
    the one-shot emission and can miss it entirely.
    """
    plugin_id = "test_runtime_ready"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=minimal_worker_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))

    daemon.ensure_started()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if any(t == "ready" for t, p in received):
            break
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)

    assert any(t == "ready" for t, p in received), "ready event not found"
    ready_payload = next(p for t, p in received if t == "ready")
    assert "geometry" in ready_payload

    PluginUIDaemon.stop_instance(plugin_id)


def test_runtime_focus_request_succeeds(minimal_worker_script):
    plugin_id = "test_runtime_focus"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=minimal_worker_script)

    result = daemon.call("focus", {})

    assert result.get("status") == "ok"

    PluginUIDaemon.stop_instance(plugin_id)


def test_runtime_exit_request_shuts_down_cleanly(minimal_worker_script):
    plugin_id = "test_runtime_exit"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=minimal_worker_script)
    proc = daemon._proc

    result = daemon.call("exit", {})

    assert result.get("status") == "ok"

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)

    # poll() is not None is satisfied by ANY termination, including a crash
    # (e.g. returncode -6/SIGABRT) — assert a genuine clean exit, not merely
    # that the process is gone.
    assert proc.returncode == 0

    PluginUIDaemon.stop_instance(plugin_id)


def test_runtime_native_close_emits_window_closed_event(minimal_worker_script):
    """Proves the mechanism the Hub's status-widget "Closed" state depends
    on: a close that originates from the window itself (not a Hub request)
    must be reported to the Hub proactively, not discovered lazily via
    process_exited sometime later.
    """
    plugin_id = "test_runtime_window_closed_event"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=minimal_worker_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))
    daemon.ensure_started()

    daemon.call("simulate_native_close", {})

    deadline = time.monotonic() + 5.0
    topics = [t for t, _ in received]
    while time.monotonic() < deadline and "window_closed" not in topics:
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)
        topics = [t for t, _ in received]

    assert "window_closed" in topics

    PluginUIDaemon.stop_instance(plugin_id)


def test_worker_survives_a_stray_event_frame_from_the_hub(minimal_worker_script):
    """Regression test for the resolved "silent event frame" trap.
    `PluginUIDaemon` no longer exposes `send_event()` (the Hub always uses
    `call()` to reach a worker now), but nothing stops a stray or
    protocol-mismatched frame with `kind: "event"` from arriving anyway —
    it must be logged and dropped, not silently swallowed in a way that
    corrupts frame boundaries or leaves the reader thread stuck. `_send_frame`
    is used directly here (bypassing the public API entirely) specifically
    to simulate that rogue-frame scenario.
    """
    plugin_id = "test_runtime_stray_event_frame"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=minimal_worker_script)

    daemon._send_frame({"kind": "event", "topic": "bogus_hub_push", "payload": {}})

    # The connection must still be healthy afterward — the stray frame must
    # not desync frame boundaries or leave anything hung.
    result = daemon.call("focus", {})
    assert result.get("status") == "ok"

    PluginUIDaemon.stop_instance(plugin_id)


def test_runtime_unknown_method_returns_error(minimal_worker_script):
    plugin_id = "test_runtime_unknown_method"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=minimal_worker_script)

    result = daemon.call("not_a_real_method", {})

    assert "error" in result

    PluginUIDaemon.stop_instance(plugin_id)


def test_theme_changed_colors_reach_the_panel(theme_aware_worker_script):
    """Regression test: a `theme_changed` request's `colors` payload must
    update `theme_fallback.DynamicColors` before the panel's own
    `_apply_theme_styles()` runs, not be silently dropped.
    """
    plugin_id = "test_runtime_theme_colors"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=theme_aware_worker_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))

    result = daemon.call("theme_changed", {"colors": {"ACCENT_PRIMARY": "#2f81f7"}})
    assert result == {"status": "ok"}

    deadline = time.monotonic() + 5.0
    topics = dict(received)
    while time.monotonic() < deadline and "theme_applied" not in topics:
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)
        topics = dict(received)

    assert topics.get("theme_applied") == {"accent": "#2f81f7"}

    PluginUIDaemon.stop_instance(plugin_id)


def test_ready_is_sent_before_a_slow_begin_async_init_completes(slow_phase_2_worker_script):
    """Regression test: begin_async_init() must run *after* the ready
    handshake, not before it — otherwise a slow one (in practice: heavy
    matplotlib/umap/sklearn imports on first use) delays "ready" past the
    Hub's own Ready Gate timeout, even though the window itself was fine.

    `ensure_started()` blocks until "ready" arrives (or its own timeout), so
    its wall-clock time is a direct measurement of when "ready" was sent.
    """
    plugin_id = "test_runtime_slow_phase_2"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=slow_phase_2_worker_script)

    start = time.monotonic()
    daemon.ensure_started(timeout=10.0)
    elapsed = time.monotonic() - start

    # begin_async_init() sleeps 2.0s; ready must land well before that
    # completes, proving it wasn't sent from behind begin_async_init().
    assert elapsed < 1.5

    PluginUIDaemon.stop_instance(plugin_id)


def test_hub_theme_is_applied_before_any_widget_is_built(theme_gate_worker_script):
    """The success half of the theme gate: the Hub's real colors must be on
    `theme_fallback.Colors` *before* `panel_factory()` runs, not merely by
    the time "ready" is sent.
    """
    plugin_id = "test_runtime_theme_gate_success"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=theme_gate_worker_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))

    daemon.ensure_started(timeout=10.0)

    deadline = time.monotonic() + 5.0
    topics = dict(received)
    while time.monotonic() < deadline and "startup_accent" not in topics:
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)
        topics = dict(received)

    assert topics.get("startup_accent") == {"accent": FAKE_HUB_COLORS["ACCENT_PRIMARY"]}

    PluginUIDaemon.stop_instance(plugin_id)


def test_worker_never_sends_ready_when_the_hub_theme_cannot_be_confirmed(minimal_worker_script):
    """Regression test for "no fallback theme": a worker that can't reach
    CoreServices must refuse to build any window — not silently fall back to
    `theme_fallback`'s hardcoded DARK/LIGHT palette (see
    `_confirm_hub_theme_or_exit`). Simulated by clearing the `core_services`
    fixture's config for this one daemon, exactly like a worker spawned
    without KARCYTICS_CORE_SERVICES_PORT/KARCYTICS_CORE_SERVICES_TOKEN set.
    """
    PluginUIDaemon._core_services_port = None
    PluginUIDaemon._core_services_token = None

    plugin_id = "test_runtime_theme_gate_failure"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=minimal_worker_script)

    with pytest.raises(RuntimeError, match="cannot confirm the Hub's real theme"):
        daemon.ensure_started(timeout=5.0)


@pytest.fixture
def data_ready_worker_script(tmp_path):
    """A panel with a real one-shot `data_ready` signal, driven by
    `load_workflow()` the same way a real analysis panel would be — proves
    `run()`'s `panel_data_ready` forwarding actually observes the panel's
    own signal end to end, not just that the connection was made.
    """
    script_path = tmp_path / "data_ready_worker.py"
    code = """
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.ui_daemon_runtime import run

class Panel(QLabel):
    data_ready = pyqtSignal()

    def load_workflow(self, payload, filename=None, metadata=None):
        self.setText(f"loaded {filename}")
        QTimer.singleShot(0, self.data_ready.emit)

def build_panel():
    return Panel("waiting for data")

if __name__ == "__main__":
    run(build_panel, window_title="Data Ready Test Module")
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_inject_workflow_then_panel_data_ready_event(data_ready_worker_script):
    """The Hub-observable half of the fix this test guards: a caller driving
    an isolated panel purely over IPC (no direct object reference) needs a
    positive "the data actually finished loading" signal distinct from
    `inject_workflow`'s own `{"status": "ok"}`, which only confirms the
    request was accepted — not that the one-tick-later load it schedules
    ever completed.
    """
    plugin_id = "test_runtime_data_ready"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=data_ready_worker_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))

    result = daemon.call("inject_workflow", {"payload": {}, "filename": "sample.fcs"})
    assert result.get("status") == "ok"

    deadline = time.monotonic() + 5.0
    topics = []
    while time.monotonic() < deadline and "panel_data_ready" not in topics:
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)
        topics = [t for t, _ in received]

    assert "panel_data_ready" in topics

    PluginUIDaemon.stop_instance(plugin_id)


@pytest.fixture
def failing_load_worker_script(tmp_path):
    """A panel whose `load_workflow` always raises — proves a deferred load
    failure (the part `inject_workflow`'s immediate `{"status": "ok"}`
    response can never cover, since the actual call happens a tick later)
    reaches the Hub as a `workflow_injection_failed` event instead of
    vanishing into this process's own stderr.
    """
    script_path = tmp_path / "failing_load_worker.py"
    code = """
from PyQt6.QtWidgets import QLabel
from karcytics_sdk.plugin.ui_daemon_runtime import run

class Panel(QLabel):
    def load_workflow(self, payload, filename=None, metadata=None):
        raise ValueError("simulated load failure")

def build_panel():
    return Panel("about to fail")

if __name__ == "__main__":
    run(build_panel, window_title="Failing Load Test Module")
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_inject_workflow_failure_sends_workflow_injection_failed_event(failing_load_worker_script):
    plugin_id = "test_runtime_injection_failed"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=failing_load_worker_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))

    result = daemon.call("inject_workflow", {"payload": {}, "filename": "sample.fcs"})
    assert result.get("status") == "ok"

    deadline = time.monotonic() + 5.0
    topics = {}
    while time.monotonic() < deadline and "workflow_injection_failed" not in topics:
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)
        topics = dict(received)

    assert "workflow_injection_failed" in topics
    assert "simulated load failure" in topics["workflow_injection_failed"]["error"]

    PluginUIDaemon.stop_instance(plugin_id)

    PluginUIDaemon.stop_instance(plugin_id)
