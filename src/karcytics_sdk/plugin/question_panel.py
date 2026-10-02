"""The answer area of a `QuestionStep`, shown inside Cyto's bubble.

Kept out of `tutorial_overlay.py` (already the overlay's layout, masking and
placement engine): this is only the choices, the feedback line, and the
check → retry → reveal logic. The overlay supplies `check`, which records the
answer with the `AcademyManager` and says whether it was correct, and turns
the footer's Check button into Continue when `answered` fires.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .theme_fallback import theme_manager
from .tutorial_models import QUESTION_PREDICT, QuestionStep

_ROW_QSS = (
    "QFrame#QuestionChoice {"
    "  background-color: {BG_MEDIUM}; border: 1px solid {BORDER}; border-radius: 6px;"
    "}"
    'QFrame#QuestionChoice[state="selected"] { border: 1px solid {ACCENT_PRIMARY}; }'
    'QFrame#QuestionChoice[state="wrong"] { border: 1px solid {ACCENT_DANGER}; }'
    'QFrame#QuestionChoice[state="reveal"] { border: 2px dashed {ACCENT_SUCCESS}; }'
    'QFrame#QuestionChoice[state="correct"] { border: 2px solid {ACCENT_SUCCESS}; }'
    "QFrame#QuestionChoice QLabel { color: {FG_PRIMARY}; background: transparent; border: none; }"
)
_FEEDBACK_QSS = (
    "QLabel#QuestionFeedback { padding: 6px 2px; color: {FG_PRIMARY}; }"
    'QLabel#QuestionFeedback[state="wrong"] { color: {ACCENT_DANGER}; }'
    'QLabel#QuestionFeedback[state="correct"] { color: {ACCENT_SUCCESS}; }'
)
_HINT = "Choose an answer first."
_REVEAL_NOTE = "The outlined answer is correct — select it to continue."
_MULTI_MISSING = "Not quite — at least one correct answer is still unselected."


def _claim_wrapped_height(label: QLabel, width: int) -> None:
    """Makes a word-wrapped label ask for all its lines at `width`.

    A wrapped QLabel's sizeHint() assumes its own preferred width, not the
    fixed one it's given here, and the bubble is sized from sizeHint() — so
    the last line or two (a long explanation) would be clipped.
    """
    label.setMinimumHeight(label.heightForWidth(width))


class _ChoiceRow(QFrame):
    """One answer: an indicator plus a word-wrapped label; the whole row clicks."""

    def __init__(self, text: str, indicator: QAbstractButton, width: int) -> None:
        super().__init__()
        self.setObjectName("QuestionChoice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.indicator = indicator
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        row.addWidget(indicator, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        self.setFixedWidth(width)
        theme_manager.apply_style(self, _ROW_QSS)
        _claim_wrapped_height(self.label, width - 20 - 8 - indicator.sizeHint().width())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.indicator.isEnabled():
            # A radio button's toggle() is a no-op when already checked;
            # click() matches what clicking the indicator itself does.
            self.indicator.click()
        super().mousePressEvent(event)

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        style = self.style()
        if style is not None:  # re-evaluate [state=...] selectors
            style.unpolish(self)
            style.polish(self)


class QuestionPanel(QWidget):
    """Choices + feedback for one `QuestionStep`.

    Signals:
        answered: the question is answered (correctly, for a "check"
            question) — the overlay swaps Check for Continue.
        content_changed: feedback/reveal changed the panel's height.
    """

    answered = pyqtSignal()
    content_changed = pyqtSignal()

    def __init__(
        self,
        step: QuestionStep,
        check: Callable[[list[int], int], bool],
        width: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("QuestionPanel")
        self._step = step
        self._check = check
        self.attempts = 0
        self.is_answered = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(not step.multi_select)
        self.rows: list[_ChoiceRow] = []
        for i, choice in enumerate(step.choices):
            indicator: QAbstractButton = QCheckBox() if step.multi_select else QRadioButton()
            indicator.setObjectName(f"QuestionChoiceIndicator_{i}")
            self._group.addButton(indicator, i)
            row = _ChoiceRow(choice.text, indicator, width)
            indicator.toggled.connect(self._on_selection_changed)
            self.rows.append(row)
            layout.addWidget(row)

        if step.multi_select:
            hint = QLabel("Select all that apply.")
            hint.setObjectName("QuestionMultiHint")
            theme_manager.apply_style(hint, "color: {FG_SECONDARY}; font-size: 12px;")
            layout.insertWidget(0, hint)

        self.feedback = QLabel("")
        self.feedback.setObjectName("QuestionFeedback")
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.RichText)
        self.feedback.setFixedWidth(width)
        self.feedback.hide()
        theme_manager.apply_style(self.feedback, _FEEDBACK_QSS)
        layout.addWidget(self.feedback)

    # ── Public API ──────────────────────────────────────────────────────

    def selected(self) -> list[int]:
        return [i for i, row in enumerate(self.rows) if row.indicator.isChecked()]

    def submit(self) -> None:
        """The footer's Check button: grade the current selection."""
        if self.is_answered:
            return
        selection = self.selected()
        if not selection:
            self._show_feedback(_HINT, "")
            return
        self.attempts += 1
        if self._check(selection, self.attempts):
            self._on_answered(selection)
        else:
            self._on_wrong(selection)

    # ── Internals ───────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        if self.is_answered:
            return
        chosen = set(self.selected())
        revealed = self.attempts >= self._step.reveal_after_attempts
        for i, row in enumerate(self.rows):
            if i in chosen:
                row.set_state("selected")
            elif revealed and i in self._step.correct_indices:
                row.set_state("reveal")
            else:
                row.set_state("")

    def _on_answered(self, selection: list[int]) -> None:
        self.is_answered = True
        predict = self._step.kind == QUESTION_PREDICT
        for i, row in enumerate(self.rows):
            row.indicator.setEnabled(False)
            row.setCursor(Qt.CursorShape.ArrowCursor)
            if i in selection:
                row.set_state("selected" if predict else "correct")
        parts = [self._step.choices[i].feedback for i in selection if self._step.choices[i].feedback]
        if self._step.explanation:
            parts.append(self._step.explanation)
        prefix = "" if predict else "✓ Correct. "
        self._show_feedback(prefix + " ".join(parts), "" if predict else "correct")
        self.answered.emit()

    def _on_wrong(self, selection: list[int]) -> None:
        correct = self._step.correct_indices
        wrong_picks = [i for i in selection if i not in correct]
        for i in wrong_picks:
            self.rows[i].set_state("wrong")
        if wrong_picks:
            message = self._step.choices[wrong_picks[0]].feedback
        else:  # multi-select with only correct picks, but not all of them
            message = _MULTI_MISSING
        if self.attempts >= self._step.reveal_after_attempts:
            for i in correct:
                if i not in selection:
                    self.rows[i].set_state("reveal")
            message = f"{message}<br><br><b>{_REVEAL_NOTE}</b>"
        self._show_feedback(f"✗ {message}", "wrong")

    def _show_feedback(self, text: str, state: str) -> None:
        self.feedback.setText(text)
        _claim_wrapped_height(self.feedback, self.feedback.width())
        self.feedback.setProperty("state", state)
        style = self.feedback.style()
        if style is not None:
            style.unpolish(self.feedback)
            style.polish(self.feedback)
        self.feedback.show()
        self.content_changed.emit()
