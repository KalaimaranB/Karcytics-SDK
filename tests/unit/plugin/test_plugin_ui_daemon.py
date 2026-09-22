"""Unit tests for karcytics_sdk.plugin.daemon.PluginUIDaemon."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from karcytics_sdk.plugin.daemon import PluginUIDaemon


@pytest.fixture
def mock_ui_daemon_script(tmp_path):
    """Create a temporary Python script that acts as a mock UI daemon worker.

    Speaks the kind-tagged frame protocol (request/response/event) rather than
    the plain PluginDaemon protocol — this is what distinguishes PluginUIDaemon.
    """
    script_path = tmp_path / "mock_ui_worker.py"
    code = """
import sys
import struct
import threading
import time
import msgpack

def write_frame(data):
    payload = msgpack.packb(data, use_bin_type=True)
    header = struct.pack('>I', len(payload))
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()

def read_frame():
    header = sys.stdin.buffer.read(4)
    if not header or len(header) < 4:
        return None
    length = struct.unpack('>I', header)[0]
    payload = sys.stdin.buffer.read(length)
    return msgpack.unpackb(payload, raw=False)

def emit_spontaneous_events():
    # Simulate the plugin's own UI firing tutorial/state events with no
    # request behind them at all, at any time — the thing PluginDaemon's
    # synchronous call() protocol can't represent.
    time.sleep(0.15)
    write_frame({"kind": "event", "topic": "state_changed", "payload": {"n": 1}})

def main():
    write_frame({"kind": "event", "topic": "ready", "payload": {"geometry": [0, 0, 800, 600]}})
    threading.Thread(target=emit_spontaneous_events, daemon=True).start()

    while True:
        frame = read_frame()
        if not frame:
            break
        method = frame.get("method")
        request_id = frame.get("request_id")
        kwargs = frame.get("kwargs", {})

        if method == "exit":
            break
        elif method == "ping":
            write_frame({"kind": "response", "request_id": request_id, "payload": {"status": "pong"}})
        elif method == "echo":
            write_frame({
                "kind": "response",
                "request_id": request_id,
                "payload": {"echo": kwargs.get("msg")},
            })
        elif method == "slow_echo":
            time.sleep(kwargs.get("delay", 0.3))
            write_frame({
                "kind": "response",
                "request_id": request_id,
                "payload": {"echo": kwargs.get("msg")},
            })
        else:
            write_frame({
                "kind": "response",
                "request_id": request_id,
                "payload": {"error": f"Unknown method {method}"},
            })

if __name__ == "__main__":
    main()
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_ui_daemon_singleton_and_ready_handshake(mock_ui_daemon_script):
    plugin_id = "test_ui_plugin_singleton"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=mock_ui_daemon_script)
    assert daemon is PluginUIDaemon.get_instance(plugin_id)

    res = daemon.call("ping", {})
    assert res == {"status": "pong"}

    PluginUIDaemon.stop_instance(plugin_id)
    assert daemon._proc is None


def test_ui_daemon_call_request_response(mock_ui_daemon_script):
    plugin_id = "test_ui_plugin_echo"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=mock_ui_daemon_script)

    res = daemon.call("echo", {"msg": "hello"})
    assert res == {"echo": "hello"}

    PluginUIDaemon.stop_instance(plugin_id)


def test_ui_daemon_dispatches_unsolicited_event(mock_ui_daemon_script):
    """The worker fires a state_changed event with no request behind it at all —
    event_received must still deliver it, proving the reader thread dispatches
    events independent of whether anything is blocked in call().
    """
    plugin_id = "test_ui_plugin_events"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=mock_ui_daemon_script)

    received = []
    daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not received:
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)

    assert received == [("state_changed", {"n": 1})]

    PluginUIDaemon.stop_instance(plugin_id)


