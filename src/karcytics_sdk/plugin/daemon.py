"""Plugin Worker Daemon — Long-lived background process manager with msgpack IPC.

Provides IPC communication between plugin frontend logic and isolated worker
processes running in plugin virtual environments.
"""

from __future__ import annotations

import itertools
import os
import queue
import select
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import msgpack
from PyQt6.QtCore import QObject, pyqtSignal

from .logging import get_logger

logger = get_logger(__name__, "karcytics_sdk")

# A PyInstaller-frozen Hub sets these so its own bootloader can find the Qt/
# Python libraries bundled inside the .app — but a plugin worker is a
# completely separate interpreter with its own venv and its own real PyQt6
# install. Inheriting them unmodified via os.environ.copy() makes the
# child's dynamic linker load the Hub's bundled Qt frameworks *alongside*
# the venv's own, producing objc class collisions and a fatal
# "Could not load the Qt platform plugin" once the worker touches any Qt
# API (see docs/internal/26 — the UI daemon does this, the analysis daemon
# currently doesn't, which is why only the former ever surfaced this).
_FROZEN_ENV_VARS_TO_STRIP = (
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "DYLD_INSERT_LIBRARIES",
    "LD_LIBRARY_PATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
    "PYTHONHOME",
)


def _build_worker_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the environment for a plugin worker subprocess.

    Starts from a copy of the current process's environment with the
    frozen-Hub-specific variables above stripped, so a worker process
    running its own venv's Python never picks up the Hub's bundled Qt/
    library search paths.
    """
    env = os.environ.copy()
    for var in _FROZEN_ENV_VARS_TO_STRIP:
        env.pop(var, None)
    if extra:
        env.update(extra)
    return env


class PluginDaemon(QObject):
    """Manages a long-lived worker process for a plugin via length-prefixed msgpack IPC."""

    _instances: ClassVar[dict[str, PluginDaemon]] = {}
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        plugin_id: str,
        daemon_script_path: Path | str | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.plugin_id = plugin_id
        self.daemon_script_path = Path(daemon_script_path) if daemon_script_path else None
        self._logger = get_logger(__name__, plugin_id)
        self._proc: subprocess.Popen | None = None
        self._call_lock = threading.Lock()
        self._retry_count = 0
        self._max_retries = 3
        # In-flight frame read state, resumed across successive
        # _read_frame_with_timeout() polls rather than restarted — see that
        # method and _read_bytes_exact() for why this must persist.
        self._read_header_buf = bytearray()
        self._read_payload_buf = bytearray()
        self._read_payload_len: int | None = None
        # Captured by _stderr_reader_loop, not read directly off self._proc
        # .stderr — see _start_process's stderr_msg for why.
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._stderr_thread: threading.Thread | None = None

    @classmethod
    def get_instance(
        self,
        plugin_id: str,
        daemon_script_path: Path | str | None = None,
    ) -> PluginDaemon:
        """Get or create the singleton PluginDaemon for a given plugin ID.

        Some callers (e.g. the Hub's own module-loading code) fetch the
        instance for a side effect unrelated to spawning — before the real
        panel factory has run — and so never pass `daemon_script_path`. If
        that call registered the singleton first, a *later* caller that
        does have a real path (a plugin's own factory, or a test overriding
        the default resolution) must not have it silently discarded just
        because the instance already existed — so an explicit path here
        backfills onto an existing instance that doesn't have one yet,
        rather than only ever applying at construction time.
        """
        with PluginDaemon._registry_lock:
            if plugin_id not in PluginDaemon._instances:
                daemon = PluginDaemon(plugin_id, daemon_script_path)
                PluginDaemon._instances[plugin_id] = daemon
            elif daemon_script_path is not None and PluginDaemon._instances[plugin_id].daemon_script_path is None:
                PluginDaemon._instances[plugin_id].daemon_script_path = Path(daemon_script_path)
            return PluginDaemon._instances[plugin_id]

    @classmethod
    def start_instance(
        self,
        plugin_id: str,
        daemon_script_path: Path | str | None = None,
    ) -> PluginDaemon:
        """Start and ensure readiness of the daemon for a plugin ID."""
        daemon = PluginDaemon.get_instance(plugin_id, daemon_script_path)
        daemon.ensure_started()
        return daemon

    @classmethod
    def stop_instance(self, plugin_id: str) -> None:
        """Stop and unregister the daemon for a plugin ID."""
        with PluginDaemon._registry_lock:
            daemon = PluginDaemon._instances.pop(plugin_id, None)
            if daemon:
                daemon.shutdown()

    def _resolve_plugin_python_executable(self) -> Path:
        """Resolve python interpreter in plugin directory or fall back to sys.executable."""
        plugin_dir = Path.home() / ".karcytics" / "plugins" / self.plugin_id
        venv_dir = plugin_dir / ".venv"

        candidates = []
        if sys.platform == "win32":
            candidates.append(venv_dir / "Scripts" / "python.exe")
        else:
            major, minor = sys.version_info.major, sys.version_info.minor
            candidates.append(venv_dir / "bin" / f"python{major}.{minor}")
            candidates.append(venv_dir / "bin" / "python3")

        for c in candidates:
            if c.exists():
                return c

        return Path(sys.executable)

    def _resolve_daemon_script(self) -> Path:
        """Resolve daemon_worker.py path if not explicitly supplied."""
        if self.daemon_script_path and self.daemon_script_path.exists():
            return self.daemon_script_path

        # Check installed plugin directory
        plugin_dir = Path.home() / ".karcytics" / "plugins" / self.plugin_id
        candidate = plugin_dir / "src" / "karcytics_plugins" / self.plugin_id / "analysis" / "daemon_worker.py"
        if candidate.exists():
            return candidate

        # Try import spec location
        try:
            import importlib.util

            mod_name = f"karcytics_plugins.{self.plugin_id}.analysis.daemon_worker"
            spec = importlib.util.find_spec(mod_name)
            if spec and spec.origin:
                return Path(spec.origin)
        except Exception:
            pass

        raise FileNotFoundError(f"Could not locate daemon worker script for plugin '{self.plugin_id}'.")

    def ensure_started(self, timeout: float = 30.0) -> None:
        """Ensure worker subprocess is running and ready."""
        with self._call_lock:
            if self._proc and self._proc.poll() is None:
                return
            self._start_process(timeout=timeout)

    def _start_process(self, timeout: float = 30.0) -> None:
        """Start worker subprocess and wait for startup ready handshake."""
        self._terminate_process()

        python_exe = self._resolve_plugin_python_executable()
        daemon_script = self._resolve_daemon_script()

        self._logger.info(
            "Starting PluginDaemon process using python=%s script=%s",
            python_exe,
            daemon_script,
            extra={"log_event": "process_start"},
        )

        sp_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            sp_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        env = _build_worker_env(
            {
                "PYTHONUNBUFFERED": "1",
                "KARCYTICS_PLUGIN_ID": self.plugin_id,
            }
        )

        start_time = time.monotonic()
        self._proc = subprocess.Popen(
            [str(python_exe), str(daemon_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **sp_kwargs,
        )

        self._stderr_tail.clear()
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader_loop, name=f"plugin-daemon-stderr-{self.plugin_id}", daemon=True
        )
        self._stderr_thread.start()

        # Read ready frame from stdout
        ready_frame = self._read_frame_with_timeout(timeout=timeout)
        if not ready_frame or ready_frame.get("status") != "ready":
            stderr_msg = "\n".join(self._stderr_tail)
            self._terminate_process()
            error_msg = (
                f"PluginDaemon for '{self.plugin_id}' failed ready handshake. "
                f"Virtual Environment connection failed! Frame: {ready_frame}. Stderr: {stderr_msg}"
            )
            self._logger.critical(error_msg, extra={"log_event": "ready_handshake_failed"})
            raise RuntimeError(error_msg)

        self._logger.info(
            "PluginDaemon successfully started and ready.",
            extra={
                "log_event": "process_ready",
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            },
        )

    def _send_frame(self, data: dict) -> None:
        """Write a length-prefixed msgpack frame to subprocess stdin."""
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Subprocess is not running.")
        payload = msgpack.packb(data, use_bin_type=True)
        length_header = struct.pack(">I", len(payload))
        self._proc.stdin.write(length_header + payload)
        self._proc.stdin.flush()

    def _stderr_reader_loop(self) -> None:
        """Continuously drain the worker's stderr for as long as the process lives.

        `stderr=subprocess.PIPE` gives the OS pipe a fixed buffer (64KB on
        macOS); nothing upstream of this reads it during normal operation
        otherwise, since only the failure path in `_start_process` ever
        touched `self._proc.stderr` directly, and only once, after startup
        already failed. A worker that writes enough to stderr during normal
        operation (verbose third-party logging, repeated warnings from a
        long computation, ...) fills that buffer and then blocks on its own
        next `write()` call — including from its main thread — which looks
        indistinguishable from a hang with no obvious cause. Tucking each
        line into `_stderr_tail` instead of discarding it also means the
        failure path above still has something real to report.
        """
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if text:
                self._stderr_tail.append(text)
                self._logger.debug(text, extra={"log_event": "worker_stderr"})

    def _read_bytes_exact(self, num_bytes: int, timeout: float, buf: bytearray) -> bool:  # noqa: C901, PLR0911, PLR0912
        """Read exactly num_bytes into buf (appended in place) using non-blocking os.read.

        Bytes already read are kept in `buf` across calls instead of being
        discarded when `timeout` elapses — for a frame too large to arrive
        within a single polling slice, a fresh local buffer here used to
        throw away everything read so far every time the deadline hit, and
        the *next* call would then reinterpret bytes from the middle of the
        still-in-flight payload as a brand new frame header. That permanently
        desynced the stream for any response too big to land in one
        `timeout` window (e.g. a multi-file FCS batch result), so the caller
        would just spin until its own outer timeout gave up — even though
        the daemon had already written a valid, complete response.

        Returns True once buf holds num_bytes, False if `timeout` elapsed
        first (buf keeps its partial progress so the next call can resume).
        """
        if not self._proc or not self._proc.stdout:
            return False

        fd = self._proc.stdout.fileno()
        start_time = time.time()

        while len(buf) < num_bytes:
            if self._proc.poll() is not None:
                # Process exited — try one last read for remaining bytes
                try:
                    rlist, _, _ = select.select([fd], [], [], 0.01)
                    if rlist:
                        chunk = os.read(fd, num_bytes - len(buf))
                        if chunk:
                            buf.extend(chunk)
                except Exception:
                    pass
                return len(buf) == num_bytes

            remaining_timeout = timeout - (time.time() - start_time)
            if remaining_timeout <= 0:
                return False

            if sys.platform != "win32":
                rlist, _, _ = select.select([fd], [], [], min(0.05, remaining_timeout))
                if not rlist:
                    continue
                try:
                    chunk = os.read(fd, num_bytes - len(buf))
                    if not chunk:
                        return False
                    buf.extend(chunk)
                except (OSError, ValueError):
                    return False
            else:
                time.sleep(0.01)
                try:
                    chunk = os.read(fd, num_bytes - len(buf))
                    if not chunk:
                        return False
                    buf.extend(chunk)
                except (OSError, ValueError):
                    return False

        return True

    def _read_frame_with_timeout(self, timeout: float) -> dict | None:
        """Read a single length-prefixed msgpack frame from stdout.

        Resumable across calls: partial header/payload bytes read during a
        previous call that didn't complete within its `timeout` slice are
        kept in self._read_header_buf / self._read_payload_buf and continued
        here rather than restarted — see _read_bytes_exact() for why
        restarting corrupts frame alignment for large responses.
        """
        if self._read_payload_len is None:
            if not self._read_bytes_exact(4, timeout, self._read_header_buf):
                return None
            self._read_payload_len = struct.unpack(">I", bytes(self._read_header_buf))[0]

        if not self._read_bytes_exact(self._read_payload_len, timeout, self._read_payload_buf):
            return None

        payload = bytes(self._read_payload_buf)
        self._read_header_buf = bytearray()
        self._read_payload_buf = bytearray()
        self._read_payload_len = None
        return msgpack.unpackb(payload, raw=False)

    def call(  # noqa: C901
        self,
        method: str,
        kwargs: dict[str, Any],
        cancel_poll: Callable[[], bool] | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Send a job frame to daemon, poll for completion or cancellation."""
        with self._call_lock:
            for retry in range(self._max_retries):
                try:
                    if not self._proc or self._proc.poll() is not None:
                        self._start_process(timeout=30.0)

                    self._send_frame({"method": method, "kwargs": kwargs})
                    start_time = time.time()

                    while True:
                        if cancel_poll and cancel_poll():
                            self._logger.info(
                                "Call cancelled by caller.",
                                extra={"log_event": "call_cancelled", "method": method},
                            )
                            try:
                                self._send_frame({"method": "cancel"})
                            except Exception:
                                pass
                            return {"error": "Task cancelled."}

                        if self._proc.poll() is not None:
                            raise RuntimeError("Daemon process terminated unexpectedly.")

                        elapsed = time.time() - start_time
                        if elapsed > timeout:
                            raise TimeoutError(f"Daemon call '{method}' timed out after {timeout}s.")

                        response = self._read_frame_with_timeout(timeout=0.1)
                        if response is not None:
                            self._logger.debug(
                                "Call completed.",
                                extra={
                                    "log_event": "call_completed",
                                    "method": method,
                                    "duration_ms": round((time.time() - start_time) * 1000, 2),
                                },
                            )
                            return response

                except Exception as exc:
                    self._logger.warning(
                        "PluginDaemon call failed (attempt %d/%d): %s",
                        retry + 1,
                        self._max_retries,
                        exc,
                        extra={"log_event": "call_failed", "method": method, "attempt": retry + 1},
                    )
                    self._terminate_process()
                    if retry == self._max_retries - 1:
                        return {"error": f"Daemon call failed after {self._max_retries} attempts: {exc}"}

            return {"error": "Daemon call failed."}

    def _terminate_process(self) -> None:
        """Terminate the worker subprocess safely."""
        if self._proc:
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None
        # Any partially-read frame belonged to this process's now-dead pipe.
        self._read_header_buf = bytearray()
        self._read_payload_buf = bytearray()
        self._read_payload_len = None

    def shutdown(self) -> None:
        """Shutdown the daemon gracefully."""
        with self._call_lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._send_frame({"method": "exit"})
                except Exception:
                    pass
            self._terminate_process()
            self._logger.info("PluginDaemon shut down.", extra={"log_event": "process_shutdown"})


class PluginUIDaemon(QObject):
    """Manages a long-lived, window-hosting worker process for a plugin's UI.

    Unlike `PluginDaemon`, which is strictly synchronous request/response (one
    in-flight call at a time), this daemon's worker owns its own `QApplication`
    and can emit events (tutorial hooks, `state_changed`, ...) at any time, not
    only as a reply to something the Hub asked for. Frames on the wire carry an
    explicit `kind` ("request" | "response" | "event") so a dedicated reader
    thread can demultiplex unsolicited events from in-flight call replies
    instead of assuming every inbound frame answers the most recent request —
    the assumption `PluginDaemon.call()` safely makes, but this daemon can't.
    That asymmetry only runs one way, though: the worker's own
    `RequestDispatcher` never handles anything but a `request` frame, so
    this class deliberately has no `send_event()` of its own — every call
    the Hub makes into a worker is `call()`, full stop. (There used to be
    one; it produced a frame the worker silently mishandled. See
    `ui_daemon_runtime.py`'s `handle_request` for what happens now if a
    stray `event` frame reaches a worker anyway.)

    Uses the same length-prefixed msgpack wire format as `PluginDaemon`
    (4-byte big-endian length header + msgpack payload) — only the payload
    schema differs, so both daemon flavors can share process-management
    conventions without a transport-level fork.
    """

    event_received = pyqtSignal(str, object)  # topic, payload
    process_exited = pyqtSignal()

    _instances: ClassVar[dict[str, PluginUIDaemon]] = {}
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()
    _core_services_port: ClassVar[int | None] = None
    _core_services_token: ClassVar[str | None] = None

    @classmethod
    def set_core_services(cls, port: int, token: str) -> None:
        """Record the Hub's CoreServicesServer port and bearer token for
        every worker this class spawns from here on.

        Called once, by the Hub, right after it starts its
        `CoreServicesServer` — see `karcytics.core.core_services_bootstrap`.
        Every worker subsequently started (by this or any other
        `PluginUIDaemon` instance) receives both via the
        `KARCYTICS_CORE_SERVICES_PORT`/`KARCYTICS_CORE_SERVICES_TOKEN`
        environment variables rather than them being threaded through every
        individual spawn call. The token must travel alongside the port:
        `CoreServicesServer` rejects every request without it (see
        `core_services.py`), so a worker that only learned the port would
        fail its very first call.
        """
        cls._core_services_port = port
        cls._core_services_token = token
        # Never log the token itself — it's a bearer credential.
        logger.info(
            "CoreServicesServer connection recorded.",
            extra={"log_event": "core_services_registered", "port": port},
        )

    def __init__(
        self,
        plugin_id: str,
        daemon_script_path: Path | str | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.plugin_id = plugin_id
        self.daemon_script_path = Path(daemon_script_path) if daemon_script_path else None
        self._logger = get_logger(__name__, plugin_id)
        # Set by a caller (e.g. karcytics/ui/windows/workspace/plugin_loader.py's
        # _instantiate_isolated_overlay, or __main__.py's isolated smoke test)
        # before the first ensure_started()/call(), to stage
        # KARCYTICS_PENDING_WORKFLOW=1 in _start_process's env — declared
        # here (rather than left as an attribute that only springs into
        # existence via external assignment) purely so every reader,
        # including a type checker, can see it's a real, always-present
        # attribute, not defer to _start_process's own `getattr(self,
        # "pending_workflow", False)` to learn that.
        self.pending_workflow: bool = False
        self.pending_academy_handoff: bool = False
        self._proc: subprocess.Popen | None = None
        self._start_lock = threading.Lock()
        self._next_request_id = itertools.count(1)
        self._pending: dict[int, queue.Queue[Any]] = {}
        self._pending_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()
        # Fed directly by the reader thread (not via event_received) so waiting
        # on it never depends on this object's own thread pumping the Qt event
        # loop — a signal emitted from the reader thread to a QObject that
        # lives on the (currently blocked-on-startup) calling thread resolves
        # to a queued connection, which only delivers once that thread's event
        # loop runs again. Blocking synchronously on such a signal here would
        # deadlock: the thing that would unblock it can't run until it unblocks.
        self._ready_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        # Captured by _stderr_reader_loop, not read directly off self._proc
        # .stderr — see _start_process's stderr_msg for why.
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._stderr_thread: threading.Thread | None = None

    @classmethod
    def get_instance(
        cls,
        plugin_id: str,
        daemon_script_path: Path | str | None = None,
    ) -> PluginUIDaemon:
        """Get or create the singleton PluginUIDaemon for a given plugin ID.

        `_instantiate_isolated_overlay` fetches the instance once up front
        (to stamp `pending_workflow`) before the panel factory that actually
        knows the real `daemon_script_path` has run — so a later caller
        that does supply one must not have it silently discarded just
        because the instance already exists; this backfills it onto an
        existing instance that doesn't have one yet, same as `PluginDaemon`.
        """
        with cls._registry_lock:
            if plugin_id not in cls._instances:
                cls._instances[plugin_id] = PluginUIDaemon(plugin_id, daemon_script_path)
            elif daemon_script_path is not None and cls._instances[plugin_id].daemon_script_path is None:
                cls._instances[plugin_id].daemon_script_path = Path(daemon_script_path)
            return cls._instances[plugin_id]

    @classmethod
    def get_running_instance(cls, plugin_id: str) -> PluginUIDaemon | None:
        """Return the already-registered instance for `plugin_id`, or `None`.

        Unlike `get_instance()`, never creates one — for callers (the Hub's
        event-dispatch fan-out, see `core_services_bootstrap.py`) that only
        want to reach a module already running, without registering a
        phantom entry for one that isn't.
        """
        with cls._registry_lock:
            daemon = cls._instances.get(plugin_id)
            if daemon is None:
                return None
            if daemon._proc is None or daemon._proc.poll() is None:
                return daemon
            return None

    @classmethod
    def start_instance(
        cls,
        plugin_id: str,
        daemon_script_path: Path | str | None = None,
        timeout: float = 30.0,
    ) -> PluginUIDaemon:
        """Start and ensure readiness of the UI daemon for a plugin ID."""
        daemon = cls.get_instance(plugin_id, daemon_script_path)
        daemon.ensure_started(timeout=timeout)
        return daemon

    @classmethod
    def stop_instance(cls, plugin_id: str) -> None:
        """Stop and unregister the UI daemon for a plugin ID."""
        with cls._registry_lock:
            daemon = cls._instances.pop(plugin_id, None)
            if daemon:
                daemon.shutdown()

    def _resolve_plugin_python_executable(self) -> Path:
        """Resolve python interpreter in plugin directory or fall back to sys.executable."""
        plugin_dir = Path.home() / ".karcytics" / "plugins" / self.plugin_id
        venv_dir = plugin_dir / ".venv"

        candidates = []
        if sys.platform == "win32":
            candidates.append(venv_dir / "Scripts" / "python.exe")
        else:
            major, minor = sys.version_info.major, sys.version_info.minor
            candidates.append(venv_dir / "bin" / f"python{major}.{minor}")
            candidates.append(venv_dir / "bin" / "python3")

        for c in candidates:
            if c.exists():
                return c

        return Path(sys.executable)

    def _resolve_daemon_script(self) -> Path:
        """Resolve ui_daemon.py path if not explicitly supplied.

        Checks both plugin layouts in use: the newer `src/karcytics_plugins/<id>/`
        tree (e.g. Flow Cytometry) and a plain `ui_daemon.py` at the plugin's
        own root (alongside `manifest.json`/`__init__.py`, e.g. Synthetic
        Biology's older manifest_version-2 layout) — a plugin migrating to
        `PluginUIDaemon` shouldn't need to restructure its whole package
        layout just to add this one file.
        """
        if self.daemon_script_path and self.daemon_script_path.exists():
            return self.daemon_script_path

        plugin_dir = Path.home() / ".karcytics" / "plugins" / self.plugin_id
        candidates = [
            plugin_dir / "src" / "karcytics_plugins" / self.plugin_id / "ui_daemon.py",
            plugin_dir / "ui_daemon.py",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        try:
            import importlib.util

            mod_name = f"karcytics_plugins.{self.plugin_id}.ui_daemon"
            spec = importlib.util.find_spec(mod_name)
            if spec and spec.origin:
                return Path(spec.origin)
        except Exception:
            pass

        raise FileNotFoundError(f"Could not locate UI daemon script for plugin '{self.plugin_id}'.")

    def ensure_started(self, timeout: float = 30.0) -> None:
        """Ensure the worker subprocess is running, ready, and being read from."""
        with self._start_lock:
            if self._proc and self._proc.poll() is None:
                return
            self._start_process(timeout=timeout)

    def _start_process(self, timeout: float = 30.0) -> None:
        """Start the worker subprocess, wait for its ready frame, and start reading."""
        self._terminate_process()
        self._stop_reader.clear()

        python_exe = self._resolve_plugin_python_executable()
        daemon_script = self._resolve_daemon_script()

        self._logger.info(
            "Starting PluginUIDaemon process using python=%s script=%s",
            python_exe,
            daemon_script,
            extra={"log_event": "process_start"},
        )

        sp_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            sp_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        extra_env = {
            "PYTHONUNBUFFERED": "1",
            "KARCYTICS_PLUGIN_ID": self.plugin_id,
        }
        if self.pending_workflow:
            extra_env["KARCYTICS_PENDING_WORKFLOW"] = "1"
        if getattr(self, "pending_academy_handoff", False):
            extra_env["KARCYTICS_ACADEMY_HANDOFF"] = "1"
        if self._core_services_port is not None:
            extra_env["KARCYTICS_CORE_SERVICES_PORT"] = str(self._core_services_port)
        if self._core_services_token is not None:
            extra_env["KARCYTICS_CORE_SERVICES_TOKEN"] = self._core_services_token
        env = _build_worker_env(extra_env)

        start_time = time.monotonic()
        self._proc = subprocess.Popen(
            [str(python_exe), str(daemon_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **sp_kwargs,
        )

        self._reader_thread = threading.Thread(
            target=self._reader_loop, name=f"ui-daemon-reader-{self.plugin_id}", daemon=True
        )
        self._reader_thread.start()

        self._stderr_tail.clear()
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader_loop, name=f"ui-daemon-stderr-{self.plugin_id}", daemon=True
        )
        self._stderr_thread.start()

        ready_frame = self._await_ready_frame(timeout=timeout)
        if ready_frame is None:
            stderr_msg = "\n".join(self._stderr_tail)
            self._terminate_process()
            error_msg = (
                f"PluginUIDaemon for '{self.plugin_id}' failed ready handshake. "
                f"Virtual Environment connection failed! Stderr: {stderr_msg}"
            )
            self._logger.critical(error_msg, extra={"log_event": "ready_handshake_failed"})
            raise RuntimeError(error_msg)

        self._logger.info(
            "PluginUIDaemon successfully started and ready.",
            extra={
                "log_event": "process_ready",
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            },
        )

    def _await_ready_frame(self, timeout: float) -> dict[str, Any] | None:
        """Block until the worker's startup "ready" frame arrives.

        Reads from `_ready_queue`, which the reader thread feeds directly —
        not through `event_received` — since this call blocks the very thread
        whose event loop a queued Qt signal delivery would need to run on.
        """
        try:
            return self._ready_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @staticmethod
    def _read_exact(stream: Any, num_bytes: int) -> bytes | None:
        """Blocking read of exactly num_bytes from stream, or None on EOF."""
        buf = bytearray()
        while len(buf) < num_bytes:
            chunk = stream.read(num_bytes - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _stderr_reader_loop(self) -> None:
        """Continuously drain the worker's stderr for as long as the process lives.

        `stderr=subprocess.PIPE` gives the OS pipe a fixed buffer (64KB on
        macOS); nothing upstream of this reads it during normal operation
        otherwise, since only the failure path in `_start_process` ever
        touched `self._proc.stderr` directly, and only once, after startup
        already failed. A window process that writes enough to stderr while
        running (verbose third-party logging, repeated warnings during a
        long render, ...) fills that buffer and then blocks on its own next
        `write()` call — including from its Qt main thread, which freezes
        the whole window (a native "beachball", not a normal busy spinner)
        with no obvious cause, since the actual computation may be
        long-since finished. Tucking each line into `_stderr_tail` instead
        of discarding it also means the failure path above still has
        something real to report.
        """
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if text:
                self._stderr_tail.append(text)
                self._logger.debug(text, extra={"log_event": "worker_stderr"})

    def _reader_loop(self) -> None:
        """Continuously drain the worker's stdout, demultiplexing response/event frames.

        Runs on its own thread rather than being polled from `call()` (as
        `PluginDaemon` does) because events must be dispatched the instant they
        arrive, independent of whether anything is currently blocked in `call()`.
        """
        proc = self._proc
        if not proc or not proc.stdout:
            return

        while not self._stop_reader.is_set():
            header = self._read_exact(proc.stdout, 4)
            if header is None:
                break
            length = struct.unpack(">I", header)[0]
            payload = self._read_exact(proc.stdout, length)
            if payload is None:
                break

            try:
                frame = msgpack.unpackb(payload, raw=False)
            except Exception:
                self._logger.warning(
                    "PluginUIDaemon received a malformed frame.", extra={"log_event": "malformed_frame"}
                )
                continue

            kind = frame.get("kind")
            if kind == "response":
                request_id = frame.get("request_id")
                with self._pending_lock:
                    q = self._pending.pop(request_id, None)
                if q is not None:
                    q.put(frame.get("payload"))
            elif kind == "event":
                topic = frame.get("topic", "")
                self._logger.debug(
                    "PluginUIDaemon received event.", extra={"log_event": "event_received", "topic": topic}
                )
                if topic == "ready":
                    self._ready_queue.put(frame.get("payload") or {})
                self.event_received.emit(topic, frame.get("payload"))
            else:
                self._logger.debug(
                    "PluginUIDaemon ignoring unknown frame kind.",
                    extra={"log_event": "unknown_frame_kind", "kind": kind},
                )

        self.process_exited.emit()

    def _send_frame(self, data: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Subprocess is not running.")
        payload = msgpack.packb(data, use_bin_type=True)
        header = struct.pack(">I", len(payload))
        self._proc.stdin.write(header + payload)
        self._proc.stdin.flush()

    def call(self, method: str, kwargs: dict[str, Any], timeout: float = 120.0) -> Any:
        """Send a request frame and block until its matching response arrives.

        Unlike `PluginDaemon.call()`, multiple calls may be in flight
        concurrently — each gets its own request_id and its own response
        queue, fed by the shared reader thread, so a slow call doesn't block a
        fast one behind it the way `PluginDaemon`'s single `_call_lock` does.
        """
        self.ensure_started()

        request_id = next(self._next_request_id)
        response_box: queue.Queue[Any] = queue.Queue()
        with self._pending_lock:
            self._pending[request_id] = response_box

        start_time = time.monotonic()
        try:
            self._send_frame({"kind": "request", "request_id": request_id, "method": method, "kwargs": kwargs})
            try:
                result = response_box.get(timeout=timeout)
                self._logger.debug(
                    "Call completed.",
                    extra={
                        "log_event": "call_completed",
                        "method": method,
                        "request_id": request_id,
                        "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                    },
                )
                return result
            except queue.Empty:
                self._logger.warning(
                    "Call timed out.",
                    extra={"log_event": "call_timed_out", "method": method, "request_id": request_id},
                )
                raise TimeoutError(f"UI daemon call '{method}' timed out after {timeout}s.") from None
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _terminate_process(self) -> None:
        self._stop_reader.set()
        if self._proc:
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None
        with self._pending_lock:
            for q in self._pending.values():
                q.put({"error": "Daemon process terminated."})
            self._pending.clear()

    def shutdown(self) -> None:
        """Shutdown the UI daemon gracefully.

        Acquires the same `_start_lock` `ensure_started()` holds for the
        entire duration of a spawn attempt. Without this, a `shutdown()`
        arriving while another thread is mid-`_start_process()` (e.g. still
        blocked in `_await_ready_frame()`) races that thread's use of
        `self._proc` — `_terminate_process()` here can null out or close
        the same subprocess/pipe the other thread is concurrently reading
        from, which segfaults rather than raising a catchable Python
        exception. Waiting for the lock instead bounds `shutdown()` to that
        in-flight attempt's own timeout in the rare case they overlap,
        which is what `ModuleStatusWidget.cancel()` accounts for by calling
        this off the GUI thread rather than assuming it returns instantly.
        """
        with self._start_lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._send_frame({"kind": "request", "request_id": -1, "method": "exit", "kwargs": {}})
                except Exception:
                    pass
            self._terminate_process()
        self._logger.info("PluginUIDaemon shut down.", extra={"log_event": "process_shutdown"})
