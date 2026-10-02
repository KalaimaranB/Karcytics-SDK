"""QuestionStep: the model, answer recording, and the bubble's check/retry/reveal flow."""

from __future__ import annotations

from typing import Any

import pytest
from PyQt6.QtWidgets import QPushButton, QWidget

from karcytics_sdk.plugin.academy import AcademyManager
from karcytics_sdk.plugin.tutorial_models import (
    QUESTION_PREDICT,
    AnswerChoice,
    Course,
    InfoStep,
    QuestionStep,
)
from karcytics_sdk.plugin.tutorial_overlay import TutorialOverlay


class FakeEventBus:
    def __init__(self) -> None:
        self.subscriptions: dict[str, list[Any]] = {}

    def subscribe(self, topic: str, callback: Any) -> None:
        self.subscriptions.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: Any) -> None:
        if callback in self.subscriptions.get(topic, []):
            self.subscriptions[topic].remove(callback)

    def emit(self, topic: str, *args: Any) -> None:
        for callback in list(self.subscriptions.get(topic, [])):
            callback(*args)


def dead_cells() -> QuestionStep:
    return QuestionStep(
        id="q",
        text="Which peak are the dead cells?",
        question_id="c1_dead_peak",
        choices=[
            AnswerChoice("The dim peak", feedback="Dim = PI kept out: an intact membrane."),
            AnswerChoice("The bright peak", correct=True),
            AnswerChoice("Neither", feedback="Dead cells take up PI, so they do show up."),
        ],
        explanation="Damaged membranes let PI in.",
        next_step_id="after",
    )


def select_all() -> QuestionStep:
    return QuestionStep(
        id="m",
        text="Why gate to Leukocytes first? (select all)",
        question_id="c3_why_gate",
        multi_select=True,
        choices=[
            AnswerChoice("Debris forms fake islands", correct=True),
            AnswerChoice("It's faster", correct=True),
            AnswerChoice("UMAP can't read scatter", feedback="It can; we just don't want debris."),
        ],
        next_step_id="after",
    )


def prediction() -> QuestionStep:
    return QuestionStep(
        id="p",
        text="Which organ will have almost no B cells?",
        kind=QUESTION_PREDICT,
        question_id="c1_mystery",
        choices=[AnswerChoice("Thymus"), AnswerChoice("Spleen")],
        next_step_id="after",
    )


def start(tmp_path, *steps, course_id: str = "c") -> AcademyManager:
    manager = AcademyManager(event_bus=FakeEventBus(), persistence_dir=tmp_path / "academy")
    course = Course(id=course_id, title="C", steps=[*steps, InfoStep(id="after", text="Done")])
    manager.register_storyboard("m", course)
    manager.start_course_confirmed(course_id)
    return manager


# ── Model ─────────────────────────────────────────────────────────────────


class TestValidation:
    def test_single_choice_needs_exactly_one_correct(self):
        with pytest.raises(ValueError, match="exactly one correct"):
            QuestionStep(
                id="x",
                text="?",
                choices=[AnswerChoice("a", correct=True), AnswerChoice("b", correct=True)],
            )

    def test_select_all_needs_a_correct_choice(self):
        with pytest.raises(ValueError, match="at least one correct"):
            QuestionStep(
                id="x",
                text="?",
                multi_select=True,
                choices=[AnswerChoice("a", feedback="no"), AnswerChoice("b", feedback="no")],
            )

    def test_wrong_choices_must_explain_why(self):
        with pytest.raises(ValueError, match="feedback"):
            QuestionStep(id="x", text="?", choices=[AnswerChoice("a", correct=True), AnswerChoice("b")])

    def test_needs_two_choices(self):
        with pytest.raises(ValueError, match="at least 2"):
            QuestionStep(id="x", text="?", choices=[AnswerChoice("a", correct=True)])

    def test_prediction_needs_an_id_but_no_correct_answer(self):
        with pytest.raises(ValueError, match="question_id"):
            QuestionStep(id="x", text="?", kind=QUESTION_PREDICT, choices=[AnswerChoice("a"), AnswerChoice("b")])
        assert prediction().correct_indices == frozenset()

    def test_unknown_kind(self):
        with pytest.raises(ValueError, match="kind"):
            QuestionStep(id="x", text="?", kind="quiz", choices=[AnswerChoice("a"), AnswerChoice("b")])


class TestIsCorrect:
    def test_single_choice(self):
        q = dead_cells()
        assert q.is_correct({1})
        assert not q.is_correct({0})
        assert not q.is_correct(set())

    def test_select_all_needs_exactly_the_correct_set(self):
        q = select_all()
        assert q.is_correct({0, 1})
        assert not q.is_correct({0})
        assert not q.is_correct({0, 1, 2})

    def test_any_answer_answers_a_prediction(self):
        assert prediction().is_correct({1})


# ── AcademyManager ────────────────────────────────────────────────────────