def test_ui_daemon_concurrent_calls_do_not_block_each_other(mock_ui_daemon_script):
    """Unlike PluginDaemon (single _call_lock serializes every call), PluginUIDaemon
    must let a fast call return while a slow one is still in flight — each call
    gets its own request_id and its own response queue off the shared reader.
    """
    plugin_id = "test_ui_plugin_concurrent"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=mock_ui_daemon_script)

    results: dict[str, dict] = {}

    def call_slow():
        results["slow"] = daemon.call("slow_echo", {"msg": "slow", "delay": 0.4}, timeout=5.0)

    import threading

    t = threading.Thread(target=call_slow)
    t.start()

    time.sleep(0.05)  # let the slow call's request land first
    fast_result = daemon.call("ping", {}, timeout=5.0)
    t.join(timeout=5.0)

    assert fast_result == {"status": "pong"}
    assert results["slow"] == {"echo": "slow"}

    PluginUIDaemon.stop_instance(plugin_id)


def test_ui_daemon_call_timeout_raises(mock_ui_daemon_script):
    plugin_id = "test_ui_plugin_timeout"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=mock_ui_daemon_script)

    with pytest.raises(TimeoutError):
        daemon.call("slow_echo", {"msg": "x", "delay": 2.0}, timeout=0.2)

    PluginUIDaemon.stop_instance(plugin_id)


def test_plugin_ui_daemon_has_no_send_event_method(mock_ui_daemon_script):
    """PluginUIDaemon must not expose a way to push an unsolicited event at
    a worker — the worker's RequestDispatcher only ever answers `request`
    frames, so a `send_event()` here would silently manufacture a response
    nobody's waiting for (this was a real, shipped bug; see
    docs/internal/24, "Known failure modes" #3). Removing the method makes
    that misuse an immediate AttributeError at the call site instead of a
    frame that vanishes at runtime.
    """
    plugin_id = "test_ui_plugin_no_send_event"
    daemon = PluginUIDaemon.start_instance(plugin_id, daemon_script_path=mock_ui_daemon_script)

    assert not hasattr(daemon, "send_event")

    PluginUIDaemon.stop_instance(plugin_id)


def test_shutdown_concurrent_with_in_flight_ensure_started_does_not_crash(tmp_path):
    """Regression test: shutdown() arriving while another thread is still
    inside ensure_started() -> _start_process() -> _await_ready_frame()
    used to race that thread's use of self._proc — _terminate_process()
    could null out/close the subprocess the other thread was concurrently
    reading from, which segfaulted rather than raising. shutdown() now
    acquires the same _start_lock ensure_started() holds for its whole
    attempt, so this must serialize cleanly instead of crashing.
    """
    script_path = tmp_path / "slow_ready_worker.py"
    code = """
import sys
import time
import struct
import msgpack

def write_frame(data):
    payload = msgpack.packb(data, use_bin_type=True)
    header = struct.pack('>I', len(payload))
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()

time.sleep(1.5)
write_frame({"kind": "event", "topic": "ready", "payload": {}})

while True:
    header = sys.stdin.buffer.read(4)
    if not header or len(header) < 4:
        break
"""
    script_path.write_text(code, encoding="utf-8")

    plugin_id = "test_shutdown_race"
    daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=script_path)

    import threading

    start_exceptions = []

    def _start():
        try:
            daemon.ensure_started(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            start_exceptions.append(exc)

    starter = threading.Thread(target=_start)
    starter.start()

    time.sleep(0.2)  # ensure the starter thread is inside _await_ready_frame
    daemon.shutdown()  # must not crash the process

    starter.join(timeout=15.0)
    assert not starter.is_alive()

    PluginUIDaemon.stop_instance(plugin_id)


def test_worker_env_carries_core_services_port_and_token(tmp_path):
    """The Hub records its CoreServicesServer port and bearer token once, via
    set_core_services(); every worker this daemon spawns afterward must see
    both in its own environment, so the worker's own composition root can
    build an authenticated CoreServicesClient without the Hub threading
    them through every individual spawn call.
    """
    script_path = tmp_path / "env_echo_worker.py"
    code = """
import os
import sys
import struct
import msgpack

def write_frame(data):
    payload = msgpack.packb(data, use_bin_type=True)
    header = struct.pack('>I', len(payload))
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()

write_frame({
    "kind": "event",
    "topic": "ready",
    "payload": {
        "port": os.environ.get("KARCYTICS_CORE_SERVICES_PORT"),
        "token": os.environ.get("KARCYTICS_CORE_SERVICES_TOKEN"),
    },
})

while True:
    header = sys.stdin.buffer.read(4)
    if not header or len(header) < 4:
        break
"""
    script_path.write_text(code, encoding="utf-8")

    plugin_id = "test_core_services_port_plugin"
    PluginUIDaemon.set_core_services(54321, "secret-token")  # noqa: S106
    try:
        daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=script_path)

        received = []
        daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))
        daemon.ensure_started()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not received:
            from PyQt6.QtWidgets import QApplication

            QApplication.processEvents()
            time.sleep(0.02)

        assert received
        assert received[0] == ("ready", {"port": "54321", "token": "secret-token"})
    finally:
        PluginUIDaemon.stop_instance(plugin_id)
        PluginUIDaemon._core_services_port = None
        PluginUIDaemon._core_services_token = None


