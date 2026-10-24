"""A one-line rail of stages: Load › Lanes › Bands › … with each stage's status.

Every stage can be opened at any time (click, or Tab and Space). A stage
can carry a status (to do, working, done, needs attention, blocked), shown
as a glyph and a colour and spelled out in its tooltip together with any
messages, so the status never depends on colour alone. A host that has no
statuses to show (a plain stepper) just never sets one.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

from .components import _connect_theme_signal, _get_theme_tokens


class StageStatus(Enum):
    """How far a stage has got, and whether it needs the user."""

    NONE = ("", "")
    TODO = ("○", "Not done yet")
    BUSY = ("…", "Working")
    OK = ("✓", "Done")
    WARNING = ("!", "Needs attention")
    ERROR = ("✕", "Blocked")

    @property
    def glyph(self) -> str:
        return self.value[0]

    @property
    def meaning(self) -> str:
        return self.value[1]


_STATUS_COLOR = {
    StageStatus.NONE: "FG_SECONDARY",
    StageStatus.TODO: "FG_SECONDARY",
    StageStatus.BUSY: "ACCENT_PRIMARY",
    StageStatus.OK: "ACCENT_SUCCESS",
    StageStatus.WARNING: "ACCENT_WARNING",
    StageStatus.ERROR: "ACCENT_DANGER",
}


class StageRail(QWidget):
    """Clickable stages in a row, one current, each with an optional status."""

    stage_clicked = Signal(int)

    def __init__(self, labels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels = list(labels)
        self._statuses = [StageStatus.NONE] * len(labels)
        self._messages: list[list[str]] = [[] for _ in labels]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        self._arrows: list[QLabel] = []
        for i, label in enumerate(labels):
            if i:
                arrow = QLabel("›")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(arrow)
                self._arrows.append(arrow)
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            self._group.addButton(button, i)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()
        self._group.idClicked.connect(self.stage_clicked)
        for i in range(len(labels)):
            self._refresh(i)
        _connect_theme_signal(self.apply_theme)

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def current(self) -> int:
        return self._group.checkedId()

    def set_status(self, index: int, status: StageStatus, messages: list[str] | None = None) -> None:
        """Show `status` on stage `index`, with `messages` (one per line) in its tooltip."""
        if not 0 <= index < len(self.buttons):
            return
        self._statuses[index] = status
        self._messages[index] = list(messages or [])
        self._refresh(index)
        self.apply_theme()

    def status(self, index: int) -> StageStatus:
        return self._statuses[index]

    def apply_theme(self) -> None:
        colors, _, _ = _get_theme_tokens()
        rules = [
            f"""
            QPushButton {{
                background: transparent; color: {colors.FG_SECONDARY};
                border: 1px solid transparent; border-radius: 10px;
                padding: 3px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ color: {colors.FG_PRIMARY}; border-color: {colors.BORDER}; }}
            QPushButton:focus {{ border-color: {colors.BORDER_FOCUS}; }}
            QPushButton:checked {{
                background: {colors.ACCENT_PRIMARY}; color: {colors.BG_DARKEST}; font-weight: bold;
            }}
            QLabel {{ color: {colors.FG_SECONDARY}; }}
            """
        ]
        for i, button in enumerate(self.buttons):
            status = self._statuses[i]
            if status in (StageStatus.WARNING, StageStatus.ERROR):
                color = getattr(colors, _STATUS_COLOR[status])
                button.setObjectName(f"stage{i}")
                rules.append(f"QPushButton#stage{i}:!checked {{ color: {color}; border-color: {color}; }}")
        self.setStyleSheet("\n".join(rules))

    def _refresh(self, index: int) -> None:
        status = self._statuses[index]
        label = self._labels[index]
        button = self.buttons[index]
        button.setText(f"{status.glyph} {label}" if status.glyph else label)
        lines = [f"Step {index + 1}: {label}"]
        if status.meaning:
            lines[0] += f" ({status.meaning.lower()})"
        lines += [f"• {m}" for m in self._messages[index]]
        button.setToolTip("\n".join(lines))
        button.setAccessibleDescription(status.meaning)