class TestManager:
    def test_cannot_advance_until_answered_correctly(self, tmp_path):
        manager = start(tmp_path, dead_cells())
        manager.next_step()
        assert manager.current_step.id == "q"

        assert manager.record_answer([0], attempts=1) is False
        manager.next_step()
        assert manager.current_step.id == "q"

        assert manager.record_answer([1], attempts=2) is True
        manager.next_step()
        assert manager.current_step.id == "after"

    def test_answers_persist_locally_with_attempts(self, tmp_path):
        manager = start(tmp_path, dead_cells())
        manager.record_answer([0], attempts=1)
        manager.record_answer([1], attempts=2)

        reloaded = AcademyManager(event_bus=FakeEventBus(), persistence_dir=tmp_path / "academy")
        assert reloaded.answers["c"]["c1_dead_peak"] == {
            "choices": ["The bright peak"],
            "attempts": 2,
            "correct": True,
        }

    def test_prediction_revealed_in_a_later_course(self, tmp_path):
        manager = start(tmp_path, prediction(), course_id="course1")
        manager.record_answer([0], attempts=1)

        later = AcademyManager(event_bus=FakeEventBus(), persistence_dir=tmp_path / "academy")
        text = later.format_step_text("You predicted {answer:c1_mystery}.")
        assert text == "You predicted Thymus."
        assert later.format_step_text("{answer:never_asked}") == "(not answered)"

    def test_next_question_starts_unanswered(self, tmp_path):
        second = dead_cells()
        second.id, second.question_id = "q2", "c1_dead_peak_again"
        first = dead_cells()
        first.next_step_id = "q2"
        manager = start(tmp_path, first, second)
        manager.record_answer([1], attempts=1)
        manager.next_step()
        assert manager.current_step.id == "q2"
        manager.next_step()
        assert manager.current_step.id == "q2"


# ── Overlay ───────────────────────────────────────────────────────────────


def overlay_for(tmp_path, step: QuestionStep) -> tuple[TutorialOverlay, AcademyManager]:
    manager = start(tmp_path, step)
    root = QWidget()
    root.resize(1200, 800)
    overlay = TutorialOverlay(manager, FakeEventBus(), parent=root)
    overlay._root = root  # type: ignore[attr-defined]  # keep parent alive
    overlay.render_step(manager.current_step)
    return overlay, manager


def footer_button(overlay: TutorialOverlay) -> QPushButton:
    found = overlay.findChild(QPushButton, "QuestionCheckButton") or overlay.findChild(
        QPushButton, "QuestionContinueButton"
    )
    assert found is not None
    return found


class TestOverlay:
    def test_renders_choices_and_check_not_next(self, tmp_path):
        overlay, _ = overlay_for(tmp_path, dead_cells())
        panel = overlay.question_panel
        assert panel is not None
        assert len(panel.rows) == 3  # noqa: PLR2004
        assert overlay.btn_next.isHidden()
        assert footer_button(overlay).text() == "Check answer"

    def test_wrong_answer_explains_and_stays(self, tmp_path):
        overlay, manager = overlay_for(tmp_path, dead_cells())
        panel = overlay.question_panel
        panel.rows[0].indicator.click()
        footer_button(overlay).click()
        assert "intact membrane" in panel.feedback.text()
        assert footer_button(overlay).text() == "Check answer"
        assert manager.current_step.id == "q"

    def test_correct_answer_after_reveal_then_continue(self, tmp_path):
        overlay, manager = overlay_for(tmp_path, dead_cells())
        panel = overlay.question_panel
        panel.rows[0].indicator.click()
        footer_button(overlay).click()
        panel.rows[2].indicator.click()
        footer_button(overlay).click()
        assert panel.rows[1].property("state") == "reveal"
        assert "outlined answer is correct" in panel.feedback.text()

        panel.rows[1].indicator.click()
        footer_button(overlay).click()
        assert "Damaged membranes" in panel.feedback.text()
        assert footer_button(overlay).text() == "Continue →"
        footer_button(overlay).click()
        assert manager.current_step.id == "after"
        assert manager.answers["c"]["c1_dead_peak"]["attempts"] == 3  # noqa: PLR2004

    def test_select_all_with_a_missing_answer_is_wrong(self, tmp_path):
        overlay, manager = overlay_for(tmp_path, select_all())
        panel = overlay.question_panel
        panel.rows[0].indicator.click()
        footer_button(overlay).click()
        assert "still unselected" in panel.feedback.text()
        panel.rows[1].indicator.click()
        footer_button(overlay).click()
        assert footer_button(overlay).text() == "Continue →"

    def test_empty_check_asks_for_an_answer_without_using_a_try(self, tmp_path):
        overlay, _ = overlay_for(tmp_path, dead_cells())
        footer_button(overlay).click()
        assert overlay.question_panel.attempts == 0
        assert "Choose an answer" in overlay.question_panel.feedback.text()

    def test_prediction_accepts_any_answer(self, tmp_path):
        overlay, manager = overlay_for(tmp_path, prediction())
        overlay.question_panel.rows[1].indicator.click()
        footer_button(overlay).click()
        assert footer_button(overlay).text() == "Let's find out →"
        footer_button(overlay).click()
        assert manager.recorded_answer("c1_mystery") == "Spleen"

    def test_long_explanation_fits_in_the_bubble(self, tmp_path, qtbot):
        step = dead_cells()
        step.explanation = " ".join(["Damaged membranes let PI in, so dead cells glow."] * 3)
        overlay, _ = overlay_for(tmp_path, step)
        overlay.parentWidget().show()
        qtbot.wait(50)  # showing re-renders the step
        panel = overlay.question_panel
        panel.rows[0].indicator.click()
        footer_button(overlay).click()  # a short "wrong" line first…
        qtbot.wait(50)
        panel.rows[1].indicator.click()
        footer_button(overlay).click()  # …then the long explanation replaces it
        qtbot.wait(50)  # the deferred re-fit
        assert panel.feedback.height() >= panel.feedback.heightForWidth(panel.feedback.width())
        assert panel.height() >= panel.sizeHint().height()
        overlay.parentWidget().close()
