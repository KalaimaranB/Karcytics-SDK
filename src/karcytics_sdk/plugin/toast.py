"""Toast notifications for Karcytics SDK.

A non-intrusive popup that appears at the bottom-right of the active window
(or screen, if there is none) and fades out on its own. Originally built into
the Hub for system warnings; lives here so any plugin — in-process or
isolated, each of which owns its own ``QApplication`` — can raise the exact
same UI for its own purposes (an update notice, a completed background job, a
non-fatal heads-up) without depending on the Hub's event bus at all. The Hub
itself now builds its warning toasts on top of this module too (see
``karcytics.ui.components.toast_manager``) rather than keeping a second copy.

Call :func:`show_toast` directly for the common case; reach for
:class:`ToastManager` if you want to manage your own stack of toasts (e.g. to
scope it to a particular parent window) instead of sharing the module-level
default.
"""

import logging
from typing import cast

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

# Style presets: (icon, color). Free-form icon/color are always accepted too —
# these just save callers from picking a hex value for the common cases.
STYLE_INFO = ("ℹ️", "#5C9EE5")
STYLE_WARNING = ("⚠️", "#E5C07B")
STYLE_ERROR = ("⛔", "#E06C75")
STYLE_UPDATE = ("⬆️", "#61AFEF")
STYLE_SUCCESS = ("✅", "#98C379")


class ToastNotification(QWidget):
    """A single non-intrusive toast notification popup."""

    def __init__(self, message: str, parent=None, duration_ms: int = 4000, icon: str = "ℹ️", color: str = "#5C9EE5"):
        """Create a toast notification displaying the specified message.

        Parameters:
            message (str): Text to display in the notification.
            parent: Optional parent widget.
            duration_ms (int): Time in milliseconds before the notification begins fading out.
            icon (str): Icon text or emoji to display.
            color (str): Border color of the toast.
        """
        super().__init__(parent)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.ToolTip | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setup_ui(message, icon, color)

        # Setup fade out timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fade_out)
        self.timer.start(duration_ms)

        # Initial fade in
        self.setWindowOpacity(0.0)
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(250)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.anim.start()

    def setup_ui(self, message: str, icon: str, color: str):
        """Build the toast layout and display the message with an icon.

        Parameters:
            message (str): Text to display.
            icon (str): Icon to display.
            color (str): Hex color for the border.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.container = QWidget(self)
        self.container.setObjectName("ToastContainer")
        self.container.setStyleSheet(f"""
            #ToastContainer {{
                background-color: #2D2D2D;
                border: 1px solid {color}; /* Toast accent border */
                border-radius: 8px;
            }}
        """)

        inner_layout = QHBoxLayout(self.container)
        inner_layout.setContentsMargins(16, 12, 16, 12)
        inner_layout.setSpacing(12)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 16px;")

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("color: #E0E0E0; font-size: 13px; font-weight: 500;")

        inner_layout.addWidget(icon_label)
        inner_layout.addWidget(msg_label, 1)

        layout.addWidget(self.container)

        # Adjust size based on content
        self.adjustSize()

    def fade_out(self):
        """Fade out the toast notification and close it when the animation finishes."""
        self.anim.setDirection(QPropertyAnimation.Direction.Backward)
        self.anim.start()
        self.anim.finished.connect(self.close)


class ToastManager:
    """Stacks and positions toast notifications at the bottom-right of the active window.

    Unlike the Hub's own ``ToastManager`` singleton (which also wires itself
    to the ``SYSTEM_WARNING`` event), this class has no opinion on *why* a
    toast is being shown — it just tracks and positions whatever is passed to
    :meth:`show`. Construct your own instance to scope a stack (e.g. to one
    plugin window), or use the shared :data:`toast_manager` / :func:`show_toast`
    for the common case of "just show this one toast".
    """

    def __init__(self) -> None:
        self._active_toasts: list[ToastNotification] = []

    def show(
        self,
        message: str,
        icon: str = "ℹ️",
        color: str = "#5C9EE5",
        duration_ms: int = 4000,
    ) -> ToastNotification | None:
        """Display a toast, stacking it above any of this manager's other visible toasts.

        Returns:
            ToastNotification | None: The toast widget, or ``None`` if no
            ``QApplication`` exists yet (nothing to anchor the popup to).
        """
        app = QApplication.instance()
        if not app:
            logger.debug("show_toast called before a QApplication exists; skipping.")
            return None
        # QApplication.instance() is typed as returning the QCoreApplication
        # base (it overrides an inherited classmethod without narrowing the
        # stub's return type) — but calling it via QApplication itself, as
        # here, always hands back the QApplication singleton, so the
        # QApplication-only members below (activeWindow/primaryScreen) are
        # safe to use.
        app = cast(QApplication, app)

        # Clean up closed toasts
        self._active_toasts = [t for t in self._active_toasts if t.isVisible()]

        toast = ToastNotification(message, duration_ms=duration_ms, icon=icon, color=color)
        self._active_toasts.append(toast)

        # Position at the bottom right of the active window or screen
        active_window = app.activeWindow()
        if active_window:  # noqa: SIM108
            parent_rect = active_window.geometry()
        else:
            parent_rect = app.primaryScreen().geometry()

        # Calculate base position (bottom right with margin)
        margin = 20
        x = parent_rect.x() + parent_rect.width() - toast.width() - margin
        y = parent_rect.y() + parent_rect.height() - margin

        # Stack vertically if there are multiple active toasts
        for existing in self._active_toasts[:-1]:
            if existing.isVisible():
                y -= existing.height() + 10

        toast.move(x, y - toast.height())
        toast.show()
        return toast


# Shared default manager + convenience function for the common "just show a toast" case.
toast_manager = ToastManager()


def show_toast(
    message: str,
    icon: str = "ℹ️",
    color: str = "#5C9EE5",
    duration_ms: int = 4000,
) -> ToastNotification | None:
    """Show a toast notification using the shared default :class:`ToastManager`.

    Parameters:
        message (str): Text to display.
        icon (str): Icon/emoji to display. See ``STYLE_*`` constants for presets.
        color (str): Hex border color. See ``STYLE_*`` constants for presets.
        duration_ms (int): Time visible before it begins fading out.
    """
    return toast_manager.show(message, icon=icon, color=color, duration_ms=duration_ms)
