"""Central EventBus for Karcytics SDK.

Provides a singleton event bus using Qt signals to allow asynchronous
and decoupled communication between different plugins and the core application.
"""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from .signals import move_to_gui_thread


class _EventBus(QObject):
    """The actual implementation of the EventBus."""

    # Generic signal used to bounce events through the Qt event loop
    _event_signal = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._subscribers: dict[str, list[Callable[[Any], None]]] = {}
        self._event_signal.connect(self._handle_event)

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """Subscribe to an event topic.

        Args:
            topic: The event topic string (e.g., "flow_data_updated")
            callback: Function to call when the event is published. Must accept one argument (the payload).
        """
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        if callback not in self._subscribers[topic]:
            self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """Remove a subscription."""
        if topic in self._subscribers and callback in self._subscribers[topic]:
            self._subscribers[topic].remove(callback)

    def publish(self, topic: str, data: Any = None) -> None:
        """Publish an event asynchronously.

        Args:
            topic: The event topic string.
            data: Arbitrary data payload to send to subscribers.
        """
        # Emitting this signal queues the event in the Qt Event Loop, making it async.
        self._event_signal.emit(topic, data)

    def _handle_event(self, topic: str, data: Any) -> None:
        """Internal slot that actually dispatches to callbacks."""
        if topic in self._subscribers:
            for callback in self._subscribers[topic]:
                try:
                    callback(data)
                except Exception as e:
                    import logging

                    logging.getLogger(__name__).exception(f"Error in event subscriber for {topic}: {e}")


class _EventBusProxy:
    """Lazy proxy to ensure QObject is not instantiated before QApplication."""

    def __init__(self) -> None:
        self._bus = None

    def _get_bus(self):
        if self._bus is None:
            # The first touch can come from a background thread (plugin code
            # loaded off the GUI thread); a bus owned by that thread would
            # drop every event once it ends.
            self._bus = _EventBus()
            move_to_gui_thread(self._bus)
        return self._bus

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        self._get_bus().subscribe(topic, callback)

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        self._get_bus().unsubscribe(topic, callback)

    def publish(self, topic: str, data: Any = None) -> None:
        self._get_bus().publish(topic, data)


# Singleton instance
CentralEventBus = _EventBusProxy()
