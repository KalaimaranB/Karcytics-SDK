import threading

from karcytics_sdk.plugin import Debouncer


def test_a_burst_of_triggers_runs_the_callback_once(qtbot):
    calls = []
    debouncer = Debouncer(lambda: calls.append(1))
    for _ in range(3):
        debouncer.trigger(20)

    qtbot.waitUntil(lambda: calls == [1], timeout=2000)
    qtbot.wait(60)
    assert calls == [1]


def test_cancel_drops_a_pending_run(qtbot):
    calls = []
    debouncer = Debouncer(lambda: calls.append(1))
    debouncer.trigger(20)
    debouncer.cancel()

    qtbot.wait(80)
    assert calls == []
    assert not debouncer.is_pending()


def test_trigger_from_a_plain_thread_runs_the_callback_on_the_gui_thread(qtbot, qapp):
    """A `threading.Timer` debounce ran its work on its own short-lived thread;
    under PySide6 a task submitted from there never reported finishing.
    """
    from PySide6.QtCore import QThread

    threads = []
    debouncer = Debouncer(lambda: threads.append(QThread.currentThread()))
    trigger = threading.Thread(target=lambda: debouncer.trigger(0))
    trigger.start()
    trigger.join()

    qtbot.waitUntil(lambda: bool(threads), timeout=2000)
    assert threads[0] is qapp.thread()
