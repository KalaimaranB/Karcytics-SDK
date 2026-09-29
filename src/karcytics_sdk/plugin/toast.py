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

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

# Style presets: (icon, color). Free-form icon/color are always accepted too —
# these just save callers from picking a hex value for the common cases.
STYLE_INFO = ("ℹ️", "#5C9EE5")
STYLE_WARNING = ("⚠️", "#E5C07B")
STYLE_ERROR = ("⛔", "#E06C75")
STYLE_UPDATE = ("⬆️", "#61AFEF")
STYLE_SUCCESS = ("✅", "#98C379")


class ToastNotification(QWidget):
    """A single non-intrusive toast notification popup.

    When *parent* is supplied the toast is a **child widget** that lives in
    the parent's coordinate space — it moves and resizes with it
    automatically.  When *parent* is ``None`` it falls back to the old
    behaviour of being a frameless top-level window.
    """

    def __init__(
        self,
        message: str,
        parent: QWidget | None = None,
        duration_ms: int = 4000,
        icon: str = "ℹ️",
        color: str = "#5C9EE5",
    ):
        """Create a toast notification displaying the specified message.

        Parameters:
            message (str): Text to display in the notification.
            parent: Optional parent widget.  When given the toast is anchored
                to that widget's coordinate space rather than the screen.
            duration_ms (int): Time in milliseconds before the notification begins fading out.
            icon (str): Icon text or emoji to display.
            color (str): Border color of the toast.
        """
        super().__init__(parent)

        if parent is not None:
            # Child-widget path: no special window flags needed — the widget
            # inherits the parent's window and stays inside its geometry.
            # raise_() in show() keeps it painted above siblings.
            pass
        else:
            # Legacy top-level path (no anchor window available).
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

        # Opacity animation:
        # - Child widgets: setWindowOpacity is a no-op; use QGraphicsOpacityEffect.
        # - Top-level windows (legacy fallback): windowOpacity works fine.
        if parent is not None:
            self._opacity_effect = QGraphicsOpacityEffect(self)
            self._opacity_effect.setOpacity(0.0)
            self.setGraphicsEffect(self._opacity_effect)
            self.anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        else:
            self._opacity_effect = None
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

        # ID-selector stylesheets (not a bare property list) so these win
        # against an ancestor window's own unscoped `QLabel { color: ... }`
        # rule (e.g. WorkspaceWindow's theme-supplement QSS) — a type
        # selector on an ancestor otherwise beats a widget's own unscoped
        # `setStyleSheet()` call in Qt's cascade, which was leaking that
        # ancestor's label color/background onto these two labels instead
        # of the toast's own colors. `background: transparent` also stops
        # either label from painting its own opaque rect over the
        # container's rounded background.
        icon_label = QLabel(icon)
        icon_label.setObjectName("ToastIcon")
        icon_label.setStyleSheet("#ToastIcon { font-size: 16px; background: transparent; }")

        msg_label = QLabel(message)
        msg_label.setObjectName("ToastMessage")
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet(
            "#ToastMessage { color: #E0E0E0; font-size: 13px; font-weight: 500; background: transparent; }"
        )

        inner_layout.addWidget(icon_label)
        inner_layout.addWidget(msg_label, 1)

        layout.addWidget(self.container)

        # Adjust size based on content
        self.adjustSize()

    def fade_out(self):
        """Fade out the toast notification and close it when the animation finishes."""
        self.timer.stop()  # guard against a second timeout while fading
        self.anim.setDirection(QPropertyAnimation.Direction.Backward)
        self.anim.finished.connect(self.close)
        self.anim.start()


class _ResizeFilter(QObject):
    """Event filter installed on an anchor window to reposition live toasts
    whenever the window is resized.

    Keeps a weak reference to the :class:`ToastManager` that owns it so we
    can call back into the manager's repositioning logic without creating a
    reference cycle between the filter and the manager.
    """

    def __init__(self, manager: "ToastManager") -> None:
        super().__init__()
        self._manager = manager

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.Resize:
            self._manager._reposition_all()
        return False  # never consume the event


