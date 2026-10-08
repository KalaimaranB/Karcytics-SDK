"""Unit tests for karcytics_sdk.plugin.daemon.PluginDaemon."""

import pytest

from karcytics_sdk.plugin.daemon import PluginDaemon, _build_worker_env


def test_build_worker_env_strips_frozen_hub_library_paths(monkeypatch):
    """A PyInstaller-frozen Hub sets DYLD_*/QT_*/PYTHONHOME so its own
    bootloader finds the Qt/Python it bundles inside the .app. A plugin
    worker is a completely separate interpreter with its own venv and its
    own real PySide6 — inheriting these unmodified made the worker's dynamic
    linker load the Hub's bundled Qt frameworks *alongside* its own,
    producing objc class collisions and a fatal "Could not load the Qt
    platform plugin" the moment the worker touched any Qt API.
    """
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/some/Karcytics.app/Contents/Frameworks")
    monkeypatch.setenv("DYLD_FRAMEWORK_PATH", "/some/Karcytics.app/Contents/Frameworks")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/some/karcytics/lib")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/some/Karcytics.app/Contents/Frameworks/PySide6/Qt6/plugins")
    monkeypatch.setenv("PYTHONHOME", "/some/Karcytics.app/Contents/Frameworks")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "kept")

    env = _build_worker_env({"KARCYTICS_PLUGIN_ID": "flow_cytometry"})

    for var in (
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "LD_LIBRARY_PATH",
        "QT_PLUGIN_PATH",
        "PYTHONHOME",
    ):
        assert var not in env, f"{var} must be stripped from the worker subprocess environment"

    assert env["SOME_UNRELATED_VAR"] == "kept"
    assert env["KARCYTICS_PLUGIN_ID"] == "flow_cytometry"


@pytest.fixture
def mock_daemon_script(tmp_path):
    """Create a temporary Python script that acts as a mock daemon worker."""
    script_path = tmp_path / "mock_worker.py"
    code = """
import sys
import struct
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

def main():
    write_frame({"status": "ready"})
    while True:
        frame = read_frame()
        if not frame:
            break
        method = frame.get("method")
        kwargs = frame.get("kwargs", {})

        if method == "exit":
            break
        elif method == "ping":
            write_frame({"status": "pong"})
        elif method == "echo":
            write_frame({"status": "ok", "echo": kwargs.get("msg")})
        elif method == "crash":
            sys.exit(1)
        elif method == "slow":
            time.sleep(0.5)
            write_frame({"status": "ok", "done": True})
        elif method == "large_slow":
            payload = msgpack.packb({"status": "ok", "blob": "x" * 500000}, use_bin_type=True)
            header = struct.pack(">I", len(payload))
            full = header + payload
            chunk_size = 8192
            for i in range(0, len(full), chunk_size):
                sys.stdout.buffer.write(full[i:i + chunk_size])
                sys.stdout.buffer.flush()
                time.sleep(0.01)
        else:
            write_frame({"error": f"Unknown method {method}"})

if __name__ == "__main__":
    main()
"""
    script_path.write_text(code, encoding="utf-8")
    return script_path


def test_daemon_singleton_and_lifecycle(mock_daemon_script):
    plugin_id = "test_plugin_singleton"
    daemon = PluginDaemon.get_instance(plugin_id, daemon_script_path=mock_daemon_script)
    assert daemon is PluginDaemon.get_instance(plugin_id)

    res = daemon.call("ping", {})
    assert res == {"status": "pong"}

    res_echo = daemon.call("echo", {"msg": "hello_world"})
    assert res_echo == {"status": "ok", "echo": "hello_world"}

    PluginDaemon.stop_instance(plugin_id)
    assert daemon._proc is None


def test_daemon_cancellation(mock_daemon_script):
    plugin_id = "test_plugin_cancel"
    daemon = PluginDaemon.get_instance(plugin_id, daemon_script_path=mock_daemon_script)

    cancelled = True
    res = daemon.call("slow", {}, cancel_poll=lambda: cancelled)
    assert res == {"error": "Task cancelled."}

    PluginDaemon.stop_instance(plugin_id)


def test_daemon_crash_recovery(mock_daemon_script):
    plugin_id = "test_plugin_crash"
    daemon = PluginDaemon.get_instance(plugin_id, daemon_script_path=mock_daemon_script)

    # First call pings
    assert daemon.call("ping", {}) == {"status": "pong"}

    # Second call triggers crash in script, but PluginDaemon auto-restarts and retries next call
    res_crash = daemon.call("crash", {})
    assert "error" in res_crash

    # Next call should succeed after auto-restart
    res_recovery = daemon.call("ping", {})
    assert res_recovery == {"status": "pong"}

    PluginDaemon.stop_instance(plugin_id)


def test_daemon_large_response_spanning_multiple_polls(mock_daemon_script):
    """A response frame that takes longer than a single 0.1s poll to fully
    arrive must still be read correctly and must not desync the stream.

    Regression test: _read_bytes_exact() used to discard any bytes already
    read whenever its per-poll timeout elapsed before the full frame
    arrived, and the next poll would then reinterpret bytes from the middle
    of that still-in-flight payload as a brand new frame header. That
    permanently corrupted frame alignment for any response too large to
    land within one polling slice, so the caller spun until its own outer
    `timeout` gave up even though the daemon had already sent a complete,
    valid response.
    """
    plugin_id = "test_plugin_large_slow"
    daemon = PluginDaemon.get_instance(plugin_id, daemon_script_path=mock_daemon_script)

    res = daemon.call("large_slow", {}, timeout=5.0)
    assert res.get("status") == "ok"
    assert res.get("blob") == "x" * 500000

    # The stream must remain aligned for a subsequent call, proving no
    # leftover/misread bytes were left behind by the large response.
    res_ping = daemon.call("ping", {})
    assert res_ping == {"status": "pong"}

    PluginDaemon.stop_instance(plugin_id)
