"""Unit tests for karcytics_sdk.plugin.academy.AcademyManager.

Exercises the real state machine against a fake `AcademyEventBus` and a
tmp_path persistence directory — no Qt, no Hub, no plugin process needed,
since the whole point of the dependency-injection split (see academy.py's
module docstring) is that the class itself has no such requirement.
"""

from __future__ import annotations

from typing import Any

import pytest

from karcytics_sdk.plugin.academy import (
    ACADEMY_COURSE_COMPLETED,
    ACADEMY_COURSE_PREPARE_PROJECT,
    ACADEMY_REVIEW_STATE_CHANGED,
    ACADEMY_STEP_CHANGED,
    ACADEMY_SUBTASK_COMPLETED,
    AcademyManager,
)
from karcytics_sdk.plugin.tutorial_models import (
    BranchingStep,
    Course,
    ForcedInteractionStep,
    InfoStep,
    SubTask,
    WaitForEventStep,
)


class FakeEventBus:
    """Records every subscribe/unsubscribe/emit call; delivers emitted args
    synchronously to any matching subscriber, mirroring a real bus closely
    enough to exercise WaitForEventStep auto-advance.
    """

    def __init__(self) -> None:
        self.subscriptions: dict[str, list[Any]] = {}
        self.emitted: list[tuple[str, tuple]] = []

    def subscribe(self, topic: str, callback: Any) -> None:
        self.subscriptions.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: Any) -> None:
        if topic in self.subscriptions and callback in self.subscriptions[topic]:
            self.subscriptions[topic].remove(callback)

    def emit(self, topic: str, *args: Any) -> None:
        self.emitted.append((topic, args))
        for callback in list(self.subscriptions.get(topic, [])):
            callback(*args)


@pytest.fixture
def bus() -> FakeEventBus:
    return FakeEventBus()


@pytest.fixture
def manager(bus, tmp_path) -> AcademyManager:
    return AcademyManager(event_bus=bus, persistence_dir=tmp_path / "academy")


def make_two_step_course(course_id: str = "course_1") -> Course:
    return Course(
        id=course_id,
        title="Test Course",
        badge_reward="Test Badge",
        badge_icon="🏅",
        steps=[
            InfoStep(id="step_1", text="First", next_step_id="step_2"),
            InfoStep(id="step_2", text="Second", next_step_id=None),
        ],
    )


class TestRegisterAndDiscoverCourses:
    def test_register_storyboard_stores_the_course_for_its_module(self, manager):
        course = make_two_step_course()

        manager.register_storyboard("flow_cytometry", course)

        assert manager.courses_by_module["flow_cytometry"] == [course]
        assert manager.get_courses_for_module("flow_cytometry") == [course]

    def test_get_courses_for_unregistered_module_is_an_empty_list(self, manager):
        assert manager.get_courses_for_module("nothing_registered") == []


class TestStartCourse:
    def test_start_course_emits_prepare_project_without_starting(self, manager, bus):
        manager.start_course("course_1")

        assert bus.emitted == [(ACADEMY_COURSE_PREPARE_PROJECT, ("course_1",))]
        assert manager.active_course is None

    def test_start_course_confirmed_activates_first_step(self, manager, bus):
        course = make_two_step_course()
        manager.register_storyboard("m", course)

        started = manager.start_course_confirmed("course_1")

        assert started is True
        assert manager.active_course is course
        assert manager.current_step.id == "step_1"
        assert (ACADEMY_STEP_CHANGED, (manager.current_step,)) in bus.emitted

    def test_start_course_confirmed_returns_false_for_unknown_course(self, manager):
        assert manager.start_course_confirmed("nope") is False
        assert manager.active_course is None


class TestNextStep:
    def test_next_step_advances_to_the_named_next_step(self, manager):
        course = make_two_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")

        manager.next_step()

        assert manager.current_step.id == "step_2"

    def test_next_step_past_the_last_step_completes_the_course(self, manager, bus):
        course = make_two_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")

        manager.next_step()  # -> step_2
        manager.next_step()  # step_2.next_step_id is None -> complete

        assert manager.current_step is None
        assert "course_1" in manager.completed_courses
        completions = [e for e in bus.emitted if e[0] == ACADEMY_COURSE_COMPLETED]
        assert completions == [(ACADEMY_COURSE_COMPLETED, ("course_1", "Test Badge"))]

    def test_next_step_with_no_active_course_is_a_safe_noop(self, manager):
        manager.next_step()
        assert manager.current_step is None


