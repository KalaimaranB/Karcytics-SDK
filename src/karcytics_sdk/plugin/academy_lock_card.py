"""The locked state of an Academy workflow: a dimmed panel and one card.

While a course in an Academy workflow is paused (or one course has finished
and the next hasn't started), the workflow must not be edited — the next
step expects the workspace exactly as the last one left it. `AcademyLockCard`
covers a plugin's panel, disables every widget in it and the window's menu
bar, and shows a centred card with Cyto waiting, what comes next, how far
the learner got, and two choices (e.g. Resume / Leave course). The plugin
decides what the choices do; this widget only locks, unlocks and reports
the click.

Disabling the widgets, rather than only covering them, is what keeps the
keyboard out too: a covered but enabled widget still takes Tab focus and
shortcuts.
"""

from __future__ import annotations

from dataclasses import dataclass

import shiboken6
from PySide6.QtCore import QChildEvent, QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .cyto_character import CytoWidget
from .theme_fallback import Colors, Fonts, theme_manager


@dataclass(frozen=True)
class LockCardContent:
    """What the lock card says. A progress bar shows when `step_total` is set."""

    eyebrow: str
    title: str
    message: str
    primary_text: str
    secondary_text: str
    step_number: int = 0
    step_total: int = 0


class AcademyLockCard(QWidget):
    """Locks `panel` behind a centred Academy card until `unlock()`.

    `primary_clicked` / `secondary_clicked` fire for the card's two buttons;
    the card stays up until the caller unlocks it.
    """

    primary_clicked = Signal()
    secondary_clicked = Signal()

    def __init__(self, panel: QWidget, show_character: bool = True) -> None:
        super().__init__(panel)
        self.setObjectName("AcademyLockCard")
        self._panel = panel
        self._show_character = show_character
        # Widgets (and the menu bar) this card disabled, to re-enable on
        # unlock — only those that were enabled, so a widget the panel had
        # disabled itself stays that way.
        self._disabled: list[QWidget] = []
        self._locked = False
        self._build()
        panel.installEventFilter(self)
        self.hide()

    # ── Public API ─────────────────────────────────────────────────────

    @property
    def is_locked(self) -> bool:
        return self._locked

    def show_lock(self, content: LockCardContent) -> None:
        """Locks the panel (if not already) and shows the card with `content`."""
        self.eyebrow_label.setText(content.eyebrow)
        self.title_label.setText(content.title)
        self.message_label.setText(content.message)
        self.primary_button.setText(content.primary_text)
        self.secondary_button.setText(content.secondary_text)
        has_progress = content.step_total > 0
        self.progress_bar.setVisible(has_progress)
        self.progress_label.setVisible(has_progress)
        if has_progress:
            self.progress_bar.setMaximum(content.step_total)
            self.progress_bar.setValue(min(content.step_number, content.step_total))
            self.progress_label.setText(f"Step {content.step_number} of {content.step_total}")
        self.cyto.setVisible(self._show_character)

        if not self._locked:
            self._locked = True
            self._disable_everything_else()
        self.setGeometry(self._panel.rect())
        self.show()
        self.raise_()
        self.primary_button.setFocus()

    def unlock(self) -> None:
        """Hides the card and re-enables whatever it disabled."""
        if not self._locked:
            return
        self._locked = False
        for widget in self._disabled:
            if shiboken6.isValid(widget):
                widget.setEnabled(True)
        self._disabled = []
        self.hide()

    def set_show_character(self, show: bool) -> None:
        self._show_character = show
        self.cyto.setVisible(show)

    # ── Qt ─────────────────────────────────────────────────────────────

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._panel and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._panel.rect())
        elif watched is self._panel and isinstance(event, QChildEvent) and event.added() and self._locked:
            # Something added to the panel while locked (a lazily built
            # tab, a re-rendered plot) must not slip in enabled — or on top.
            child = event.child()
            if isinstance(child, QWidget) and child is not self and child.isEnabled():
                child.setEnabled(False)
                self._disabled.append(child)
            self.raise_()
        return False

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(8, 10, 14, 190))

    # ── Internals ──────────────────────────────────────────────────────

    def _disable_everything_else(self) -> None:
        for child in self._panel.children():
            if isinstance(child, QWidget) and child is not self and child.isEnabled():
                child.setEnabled(False)
                self._disabled.append(child)
        window = self._panel.window()
        menu_bar = window.menuBar() if isinstance(window, QMainWindow) else None
        if menu_bar is not None and menu_bar.isEnabled():
            menu_bar.setEnabled(False)
            self._disabled.append(menu_bar)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QFrame()
        self.card.setObjectName("AcademyLockCardFrame")
        self.card.setMaximumWidth(780)
        row = QHBoxLayout(self.card)
        row.setContentsMargins(24, 24, 36, 24)
        row.setSpacing(12)

        self.cyto = CytoWidget(self.card)
        self.cyto.set_emotion("idle")
        row.addWidget(self.cyto, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(10)
        text.addStretch()
        self.eyebrow_label = QLabel()
        self.eyebrow_label.setObjectName("AcademyLockEyebrow")
        self.title_label = QLabel()
        self.title_label.setObjectName("AcademyLockTitle")
        self.title_label.setWordWrap(True)
        self.message_label = QLabel()
        self.message_label.setObjectName("AcademyLockMessage")
        self.message_label.setWordWrap(True)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("AcademyLockProgressLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("AcademyLockProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        for widget in (
            self.eyebrow_label,
            self.title_label,
            self.message_label,
            self.progress_label,
            self.progress_bar,
        ):
            text.addWidget(widget)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        self.primary_button = QPushButton()
        self.primary_button.setObjectName("AcademyLockPrimaryButton")
        self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_button.clicked.connect(self.primary_clicked)
        self.secondary_button = QPushButton()
        self.secondary_button.setObjectName("AcademyLockSecondaryButton")
        self.secondary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.secondary_button.setFlat(True)
        self.secondary_button.clicked.connect(self.secondary_clicked)
        buttons.addWidget(self.primary_button)
        buttons.addWidget(self.secondary_button)
        buttons.addStretch()
        text.addSpacing(8)
        text.addLayout(buttons)
        text.addStretch()
        row.addLayout(text, stretch=1)

        outer.addWidget(self.card)
        self._apply_styles()
        theme_manager.theme_changed.connect(self._apply_styles)

    def _apply_styles(self) -> None:
        theme_manager.apply_style(
            self.card,
            f"QFrame#AcademyLockCardFrame {{ background: {Colors.BG_DARK};"
            f" border: 1px solid {Colors.BORDER_FOCUS}; border-radius: 14px; }}",
        )
        theme_manager.apply_style(
            self.eyebrow_label,
            f"color: {Colors.ACCENT_PRIMARY}; font-size: {Fonts.SIZE_SMALL}px;"
            " font-weight: bold; letter-spacing: 1px; background: transparent;",
        )
        theme_manager.apply_style(
            self.title_label,
            f"color: {Colors.FG_PRIMARY}; font-size: {Fonts.SIZE_LARGE + 4}px;"
            " font-weight: bold; background: transparent;",
        )
        for label in (self.message_label, self.progress_label):
            theme_manager.apply_style(
                label,
                f"color: {Colors.FG_SECONDARY}; font-size: {Fonts.SIZE_NORMAL}px; background: transparent;",
            )
        theme_manager.apply_style(
            self.progress_bar,
            f"QProgressBar {{ background: {Colors.BG_MEDIUM}; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {Colors.ACCENT_PRIMARY}; border-radius: 4px; }}",
        )
        theme_manager.apply_style(
            self.primary_button,
            f"QPushButton {{ background: {Colors.ACCENT_SUCCESS}; color: white; border: none;"
            f" border-radius: 6px; padding: 10px 24px; font-size: {Fonts.SIZE_NORMAL}px;"
            " font-weight: bold; }}",
        )
        theme_manager.apply_style(
            self.secondary_button,
            f"QPushButton {{ background: transparent; color: {Colors.FG_SECONDARY}; border: none;"
            f" padding: 10px 8px; font-size: {Fonts.SIZE_NORMAL}px; text-decoration: underline; }}"
            f"QPushButton:hover {{ color: {Colors.FG_PRIMARY}; }}",
        )
