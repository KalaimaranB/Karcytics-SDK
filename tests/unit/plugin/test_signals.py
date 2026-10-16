import threading
import warnings

from PySide6.QtCore import QObject, Signal

from karcytics_sdk.plugin import move_to_gui_thread, safe_disconnect
from karcytics_sdk.plugin.events import _EventBusProxy


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


def _build_on_a_plain_thread(factory):
    built = []
    thread = threading.Thread(target=lambda: built.append(factory()))
    thread.start()
    thread.join()
    return built[0]


def test_move_to_gui_thread_hands_a_plain_thread_object_to_the_gui_thread(qapp):
    """An object built on a plain thread belongs to that thread, and PySide6
    delivers lambda slots through it — dropped once that thread has ended.
    """

    def build():
        emitter = _Emitter()
        move_to_gui_thread(emitter)
        return emitter

    assert _build_on_a_plain_thread(build).thread() is qapp.thread()


def test_move_to_gui_thread_leaves_a_gui_thread_object_alone(qapp):
    emitter = _Emitter()
    move_to_gui_thread(emitter)
    assert emitter.thread() is qapp.thread()


def test_event_bus_first_touched_off_the_gui_thread_still_delivers(qtbot):
    proxy, received = _EventBusProxy(), []
    _build_on_a_plain_thread(lambda: proxy.subscribe("topic", received.append))

    proxy.publish("topic", 3)

    qtbot.waitUntil(lambda: received == [3], timeout=5000)