class ToastManager:
    r"""Stacks and positions toast notifications at a corner of the anchor
    window (or the active window / primary screen as a fallback) — see
    ``corner``.

    Unlike the Hub's own ``ToastManager`` singleton (which also wires itself
    to the ``SYSTEM_WARNING`` event), this class has no opinion on *why* a
    toast is being shown — it just tracks and positions whatever is passed to
    :meth:`show`. Construct your own instance to scope a stack (e.g. to one
    plugin window), or use the shared :data:`toast_manager` / :func:`show_toast`
    for the common case of \"just show this one toast\".

    Toasts are **child widgets** of their anchor window when one is given.
    This means they follow the window automatically when it is moved or
    resized, unlike the old approach of computing an absolute screen
    coordinate once at show-time and never updating it.
    """

    _MARGIN = 20
    _SPACING = 10

    def __init__(self, corner: str = "bottom-right") -> None:
        """Construct a manager stacking toasts from one corner of its anchor.

        Parameters:
            corner: Which corner of the anchor window to stack toasts from —
                ``"bottom-right"`` (default) or ``"bottom-left"``.
        """
        self._active_toasts: list[ToastNotification] = []
        self._resize_filter: _ResizeFilter | None = None
        self._anchor: QWidget | None = None
        self._corner = corner

    def _x_for(self, anchor: QWidget, toast: "ToastNotification", margin: int) -> int:
        if self._corner == "bottom-left":
            return margin
        return anchor.width() - toast.width() - margin

    def show(
        self,
        message: str,
        icon: str = "ℹ️",
        color: str = "#5C9EE5",
        duration_ms: int = 4000,
        window: QWidget | None = None,
    ) -> ToastNotification | None:
        """Display a toast, stacking it above any of this manager's other visible toasts.

        Parameters:
            message: Text to display.
            icon: Icon/emoji. See ``STYLE_*`` constants for presets.
            color: Hex border color. See ``STYLE_*`` constants for presets.
            duration_ms: Time visible before fading out.
            window: The window to anchor the toast to.  When provided the
                toast is created as a **child widget** of that window so it
                follows any move or resize automatically.  When ``None`` the
                active window (or primary screen) is used as a fallback, but
                the toast position is only computed once at show-time.

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

        # Resolve the anchor widget.
        anchor = window or app.activeWindow()

        # A QMainWindow's QMainWindowLayout automatically resizes the central
        # widget to fill the entire client area — so a child widget added
        # directly to QMainWindow sits *behind* the central widget and is
        # never visible.  Descend to the central widget instead so the toast
        # is a sibling of the plugin panel's own children and raise_() keeps
        # it on top.
        from PyQt6.QtWidgets import QMainWindow

        if isinstance(anchor, QMainWindow):
            cw = anchor.centralWidget()
            if cw is not None:
                anchor = cw

        # Clean up closed toasts
        self._active_toasts = [t for t in self._active_toasts if t.isVisible()]

        if anchor is not None:
            # ── Child-widget path ──────────────────────────────────────
            # Parent the toast to the anchor so it lives in the window's
            # coordinate space and moves/resizes with it automatically.
            toast = ToastNotification(message, parent=anchor, duration_ms=duration_ms, icon=icon, color=color)

            # Install / update the resize-event filter on the anchor so we
            # can restack live toasts whenever the window changes size.
            if self._anchor is not anchor:
                if self._anchor is not None and self._resize_filter is not None:
                    self._anchor.removeEventFilter(self._resize_filter)
                self._resize_filter = _ResizeFilter(self)
                anchor.installEventFilter(self._resize_filter)
                self._anchor = anchor

            self._active_toasts.append(toast)

            # Position the new toast at the bottom-right of the anchor before
            # showing it.  _reposition_all() can't do this: it filters by
            # isVisible(), and the toast isn't visible until after show().
            margin = self._MARGIN
            # Stack above any already-visible toasts.
            y_offset = sum(
                t.height() + self._SPACING
                for t in self._active_toasts[:-1]  # all but the new one
                if t.isVisible()
            )
            x = self._x_for(anchor, toast, margin)
            y = anchor.height() - margin - toast.height() - y_offset
            toast.move(QPoint(x, y))

            toast.show()
            toast.raise_()
        else:
            # ── Legacy fallback: no anchor window ──────────────────────
            # Compute a one-shot absolute screen position (original behaviour).
            screen_rect = app.primaryScreen().geometry()
            toast = ToastNotification(message, parent=None, duration_ms=duration_ms, icon=icon, color=color)
            self._active_toasts.append(toast)
            margin = self._MARGIN
            x = (
                screen_rect.x() + margin
                if self._corner == "bottom-left"
                else screen_rect.x() + screen_rect.width() - toast.width() - margin
            )
            y = screen_rect.y() + screen_rect.height() - margin
            for existing in self._active_toasts[:-1]:
                if existing.isVisible():
                    y -= existing.height() + self._SPACING
            toast.move(x, y - toast.height())
            toast.show()

        return toast

    def _reposition_all(self) -> None:
        """Recompute positions for all live toasts relative to the anchor window.

        Called on every resize event and whenever a new toast is added, so
        the stack always sits at the bottom-right of the current window rect.
        """
        anchor = self._anchor
        if anchor is None:
            return

        self._active_toasts = [t for t in self._active_toasts if t.isVisible()]

        margin = self._MARGIN
        # y starts from the bottom of the anchor and steps upward.
        y = anchor.height() - margin
        for toast in reversed(self._active_toasts):
            if not toast.isVisible():
                continue
            x = self._x_for(anchor, toast, margin)
            toast.move(QPoint(x, y - toast.height()))
            y -= toast.height() + self._SPACING


# Shared default manager + convenience function for the common "just show a toast" case.
toast_manager = ToastManager()


def show_toast(
    message: str,
    icon: str = "ℹ️",
    color: str = "#5C9EE5",
    duration_ms: int = 4000,
    window: QWidget | None = None,
) -> ToastNotification | None:
    """Show a toast notification using the shared default :class:`ToastManager`.

    Parameters:
        message (str): Text to display.
        icon (str): Icon/emoji to display. See ``STYLE_*`` constants for presets.
        color (str): Hex border color. See ``STYLE_*`` constants for presets.
        duration_ms (int): Time visible before it begins fading out.
        window (QWidget | None): Window to anchor the toast to.  When given
            the toast follows the window on move/resize.  Defaults to the
            currently active window.
    """
    return toast_manager.show(message, icon=icon, color=color, duration_ms=duration_ms, window=window)
