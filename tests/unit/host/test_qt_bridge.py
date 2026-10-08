"""Unit tests for QtThreadBridge — lets a CoreServicesServer handler thread
safely call into Qt-owned state and get a result back.
"""

import threading

import pytest

from karcytics_sdk.host.qt_bridge import QtThreadBridge


def test_run_from_the_owning_thread_returns_the_result(qapp):  # noqa: ARG001
    bridge = QtThreadBridge()

    result = bridge.run(lambda: 1 + 1)

    assert result == 2


def test_run_from_a_background_thread_returns_the_result(qapp):  # noqa: ARG001
    """This is the real usage: CoreServicesServer's HTTP handler thread
    calling in, not the GUI thread calling itself.
    """
    bridge = QtThreadBridge()
    outcome: dict = {}

    def _worker():
        try:
            outcome["result"] = bridge.run(lambda: "computed on the gui thread")
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=_worker)
    thread.start()

    # The GUI thread must pump its event loop for the queued call to run.
    deadline_iterations = 200
    from PySide6.QtWidgets import QApplication

    for _ in range(deadline_iterations):
        if "result" in outcome or "error" in outcome:
            break
        QApplication.processEvents()
        thread.join(timeout=0.02)

    thread.join(timeout=2.0)

    assert outcome.get("result") == "computed on the gui thread"


def test_run_reraises_the_callables_own_exception(qapp):  # noqa: ARG001
    bridge = QtThreadBridge()

    def _boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        bridge.run(_boom)
