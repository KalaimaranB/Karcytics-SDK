"""Unit tests for `_RequestReader` — the worker-side stdin reader thread.

Covers the two failure modes a Hub<->worker frame stream can hit that
`run()`'s end-to-end tests don't exercise directly: a single corrupt frame
(must not kill the reader thread) and the Hub's stdin pipe closing (must
exit the process, not hang forever).
"""

from unittest.mock import MagicMock, patch

import pytest

from karcytics_sdk.plugin.ui_daemon_runtime import _hard_exit, _RequestBridge, _RequestReader


class _ExitCalled(Exception):
    """Stands in for the real process termination `_hard_exit()` performs, so
    the loop under test actually stops instead of looping forever once
    mocked `_hard_exit` returns normally.
    """


def test_malformed_frame_is_logged_and_does_not_stop_the_reader():
    good_frame = {"kind": "request", "request_id": 1, "method": "focus", "kwargs": {}}
    responses = iter([RuntimeError("bad msgpack payload"), good_frame, None])

    def _fake_read_frame():
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    bridge = _RequestBridge()
    received = []
    bridge.request_received.connect(received.append)

    logger = MagicMock()

    with (
        patch("karcytics_sdk.plugin.ui_daemon_runtime.read_frame", side_effect=_fake_read_frame),
        patch("karcytics_sdk.plugin.ui_daemon_runtime._hard_exit", side_effect=_ExitCalled) as mock_exit,
        pytest.raises(_ExitCalled),
    ):
        reader = _RequestReader(bridge, logger)
        reader._loop()

    logger.warning.assert_called_once()
    assert logger.warning.call_args.kwargs["extra"]["log_event"] == "malformed_frame"
    assert received == [good_frame]
    mock_exit.assert_called_once_with(0)


def test_stdin_eof_logs_and_exits_process():
    logger = MagicMock()
    bridge = _RequestBridge()

    with (
        patch("karcytics_sdk.plugin.ui_daemon_runtime.read_frame", return_value=None),
        patch("karcytics_sdk.plugin.ui_daemon_runtime._hard_exit", side_effect=_ExitCalled) as mock_exit,
        pytest.raises(_ExitCalled),
    ):
        reader = _RequestReader(bridge, logger)
        reader._loop()

    mock_exit.assert_called_once_with(0)
    logger.info.assert_called_once()
    assert logger.info.call_args.kwargs["extra"]["log_event"] == "stdin_closed"


def test_hard_exit_flushes_and_calls_os_exit_off_windows():
    with (
        patch("karcytics_sdk.plugin.ui_daemon_runtime.sys.platform", "darwin"),
        patch("karcytics_sdk.plugin.ui_daemon_runtime.sys.stdout") as stdout,
        patch("karcytics_sdk.plugin.ui_daemon_runtime.os._exit", side_effect=_ExitCalled) as mock_exit,
        pytest.raises(_ExitCalled),
    ):
        _hard_exit(3)

    stdout.flush.assert_called_once()
    mock_exit.assert_called_once_with(3)


def test_hard_exit_terminates_the_process_on_windows():
    """ExitProcess (os._exit) would run Qt's DLL detach code and crash."""
    kernel32 = MagicMock()
    kernel32.GetCurrentProcess.return_value = "this-process"
    kernel32.TerminateProcess.side_effect = _ExitCalled
    windll = MagicMock(kernel32=kernel32)

    with (
        patch("karcytics_sdk.plugin.ui_daemon_runtime.sys.platform", "win32"),
        patch("ctypes.windll", windll, create=True),
        patch("karcytics_sdk.plugin.ui_daemon_runtime.os._exit") as mock_exit,
        pytest.raises(_ExitCalled),
    ):
        _hard_exit(5)

    kernel32.TerminateProcess.assert_called_once_with("this-process", 5)
    mock_exit.assert_not_called()