class TestForcedInteractionStep:
    def test_next_step_blocked_until_all_subtasks_complete(self, manager):
        course = Course(
            id="c",
            title="Forced",
            steps=[
                ForcedInteractionStep(
                    id="step_1",
                    text="Do both",
                    next_step_id="step_2",
                    sub_tasks=[
                        SubTask(id="a", instruction="A", target_widget_name="w1"),
                        SubTask(id="b", instruction="B", target_widget_name="w2"),
                    ],
                ),
                InfoStep(id="step_2", text="Done"),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")

        manager.next_step()
        assert manager.current_step.id == "step_1", "must not advance with 0/2 subtasks done"

        manager.complete_subtask("a")
        manager.next_step()
        assert manager.current_step.id == "step_1", "must not advance with 1/2 subtasks done"

        manager.complete_subtask("b")
        manager.next_step()
        assert manager.current_step.id == "step_2"

    def test_complete_subtask_emits_remaining_count(self, manager, bus):
        course = Course(
            id="c",
            title="Forced",
            steps=[
                ForcedInteractionStep(
                    id="step_1",
                    text="Do both",
                    sub_tasks=[
                        SubTask(id="a", instruction="A", target_widget_name="w1"),
                        SubTask(id="b", instruction="B", target_widget_name="w2"),
                    ],
                ),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")

        manager.complete_subtask("a")

        assert (ACADEMY_SUBTASK_COMPLETED, ("a", 1)) in bus.emitted

    def test_complete_subtask_with_unknown_id_is_ignored(self, manager, bus):
        course = Course(
            id="c",
            title="Forced",
            steps=[
                ForcedInteractionStep(
                    id="step_1",
                    text="Do one",
                    sub_tasks=[SubTask(id="a", instruction="A", target_widget_name="w1")],
                ),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")
        bus.emitted.clear()

        manager.complete_subtask("not_a_real_subtask")

        assert bus.emitted == []


class TestWaitForEventStep:
    def test_subscribes_on_start_and_auto_advances_when_the_event_fires(self, manager, bus):
        course = Course(
            id="c",
            title="Waits",
            steps=[
                WaitForEventStep(id="step_1", text="Waiting...", next_step_id="step_2", event_name="PROJECT_LOADED"),
                InfoStep(id="step_2", text="Done"),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")

        assert "PROJECT_LOADED" in bus.subscriptions
        assert manager.current_step.id == "step_1"

        bus.emit("PROJECT_LOADED", "/some/project")

        assert manager.current_step.id == "step_2"

    def test_advancing_past_a_wait_step_unsubscribes_it(self, manager, bus):
        course = Course(
            id="c",
            title="Waits",
            steps=[
                WaitForEventStep(id="step_1", text="Waiting...", next_step_id="step_2", event_name="PROJECT_LOADED"),
                InfoStep(id="step_2", text="Done"),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")

        bus.emit("PROJECT_LOADED")

        assert bus.subscriptions["PROJECT_LOADED"] == []

    def test_a_stale_event_after_manually_skipping_the_step_is_ignored(self, manager):
        course = Course(
            id="c",
            title="Waits",
            steps=[
                WaitForEventStep(id="step_1", text="Waiting...", next_step_id="step_2", event_name="PROJECT_LOADED"),
                InfoStep(id="step_2", text="Done", next_step_id="step_1"),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")
        manager.next_step(specific_step_id="step_2")
        assert manager.current_step.id == "step_2"


class TestProgressPersistence:
    def test_progress_persists_across_manager_instances(self, bus, tmp_path):
        persistence_dir = tmp_path / "academy"
        course = make_two_step_course()

        first = AcademyManager(event_bus=bus, persistence_dir=persistence_dir)
        first.register_storyboard("m", course)
        first.start_course_confirmed("course_1")
        first.next_step()
        first.next_step()  # completes the course

        second = AcademyManager(event_bus=FakeEventBus(), persistence_dir=persistence_dir)

        assert second.completed_courses == ["course_1"]
        assert second.badges[0]["id"] == "course_1"

    def test_missing_progress_file_starts_with_empty_state(self, bus, tmp_path):
        manager = AcademyManager(event_bus=bus, persistence_dir=tmp_path / "nonexistent")

        assert manager.completed_courses == []
        assert manager.badges == []

    def test_record_and_has_prerequisite(self, manager):
        assert manager.has_prerequisite("course_1") is False

        manager.record_prerequisite("course_1", "workflow_hash_abc")

        assert manager.has_prerequisite("course_1") is True


class TestGetProgressAndResetCourse:
    def test_get_progress_is_100_once_completed(self, manager):
        course = make_two_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()

        assert manager.get_progress("course_1") == 100.0

    def test_get_progress_is_zero_for_untouched_course(self, manager):
        assert manager.get_progress("never_started") == 0.0

    def test_reset_course_clears_completion_and_badges(self, manager):
        course = make_two_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()
        assert "course_1" in manager.completed_courses

        manager.reset_course("course_1")

        assert "course_1" not in manager.completed_courses
        assert manager.badges == []
        assert manager.active_course is None


class TestIsCoreIntroDone:
    def test_false_when_not_completed(self, manager):
        assert manager.is_core_intro_done() is False

    def test_true_once_core_intro_v1_is_completed(self, manager):
        course = Course(id="core_intro_v1", title="Core Intro", steps=[InfoStep(id="s", text="hi")])
        manager.register_storyboard("core", course)
        manager.start_course_confirmed("core_intro_v1")
        manager.next_step()  # only step -> completes

        assert manager.is_core_intro_done() is True


def make_three_step_course(course_id: str = "course_1") -> Course:
    return Course(
        id=course_id,
        title="Test Course",
        steps=[
            InfoStep(id="step_1", text="First", next_step_id="step_2"),
            InfoStep(id="step_2", text="Second", next_step_id="step_3"),
            InfoStep(id="step_3", text="Third", next_step_id=None),
        ],
    )


class TestReviewMode:
    def test_step_history_records_steps_in_visitation_order(self, manager):
        course = make_three_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()

        assert manager.step_history == ["step_1", "step_2", "step_3"]

    def test_is_reviewing_false_and_review_previous_noop_before_any_history(self, manager):
        assert manager.is_reviewing is False
        assert manager.can_review_previous() is False
        assert manager.review_previous() is False
        assert manager.is_reviewing is False

    def test_review_previous_moves_pointer_back_and_emits_review_state_changed(self, manager, bus):
        course = make_three_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()
        bus.emitted.clear()

        result = manager.review_previous()

        assert result is True
        assert manager.is_reviewing is True
        assert manager.get_review_step().id == "step_2"
        assert manager.current_step.id == "step_3", "the live step must be untouched"
        assert bus.emitted == [(ACADEMY_REVIEW_STATE_CHANGED, (manager.get_review_step(), True))]

    def test_review_previous_can_walk_back_multiple_steps(self, manager):
        course = make_three_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()

        manager.review_previous()
        manager.review_previous()

        assert manager.get_review_step().id == "step_1"
        assert manager.can_review_previous() is False
        assert manager.review_previous() is False

    def test_review_next_pages_forward_through_history_and_exits_at_the_live_step(self, manager):
        course = make_three_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()

        manager.review_previous()
        manager.review_previous()
        assert manager.get_review_step().id == "step_1"

        assert manager.review_next() is True
        assert manager.get_review_step().id == "step_2"
        assert manager.can_review_next() is True
        assert manager.is_reviewing is True

        # Paging forward onto the live step's own history entry exits review
        # mode outright — walking forward back to "where I am" should mean
        # you're actually back, not looking at a read-only copy of it with
        # Next stuck disabled (see review_next()'s docstring for the bug
        # this fixes).
        assert manager.review_next() is True
        assert manager.is_reviewing is False
        assert manager.get_review_step() is None
        assert manager.current_step.id == "step_3"

        assert manager.review_next() is False

    def test_return_to_current_clears_pointer_and_emits_with_is_reviewing_false(self, manager, bus):
        course = make_three_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.review_previous()
        bus.emitted.clear()

        manager.return_to_current()

        assert manager.is_reviewing is False
        assert manager.get_review_step() is None
        assert bus.emitted == [(ACADEMY_REVIEW_STATE_CHANGED, (manager.current_step, False))]

    def test_return_to_current_with_nothing_to_return_from_is_a_safe_noop(self, manager, bus):
        manager.return_to_current()
        assert bus.emitted == []

    def test_next_step_is_a_noop_while_reviewing(self, manager):
        course = make_three_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.review_previous()

        manager.next_step()

        assert manager.current_step.id == "step_2", "the live step must not advance while reviewing"
        assert manager.is_reviewing is True

    def test_complete_subtask_is_a_noop_while_reviewing(self, manager, bus):
        # The LIVE step is the ForcedInteractionStep here — reviewing an
        # earlier, different step must still block completing a subtask on
        # it, even though `current_step` itself still satisfies the
        # ForcedInteractionStep type check that complete_subtask() also does.
        course = Course(
            id="c",
            title="Forced",
            steps=[
                InfoStep(id="step_1", text="First", next_step_id="step_2"),
                ForcedInteractionStep(
                    id="step_2",
                    text="Do it",
                    sub_tasks=[SubTask(id="a", instruction="A", target_widget_name="w1")],
                ),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")
        manager.next_step()  # -> step_2 (live, ForcedInteractionStep)
        manager.review_previous()  # reviewing step_1
        bus.emitted.clear()

        manager.complete_subtask("a")

        assert bus.emitted == [], "reviewing must never let a subtask be completed"
        assert manager.active_subtask_progress == {}

    def test_complete_course_is_a_noop_while_reviewing(self, manager, bus):
        course = make_two_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()  # -> step_2, gives history 2 entries so review_previous can engage
        manager.review_previous()
        bus.emitted.clear()

        manager.complete_course()

        assert "course_1" not in manager.completed_courses
        assert bus.emitted == []

    def test_branching_history_records_only_the_taken_path_not_the_untaken_option(self, manager):
        course = Course(
            id="c",
            title="Branch",
            steps=[
                BranchingStep(id="step_1", text="Choose", options={"a": "step_a", "b": "step_b"}),
                InfoStep(id="step_a", text="A"),
                InfoStep(id="step_b", text="B"),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")

        manager.next_step(specific_step_id="step_a")

        assert manager.step_history == ["step_1", "step_a"]
        assert "step_b" not in manager.step_history

    def test_revisiting_a_step_via_retry_routing_appends_a_new_history_entry(self, manager):
        course = Course(
            id="c",
            title="Retry",
            steps=[
                InfoStep(id="step_1", text="First", next_step_id="step_2"),
                InfoStep(id="step_2", text="Retry target", next_step_id="step_1"),
            ],
        )
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("c")

        manager.next_step()  # -> step_2
        manager.next_step()  # -> step_1 again (simulating a retry loop back to an earlier step)

        assert manager.step_history == ["step_1", "step_2", "step_1"]

    def test_reset_course_clears_history_and_review_pointer(self, manager):
        course = make_two_step_course()
        manager.register_storyboard("m", course)
        manager.start_course_confirmed("course_1")
        manager.next_step()
        manager.next_step()  # completes -> eligible for reset
        manager.reset_course("course_1")

        assert manager.step_history == []
        assert manager.is_reviewing is False

    def test_starting_a_new_course_clears_previous_course_history(self, manager):
        course_1 = make_two_step_course("course_1")
        course_2 = make_two_step_course("course_2")
        manager.register_storyboard("m", course_1)
        manager.register_storyboard("m", course_2)
        manager.start_course_confirmed("course_1")
        manager.next_step()

        manager.start_course_confirmed("course_2")

        assert manager.step_history == ["step_1"]
