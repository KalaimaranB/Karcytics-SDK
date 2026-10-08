import warnings

from PySide6.QtCore import QObject, Signal

from karcytics_sdk.plugin import safe_disconnect


class _Emitter(QObject):
    fired = Signal(int)


def test_safe_disconnect_removes_a_connected_slot():
    emitter, received = _Emitter(), []
    emitter.fired.connect(received.append)

    assert safe_disconnect(emitter.fired, received.append) is True
    emitter.fired.emit(1)
    assert received == []


def test_safe_disconnect_is_silent_when_nothing_is_connected():
    emitter = _Emitter()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert safe_disconnect(emitter.fired, print) is False
        assert safe_disconnect(emitter.fired) is False


def test_disconnect_then_connect_keeps_exactly_one_connection():
    emitter, received = _Emitter(), []
    for _ in range(3):
        safe_disconnect(emitter.fired, received.append)
        emitter.fired.connect(received.append)
    emitter.fired.emit(7)
    assert received == [7]


def test_safe_disconnect_without_a_slot_removes_every_connection():
    emitter, first, second = _Emitter(), [], []
    emitter.fired.connect(first.append)
    emitter.fired.connect(second.append)

    assert safe_disconnect(emitter.fired) is True
    emitter.fired.emit(1)
    assert (first, second) == ([], [])