def test_worker_env_carries_default_icon_path(tmp_path):
    """The Hub records its own icon path once, via set_default_icon_path();
    every worker this daemon spawns afterward must see it in its own
    environment, so ui_daemon_runtime.run() has a fallback icon to use when
    the plugin itself doesn't ship one — see _resolve_app_icon_path().
    """
    script_path = tmp_path / "icon_env_echo_worker.py"
    code = """
import os
import sys
import struct
import msgpack

def write_frame(data):
    payload = msgpack.packb(data, use_bin_type=True)
    header = struct.pack('>I', len(payload))
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()

write_frame({
    "kind": "event",
    "topic": "ready",
    "payload": {"icon_path": os.environ.get("KARCYTICS_CORE_ICON_PATH")},
})

while True:
    header = sys.stdin.buffer.read(4)
    if not header or len(header) < 4:
        break
"""
    script_path.write_text(code, encoding="utf-8")

    plugin_id = "test_default_icon_path_plugin"
    fake_icon_path = str(tmp_path / "logo.icns")
    PluginUIDaemon.set_default_icon_path(fake_icon_path)
    try:
        daemon = PluginUIDaemon.get_instance(plugin_id, daemon_script_path=script_path)

        received = []
        daemon.event_received.connect(lambda topic, payload: received.append((topic, payload)))
        daemon.ensure_started()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not received:
            from PyQt6.QtWidgets import QApplication

            QApplication.processEvents()
            time.sleep(0.02)

        assert received
        assert received[0] == ("ready", {"icon_path": fake_icon_path})
    finally:
        PluginUIDaemon.stop_instance(plugin_id)
        PluginUIDaemon._core_icon_path = None


def test_resolve_daemon_script_finds_plugin_root_layout(tmp_path):
    """Older plugins (manifest_version 2, no src/karcytics_plugins/<id>/ tree —
    e.g. Synthetic Biology) keep everything at the plugin's own root next to
    manifest.json/__init__.py. ui_daemon.py must be discoverable there too,
    not just under the newer src/ layout Flow Cytometry uses.
    """
    plugin_id = "test_root_layout_plugin"
    plugin_dir = tmp_path / ".karcytics" / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True)
    root_script = plugin_dir / "ui_daemon.py"
    root_script.write_text("# not executed by this test")

    daemon = PluginUIDaemon(plugin_id)
    with patch.object(Path, "home", return_value=tmp_path):
        resolved = daemon._resolve_daemon_script()

    assert resolved == root_script


def test_resolve_daemon_script_prefers_src_layout_when_both_exist(tmp_path):
    plugin_id = "test_both_layouts_plugin"
    plugin_dir = tmp_path / ".karcytics" / "plugins" / plugin_id
    src_dir = plugin_dir / "src" / "karcytics_plugins" / plugin_id
    src_dir.mkdir(parents=True)
    src_script = src_dir / "ui_daemon.py"
    src_script.write_text("# not executed by this test")
    (plugin_dir / "ui_daemon.py").write_text("# not executed by this test")

    daemon = PluginUIDaemon(plugin_id)
    with patch.object(Path, "home", return_value=tmp_path):
        resolved = daemon._resolve_daemon_script()

    assert resolved == src_script
