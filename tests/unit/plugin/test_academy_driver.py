"""Unit tests for karcytics_sdk.plugin.academy_driver.AcademyStepDriver's
mistake-handling: `IValidator.describe_failure()` routing/corrective actions,
and the "no on_fail_step_id configured" safety-net banner.

Calls the driver's polling/hint methods directly rather than driving it
through its QTimer — the timer cadence isn't what's under test here, the
failure-handling logic is.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from PyQt6.QtWidgets import QWidget

from karcytics_sdk.plugin.academy import AcademyManager
from karcytics_sdk.plugin.academy_driver import AcademyStepDriver
from karcytics_sdk.plugin.tutorial_models import (
    Course,
    InfoStep,
    IValidator,
    ValidationFailure,
    VerificationStep,
)
from karcytics_sdk.plugin.tutorial_overlay import TutorialOverlay


class FakeEventBus:
    def __init__(self) -> None:
        self.subscriptions: dict[str, list[Any]] = {}

    def subscribe(self, topic: str, callback: Any) -> None:
        self.subscriptions.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: Any) -> None:
        if topic in self.subscriptions and callback in self.subscriptions[topic]:
            self.subscriptions[topic].remove(callback)

    def emit(self, topic: str, *args: Any) -> None:
        for callback in list(self.subscriptions.get(topic, [])):
            callback(*args)


class CorrectiveValidator(IValidator):
    """Always fails; reports a fix the driver should apply and a step to
    retry, mirroring GateShapeValidator's misnamed-gate auto-rename path.
    """

    def __init__(self) -> None:
        self.corrective_calls: list[Any] = []

    def validate(self, app_state: Any) -> bool:
        return False

    def describe_failure(self, app_state: Any) -> ValidationFailure | None:
        return ValidationFailure(
            reason="Right shape, wrong name — fixing it for you.",
            corrective=self.corrective_calls.append,
            retry_step_id="fixed_info",
        )


class SilentlyFailingValidator(IValidator):
    """Always fails and offers no diagnosis — the case a course author
    forgot to give an on_fail_step_id for.
    """

    def validate(self, app_state: Any) -> bool:
        return False


def make_driver(course: Course, bus: FakeEventBus, tmp_path) -> tuple[AcademyStepDriver, AcademyManager, list[tuple]]:
    manager = AcademyManager(event_bus=bus, persistence_dir=tmp_path / "academy")
    manager.register_storyboard("m", course)
    manager.start_course_confirmed(course.id)

    root = QWidget()
    overlay = TutorialOverlay(manager, bus, parent=root)
    banners: list[tuple] = []
    overlay.show_banner = lambda text, **kw: banners.append((text, kw))  # type: ignore[method-assign]

    driver = AcademyStepDriver(manager, overlay, root, state_provider=lambda: root)
    return driver, manager, banners


@pytest.fixture
def bus() -> FakeEventBus:
    return FakeEventBus()


class TestDescribeFailureRouting:
    def test_corrective_action_runs_and_routes_to_retry_step(self, bus, tmp_path):
        validator = CorrectiveValidator()
        course = Course(
            id="c1",
            title="T",
            steps=[
                VerificationStep(id="check", text="Checking...", validator=validator, on_success_step_id="done"),
                InfoStep(id="fixed_info", text="Fixed it for you."),
                InfoStep(id="done", text="Done"),
            ],
        )
        driver, manager, banners = make_driver(course, bus, tmp_path)

        driver._poll_verification_step(manager.current_step)

        assert validator.corrective_calls, "corrective() must be invoked on failure"
        assert manager.current_step.id == "fixed_info"
        assert banners and "Right shape, wrong name" in banners[0][0]

    def test_no_describe_failure_and_no_on_fail_step_id_shows_safety_net_banner(self, bus, tmp_path):
        course = Course(
            id="c1",
            title="T",
            steps=[
                VerificationStep(
                    id="check",
                    text="Checking...",
                    validator=SilentlyFailingValidator(),
                    on_success_step_id="done",
                ),
                InfoStep(id="done", text="Done"),
            ],
        )
        driver, manager, banners = make_driver(course, bus, tmp_path)
        step_before = manager.current_step

        # Fresh step: the user hasn't had a chance to act yet, so a "wrong"
        # verdict on the very first poll must NOT flash a banner.
        driver._step_entered_at = time.monotonic()
        driver._poll_verification_step(manager.current_step)
        assert not banners, "no banner until the user has had real time to act"
        assert manager.current_step is step_before

        # Once they've plausibly had a chance to act (and still fail): show it.
        driver._step_entered_at = time.monotonic() - 999.0
        driver._poll_verification_step(manager.current_step)
        assert banners, "a step with no failure diagnosis and no on_fail_step_id must still surface a hint"
        assert manager.current_step is step_before

        # Only shown once per step visit, not spammed every poll tick.
        driver._poll_verification_step(manager.current_step)
        assert len(banners) == 1

    def test_failure_hint_field_overrides_default_safety_net_text(self, bus, tmp_path):
        course = Course(
            id="c1",
            title="T",
            steps=[
                VerificationStep(
                    id="check",
                    text="Checking...",
                    validator=SilentlyFailingValidator(),
                    on_success_step_id="done",
                    failure_hint="Try double-clicking the sample first.",
                ),
                InfoStep(id="done", text="Done"),
            ],
        )
        driver, manager, banners = make_driver(course, bus, tmp_path)
        driver._step_entered_at = time.monotonic() - 999.0

        driver._poll_verification_step(manager.current_step)

        assert banners[0][0] == "Try double-clicking the sample first."

    def test_stuck_hint_after_ms_overrides_the_default_safety_net_delay(self, bus, tmp_path):
        course = Course(
            id="c1",
            title="T",
            steps=[
                VerificationStep(
                    id="check",
                    text="Checking...",
                    validator=SilentlyFailingValidator(),
                    on_success_step_id="done",
                    stuck_hint_after_ms=1,
                ),
                InfoStep(id="done", text="Done"),
            ],
        )
        driver, manager, banners = make_driver(course, bus, tmp_path)
        driver._step_entered_at = time.monotonic() - 1.0  # well past the 1ms override

        driver._poll_verification_step(manager.current_step)

        assert banners, "a per-step stuck_hint_after_ms should shorten the default 15s safety-net delay"

    def test_on_fail_step_id_still_wins_when_no_describe_failure_result(self, bus, tmp_path):
        """Existing behavior (retry loop / max_retries routing) must be
        unaffected when a validator has no describe_failure override.
        """
        course = Course(
            id="c1",
            title="T",
            steps=[
                VerificationStep(
                    id="check",
                    text="Checking...",
                    validator=SilentlyFailingValidator(),
                    on_success_step_id="done",
                    on_fail_step_id="fail_step",
                    max_retries=0,
                ),
                InfoStep(id="fail_step", text="Try again"),
                InfoStep(id="done", text="Done"),
            ],
        )
        driver, manager, banners = make_driver(course, bus, tmp_path)

        driver._poll_verification_step(manager.current_step)

        assert manager.current_step.id == "fail_step"
        assert not banners, "the pre-existing on_fail_step_id path shouldn't gain a new banner"


class TestStuckHint:
    def test_shows_hint_once_after_configured_idle_time_elapses(self, bus, tmp_path):
        course = Course(
            id="c1",
            title="T",
            steps=[
                InfoStep(
                    id="s1",
                    text="Click the thing",
                    stuck_hint_text="Still there? Click the highlighted button.",
                    stuck_hint_after_ms=1,
                ),
            ],
        )
        driver, manager, banners = make_driver(course, bus, tmp_path)
        driver._step_entered_at = time.monotonic() - 1.0  # well past 1ms threshold

        driver._maybe_show_stuck_hint(manager.current_step)
        driver._maybe_show_stuck_hint(manager.current_step)  # should not repeat

        assert banners == [("Still there? Click the highlighted button.", {"is_error": False, "duration_ms": 4000})]

    def test_no_stuck_hint_after_ms_configured_is_a_noop(self, bus, tmp_path):
        course = Course(id="c1", title="T", steps=[InfoStep(id="s1", text="Hi")])
        driver, manager, banners = make_driver(course, bus, tmp_path)
        driver._step_entered_at = time.monotonic() - 999.0

        driver._maybe_show_stuck_hint(manager.current_step)

        assert banners == []
