"""The Karcytics Academy state machine — dependency-injected, process-agnostic.

`AcademyManager` used to live only in the Hub (`karcytics.core.tutorial_manager`)
and reached for Hub-only globals directly: the Hub's own `event_bus`/
`KarcyticsEvent` enum and `AppConfig.APP_DATA_DIR`. That made it unusable from
an isolated plugin process, which has no access to either. This is the same
class with those two dependencies inverted into constructor arguments — see
`AcademyEventBus` below — so the Hub and any isolated plugin can each run
their own real instance, wired to whatever event bus and persistence
directory make sense for that process. Compare `PreferenceManagerProtocol`
in `preferences.py`, which inverts a Hub-only dependency the same way.

Every topic this class emits or subscribes to is a plain string, not an enum
member — the Hub's `KarcyticsEvent` enum member *names* line up with these
strings exactly (`"ACADEMY_STEP_CHANGED"`, `"PROJECT_LOADED"`, etc.), so a
Hub-side `AcademyEventBus` implementation can do `KarcyticsEvent[topic]` to
bridge into the real enum-keyed bus. A plugin-side implementation can use the
string directly as its topic key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .io import load_json, save_json
from .tutorial_models import BaseStep, Course, ForcedInteractionStep, WaitForEventStep

logger = logging.getLogger(__name__)

# Canonical topic names. These match the Hub's `KarcyticsEvent` enum member
# names exactly (see `karcytics.core.event_bus`) so a Hub-side event bus
# adapter can resolve them with a plain `KarcyticsEvent[topic]` lookup.
ACADEMY_STEP_CHANGED = "ACADEMY_STEP_CHANGED"
ACADEMY_COURSE_COMPLETED = "ACADEMY_COURSE_COMPLETED"
ACADEMY_SUBTASK_COMPLETED = "ACADEMY_SUBTASK_COMPLETED"
ACADEMY_CHECKPOINT_SAVED = "ACADEMY_CHECKPOINT_SAVED"
ACADEMY_COURSE_PREPARE_PROJECT = "ACADEMY_COURSE_PREPARE_PROJECT"


@runtime_checkable
class AcademyEventBus(Protocol):
    """What `AcademyManager` needs from a process's event bus.

    `emit` takes the same variable positional args the Hub's real
    `EventManager.emit()` always has (a step object, a `(course_id,
    badge_reward)` pair, and so on) — an implementation wrapping a
    single-payload bus (like a plugin's `CentralEventBus`) packs/unpacks
    that itself.
    """

    def subscribe(self, topic: str, callback: Callable[..., Any]) -> None: ...

    def unsubscribe(self, topic: str, callback: Callable[..., Any]) -> None: ...

    def emit(self, topic: str, *args: Any) -> None: ...


class AcademyManager:
    """Manages the Karcytics Academy state machine and module courses.

    Identical state machine to the Hub's original `AcademyManager`; only the
    event bus and the persistence directory are now supplied by the caller
    instead of imported directly.
    """

    def __init__(self, event_bus: AcademyEventBus, persistence_dir: Path) -> None:
        """Initialize academy paths, course registries, progress data, and active course state."""
        self._event_bus = event_bus
        self.config_dir = persistence_dir
        self.progress_file = self.config_dir / "progress.json"
        self.checkpoints_dir = self.config_dir / "checkpoints"

        # Maps module_id to a list of registered courses
        self.courses_by_module: dict[str, list[Course]] = {}

        # Persistence data
        self.completed_courses: list[str] = []
        self.badges: list[dict[str, Any]] = []
        self.prerequisites_met: dict[str, str] = {}  # course_id -> workflow_hash

        # State tracking
        self.active_course: Course | None = None
        self.current_step: BaseStep | None = None
        self.active_subtask_progress: dict[str, bool] = {}

        # Tracks the active event subscription for WaitForEventStep
        self._wait_event_subscription: tuple[str, Callable[..., Any]] | None = None

        self._load_progress()

    def _load_progress(self) -> None:
        """Loads progress from disk."""
        if not self.progress_file.exists():
            return
        try:
            data = load_json(str(self.progress_file))
        except Exception as e:
            logger.warning(f"Failed to load academy progress: {e}")
            return
        self.completed_courses = data.get("completed_courses", [])
        self.badges = data.get("badges", [])
        self.prerequisites_met = data.get("prerequisites_met", {})

    def _save_progress(self) -> None:
        """Saves current progress to disk."""
        data = {
            "completed_courses": self.completed_courses,
            "badges": self.badges,
            "prerequisites_met": self.prerequisites_met,
        }
        try:
            save_json(str(self.progress_file), data)
        except Exception as e:
            logger.error(f"Failed to save academy progress: {e}")

    def register_storyboard(self, module_id: str, course: Course) -> None:
        """Registers a new tutorial course for a specific module."""
        if module_id not in self.courses_by_module:
            self.courses_by_module[module_id] = []
        self.courses_by_module[module_id].append(course)
        logger.info(f"Registered Academy course '{course.title}' for module '{module_id}'")

    def get_courses_for_module(self, module_id: str) -> list[Course]:
        """Returns all registered courses for the given module ID."""
        return self.courses_by_module.get(module_id, [])

    def start_course(self, course_id: str) -> None:
        """Requests to start a course. The UI/Plugin must handle project setup and call confirmed."""  # noqa: E501
        self._event_bus.emit(ACADEMY_COURSE_PREPARE_PROJECT, course_id)

    def start_course_confirmed(self, course_id: str) -> bool:
        """Start a prepared course and activate its initial step.

        Parameters:
            course_id (str): Identifier of the course to start.

        Returns:
            bool: `true` if the course was found and started, `false` otherwise.
        """
        for courses in self.courses_by_module.values():
            for course in courses:
                if course.id == course_id:
                    self.active_course = course

                    if course.steps:
                        self.current_step = course.steps[0]
                        self.active_subtask_progress = {}
                        # Subscribe immediately if the first step is a WaitForEventStep
                        if isinstance(self.current_step, WaitForEventStep):
                            self._subscribe_wait_event(self.current_step)

                    self._emit_step_changed()
                    return True
        logger.error(f"Attempted to start unknown course: {course_id}")
        return False

    def record_prerequisite(self, course_id: str, workflow_hash: str) -> None:
        """Records that a course prerequisite workflow was saved."""
        self.prerequisites_met[course_id] = workflow_hash
        self._save_progress()

    def has_prerequisite(self, course_id: str) -> bool:
        """Checks if a course has its prerequisite workflow hash recorded."""
        return course_id in self.prerequisites_met

    def next_step(self, specific_step_id: str | None = None, _internal_force: bool = False) -> None:
        """Progresses the state machine to the next step."""
        if not self.active_course or not self.current_step:
            return

        # Enforce sub-task completion if it's a ForcedInteractionStep
        if isinstance(self.current_step, ForcedInteractionStep) and specific_step_id is None:
            progress = self._get_current_subtask_progress()
            if not all(progress.get(task.id, False) for task in self.current_step.sub_tasks):
                logger.warning("Cannot advance: not all sub-tasks completed.")
                return

        if isinstance(self.current_step, WaitForEventStep) and not _internal_force and specific_step_id is None:
            logger.warning(f"Cannot advance: waiting for event {self.current_step.event_name}")
            return

        # Unsubscribe any previous WaitForEventStep listener before moving on
        self._cancel_wait_subscription()

        next_id = specific_step_id or self.current_step.next_step_id

        if next_id and next_id != "__complete__":
            self.current_step = self.active_course.get_step(next_id)
            if self.current_step:
                # Reset subtask progress for the new step
                if isinstance(self.current_step, ForcedInteractionStep):
                    self.active_subtask_progress = {}
                # Subscribe if this is a WaitForEventStep
                if isinstance(self.current_step, WaitForEventStep):
                    self._subscribe_wait_event(self.current_step)
            self._emit_step_changed()
        else:
            self.complete_course()
            self.current_step = None
            self._emit_step_changed()

    def complete_subtask(self, subtask_id: str) -> None:
        """Marks a sub-task as complete for the current step."""
        if not self.active_course or not isinstance(self.current_step, ForcedInteractionStep):
            return

        valid_ids = [t.id for t in self.current_step.sub_tasks]
        if subtask_id not in valid_ids:
            return

        self.active_subtask_progress[subtask_id] = True

        remaining = sum(1 for t in valid_ids if not self.active_subtask_progress.get(t, False))
        self._event_bus.emit(ACADEMY_SUBTASK_COMPLETED, subtask_id, remaining)

    def _get_current_subtask_progress(self) -> dict[str, bool]:
        if not self.active_course:
            return {}
        return self.active_subtask_progress

    def _subscribe_wait_event(self, step: WaitForEventStep) -> None:
        """Subscribe to the named event so the step auto-advances when it fires."""
        if not step.event_name:
            logger.error(f"WaitForEventStep {step.id!r} has no event_name set")
            return

        topic = step.event_name
        step_id = step.id

        def _on_event(*_args: Any, **_kwargs: Any) -> Any:
            # Only advance if we're still on this step
            if self.current_step and self.current_step.id == step_id:
                self.next_step(_internal_force=True)

        self._event_bus.subscribe(topic, _on_event)
        self._wait_event_subscription = (topic, _on_event)
        logger.debug(f"WaitForEventStep {step_id!r}: subscribed to {topic}")

    def _cancel_wait_subscription(self) -> None:
        """Unsubscribe any active WaitForEventStep listener."""
        if self._wait_event_subscription:
            topic, callback = self._wait_event_subscription
            self._event_bus.unsubscribe(topic, callback)
            self._wait_event_subscription = None

    def complete_course(self) -> None:
        """Marks the active course as completed and awards badges."""
        self._cancel_wait_subscription()
        if self.active_course:
            course_id = self.active_course.id
            if course_id not in self.completed_courses:
                self.completed_courses.append(course_id)

            if self.active_course.badge_reward:
                self._award_badge(self.active_course)

            self._save_checkpoint(course_id)
            self._save_progress()

            self._event_bus.emit(ACADEMY_COURSE_COMPLETED, course_id, self.active_course.badge_reward)

    def reset_course(self, course_id: str) -> None:
        """Clears progress for a specific course, allowing the user to start over."""
        modified = False
        if course_id in self.completed_courses:
            self.completed_courses.remove(course_id)
            modified = True

        initial_len = len(self.badges)
        self.badges = [b for b in self.badges if b.get("id") != course_id]
        if len(self.badges) != initial_len:
            modified = True

        if modified:
            self._save_progress()
            logger.info(f"Progress reset for course {course_id}")

            # Reset state
            self.active_course = None
            self.current_step = None
            self._emit_step_changed()

    def _award_badge(self, course: Course) -> None:
        """Awards a badge if not already earned."""
        if not any(b["id"] == course.id for b in self.badges):
            badge = {
                "id": course.id,
                "label": course.badge_reward,
                "icon": course.badge_icon,
                "earned_at": datetime.now(UTC).isoformat(),
            }
            self.badges.append(badge)
            logger.info(f"Awarded badge: {course.badge_reward}")

    def _save_checkpoint(self, course_id: str) -> None:
        """Saves the current workflow state as a checkpoint."""
        checkpoint_path = self.checkpoints_dir / f"{course_id}.json"
        # In a real app, we would call the workflow_manager.serialize_state() here.
        # For now we create a dummy file. Carried forward as-is from the Hub's
        # original implementation — a pre-existing stub, not a new gap.
        data = {"checkpoint_for": course_id, "timestamp": datetime.now(UTC).isoformat()}
        try:
            save_json(str(checkpoint_path), data)
        except Exception as e:
            logger.error(f"Failed to save checkpoint for {course_id}: {e}")
            return
        self._event_bus.emit(ACADEMY_CHECKPOINT_SAVED, course_id, str(checkpoint_path))

    def restore_checkpoint(self, course_id: str) -> bool:
        """Restores the workflow state from a checkpoint."""
        checkpoint_path = self.checkpoints_dir / f"{course_id}.json"
        if not checkpoint_path.exists():
            logger.error(f"Checkpoint for {course_id} not found.")
            return False

        # Real app: call workflow_manager.load_state(checkpoint_path)
        logger.info(f"Restored checkpoint for {course_id}")
        return True

    def verify_state(self, validator_obj: Any, app_state: Any) -> tuple[bool, str]:
        """Runs an IValidator against the app state."""
        try:
            is_valid = validator_obj.validate(app_state)
            return is_valid, "Validation passed" if is_valid else "Validation failed"
        except Exception as e:
            logger.error(f"Validator exception: {e}")
            return False, f"Error running validation: {e}"

    def _emit_step_changed(self) -> None:
        """Notifies the UI overlay that the tutorial state has progressed."""
        self._event_bus.emit(ACADEMY_STEP_CHANGED, self.current_step)

    def get_progress(self, course_id: str) -> float:
        """Returns completion percentage (100.0 if completed)."""
        if course_id in self.completed_courses or any(b.get("id") == course_id for b in self.badges):
            if course_id not in self.completed_courses:
                self.completed_courses.append(course_id)
                self._save_progress()
            return 100.0

        return 0.0

    def is_core_intro_done(self) -> bool:
        """Returns True if the core onboarding tour has been completed."""
        return "core_intro_v1" in self.completed_courses
