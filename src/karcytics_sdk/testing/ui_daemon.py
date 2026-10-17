"""Drive a plugin's real `ui_daemon.py` subprocess from an integration test.

Every isolated plugin should prove its daemon reaches "ready" and answers
requests standalone, speaking the real msgpack-over-stdio protocol with
nothing mocked. This module is the shared harness for that, so each plugin
repo's test is a few lines instead of its own copy of the pipe plumbing.

Usage in a plugin's ``tests/integration/test_ui_daemon.py``::

    from karcytics_sdk.testing.ui_daemon import spawn_ui_daemon, write_frame

    def test_reaches_ready():
        with spawn_ui_daemon(DAEMON_SCRIPT, cwd=REPO_ROOT) as daemon:
            frame = daemon.io.wait_for_event("ready", "never sent 'ready'")
            assert len(frame["payload"]["geometry"]) == 4
"""

from __future__ import annotations

import io
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import msgpack

_HEADER_BYTES = 4
DEFAULT_HUB_COLORS = {"BG_DARKEST": "#0a0a0a", "ACCENT_PRIMARY": "#2f81f7"}


def write_frame(stream: Any, data: dict) -> None:
    """Send one length-prefixed msgpack frame to the daemon's stdin."""
    payload = msgpack.packb(data, use_bin_type=True)
    stream.write(struct.pack(">I", len(payload)) + payload)
    stream.flush()


class DaemonIO:
    """Continuously drains a daemon's stdout (as frames) and stderr (raw).

    Both pipes are read on background threads from the moment the process
    starts. Reading only stdout on the test thread leaves stderr undrained;
    once the daemon fills that OS pipe buffer it blocks forever while the
    test blocks reading stdout forever. Windows' small pipe buffers make
    that far more likely there. Every wait is bounded, so a stuck daemon
    fails the test with its stderr attached instead of hanging CI.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._frames: queue.Queue[dict] = queue.Queue()
        self._stderr_chunks: list[bytes] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        stream = cast(io.BufferedReader, self._proc.stdout)
        while True:
            header = stream.read(_HEADER_BYTES)
            if len(header) < _HEADER_BYTES:
                return
            (length,) = struct.unpack(">I", header)
            self._frames.put(msgpack.unpackb(stream.read(length), raw=False))

    def _pump_stderr(self) -> None:
        # read1() returns whatever is available after one raw read; read(n)
        # would wait for a full n bytes and hide a short trace from a daemon
        # that then stalls.
        stderr = cast(io.BufferedReader, self._proc.stderr)
        for chunk in iter(lambda: stderr.read1(4096), b""):
            self._stderr_chunks.append(chunk)

    @property
    def stderr(self) -> str:
        return b"".join(self._stderr_chunks).decode(errors="replace")

    def _fail(self, message: str, timeout: float) -> AssertionError:
        # A native crash (e.g. inside Qt) leaves no traceback; only the exit
        # code tells "stuck" apart from "died".
        returncode = self._proc.poll()
        status = "still running" if returncode is None else f"exited with code {returncode}"
        return AssertionError(
            f"{message} (waited {timeout:.0f}s; process {status})\n--- daemon stderr ---\n{self.stderr}"
        )

    def read_frame(self, timeout_error: str, timeout: float = 20.0) -> dict:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            raise self._fail(timeout_error, timeout) from None

    def wait_for(self, match: Callable[[dict], bool], timeout_error: str, timeout: float = 20.0) -> dict:
        """The first frame satisfying ``match``; earlier frames are discarded."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                frame = self._frames.get(timeout=remaining)
            except queue.Empty:
                break
            if match(frame):
                return frame
        raise self._fail(timeout_error, timeout)

    def wait_for_event(self, topic: str, timeout_error: str, timeout: float = 20.0) -> dict:
        return self.wait_for(lambda f: f.get("kind") == "event" and f.get("topic") == topic, timeout_error, timeout)

    def wait_for_response(self, request_id: int, timeout_error: str, timeout: float = 20.0) -> dict:
        return self.wait_for(
            lambda f: f.get("kind") == "response" and f.get("request_id") == request_id, timeout_error, timeout
        )


@dataclass
class RunningDaemon:
    process: subprocess.Popen
    io: DaemonIO

    def request(self, request_id: int, method: str, **kwargs: Any) -> None:
        write_frame(
            self.process.stdin, {"kind": "request", "request_id": request_id, "method": method, "kwargs": kwargs}
        )


@contextmanager
def spawn_ui_daemon(
    script: Path,
    cwd: Path,
    hub_colors: dict[str, str] | None = None,
    python: str = sys.executable,
) -> Iterator[RunningDaemon]:
    """Start ``script`` the way ``PluginUIDaemon`` does, against a stand-in Hub.

    A minimal ``CoreServicesServer`` answers ``theme.get_current_colors``:
    the daemon runtime confirms the Hub's theme before building any UI and
    never reaches "ready" without it. The process is killed on exit.
    """
    from karcytics_sdk.host.core_services import CoreServicesServer

    colors = dict(hub_colors or DEFAULT_HUB_COLORS)
    server = CoreServicesServer()
    server.register("theme.get_current_colors", lambda _kwargs: dict(colors))
    server.start()

    env = os.environ.copy()
    env["KARCYTICS_CORE_SERVICES_PORT"] = str(server.port)
    env["KARCYTICS_CORE_SERVICES_TOKEN"] = server.token
    # Unbuffered so a timeout's captured stderr shows the daemon's output
    # right up to the cutoff, not whatever was still in a pipe buffer.
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [python, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        yield RunningDaemon(proc, DaemonIO(proc))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        server.stop()
