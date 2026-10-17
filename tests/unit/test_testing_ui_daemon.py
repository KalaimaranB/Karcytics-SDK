"""The shared daemon test harness, driven against a tiny scripted fake daemon."""

from __future__ import annotations

import textwrap

import pytest

from karcytics_sdk.testing.ui_daemon import spawn_ui_daemon

_FAKE_DAEMON = textwrap.dedent(
    """
    import os, struct, sys
    import msgpack

    def send(frame):
        payload = msgpack.packb(frame, use_bin_type=True)
        sys.stdout.buffer.write(struct.pack(">I", len(payload)) + payload)
        sys.stdout.buffer.flush()

    sys.stderr.write("port=" + os.environ["KARCYTICS_CORE_SERVICES_PORT"] + "\\n")
    sys.stderr.flush()
    send({"kind": "event", "topic": "noise", "payload": None})
    send({"kind": "event", "topic": "ready", "payload": {"geometry": [0, 0, 10, 10]}})

    header = sys.stdin.buffer.read(4)
    request = msgpack.unpackb(sys.stdin.buffer.read(struct.unpack(">I", header)[0]), raw=False)
    send({"kind": "response", "request_id": request["request_id"], "payload": {"echo": request["method"]}})
    if request["method"] == "hang":
        sys.stdin.buffer.read()
    """
)


@pytest.fixture
def fake_daemon(tmp_path):
    script = tmp_path / "fake_daemon.py"
    script.write_text(_FAKE_DAEMON)
    return script


def test_waits_past_unrelated_frames_to_the_requested_event(fake_daemon, tmp_path):
    with spawn_ui_daemon(fake_daemon, cwd=tmp_path) as daemon:
        frame = daemon.io.wait_for_event("ready", "no ready", timeout=10)

    assert frame["payload"] == {"geometry": [0, 0, 10, 10]}


def test_request_round_trips_to_a_matching_response(fake_daemon, tmp_path):
    with spawn_ui_daemon(fake_daemon, cwd=tmp_path) as daemon:
        daemon.io.wait_for_event("ready", "no ready", timeout=10)
        daemon.request(7, "focus")
        response = daemon.io.wait_for_response(7, "no response", timeout=10)

        assert response == {"kind": "response", "request_id": 7, "payload": {"echo": "focus"}}
        assert daemon.process.wait(timeout=10) == 0


def test_timeout_reports_the_daemons_stderr_and_status(fake_daemon, tmp_path):
    with spawn_ui_daemon(fake_daemon, cwd=tmp_path) as daemon:
        daemon.io.wait_for_event("ready", "no ready", timeout=10)
        with pytest.raises(AssertionError) as excinfo:
            daemon.io.wait_for_event("never", "no such event", timeout=0.5)

    message = str(excinfo.value)
    assert "no such event" in message
    assert "still running" in message
    assert "port=" in message


def test_daemon_is_killed_when_the_context_exits(fake_daemon, tmp_path):
    with spawn_ui_daemon(fake_daemon, cwd=tmp_path) as daemon:
        daemon.io.wait_for_event("ready", "no ready", timeout=10)
        daemon.request(1, "hang")
        daemon.io.wait_for_response(1, "no response", timeout=10)
        proc = daemon.process

    assert proc.poll() is not None
