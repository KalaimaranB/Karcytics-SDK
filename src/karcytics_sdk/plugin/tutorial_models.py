"""SOLID models and interfaces for the Karcytics Academy tutorial engine.

Vendored from the Hub's `karcytics.core.models.tutorial_models` — these are
pure dataclasses/interfaces with zero dependency on the Hub itself (only
`abc`/`dataclasses`/`typing`), so a plugin building its own Academy course
content can depend on this permanent, real copy directly rather than an
unimportable Hub-internal path. Any plugin author writing course content
imports from here; the Hub's own copy is the authoritative implementation
these mirror.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class IValidator(ABC):
    """Interface for verifying application state."""

    @abstractmethod
    def validate(self, app_state: Any) -> bool:
        """Evaluate the current application state. Returns True if valid."""
        pass


@dataclass
class BaseStep(ABC):
    """Abstract base step for the academy engine."""

    id: str
    text: str
    cyto_emotion: str = "talking"
    cyto_animation: str | None = None
    next_step_id: str | None = None
    target_widget_names: list[str] = field(default_factory=list)
    allow_interaction: bool = False
    allow_scroll: bool = False
    guide_poly: list[tuple[float, float]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    hide_bubble_after_ms: int | None = None
    manual_dismiss_bubble: bool = False


@dataclass
class InfoStep(BaseStep):
    """A generic step that presents information and awaits a 'Next' click."""

    pass


@dataclass
class InteractionStep(BaseStep):
    """Requires the user to interact with a specific UI widget."""

    target_widget_name: str = ""
    event_trigger: str = "clicked"  # The Qt signal to listen for (e.g., 'clicked', 'toggled')
    show_waiting_indicator: bool = False


@dataclass
class VerificationStep(BaseStep):
    """Evaluates an IValidator against the app state before proceeding."""

    validator: IValidator | None = None
    on_success_step_id: str | None = None
    on_fail_step_id: str | None = None
    hide_next_button: bool = False
    max_retries: int = 0


@dataclass
class ActionStep(BaseStep):
    """Executes a callback action and immediately progresses to next_step_id."""

    action: Callable[[Any], None] | None = None  # Receives main_panel as argument


@dataclass
class BranchingStep(BaseStep):
    """Presents options to the user to branch the tutorial logic."""

    options: dict[str, str] = field(default_factory=dict)  # Maps button text to step_id


@dataclass
class SubTask:
    """A single required action within a ForcedInteractionStep."""

    id: str
    instruction: str
    target_widget_name: str
    event_trigger: str = "clicked"
    validator: IValidator | None = None


@dataclass
class ForcedInteractionStep(BaseStep):
    """A step with multiple required sub-tasks.

    User cannot advance until ALL sub-tasks are completed.
    """

    sub_tasks: list[SubTask] = field(default_factory=list)
    auto_advance_when_complete: bool = False


@dataclass
class SubplotCheckStep(BaseStep):
    """Prompts the user to open a specific subplot and.

    confirms they have done so before advancing.
    """

    subplot_target: str = ""  # e.g. "FMO_PE"
    validator: IValidator | None = None


@dataclass
class WaitForEventStep(BaseStep):
    """Auto-advances when a specific KarcyticsEvent fires on the event bus.

    The Next button is hidden; the overlay shows a waiting indicator.
    ``event_name`` must match a ``KarcyticsEvent`` enum member name exactly
    (e.g. ``"PROJECT_LOADED"``, ``"FILE_IMPORTED"``).

    Which "event bus" that is depends on where the course runs, and for an
    isolated plugin it is **not** the Hub's real event bus:

    - In the Hub's own process, ``AcademyManager`` is constructed with
      ``_HubAcademyEventBus``, which subscribes directly against the Hub's
      live ``KarcyticsEvent`` bus — a genuine Hub-only event (say,
      ``PROJECT_LOADED`` firing because the user opened a different
      project) reaches this step correctly, no extra work needed.
    - In an isolated plugin's own process, ``AcademyManager`` is
      constructed with ``_LocalAcademyEventBus``
      (`karcytics_sdk/plugin/runtime_services.py`), which subscribes
      against that process's own local ``CentralEventBus`` — purely
      in-process pub/sub, unrelated to the Hub. A genuine Hub-only event
      never reaches it. `docs/internal/28_Event_Bridging.md`'s
      ``RemoteEventBus``/``event.subscribe``/``dispatch_event`` channel
      *can* carry that event across the process boundary, but nothing
      wires it to this step automatically — a plugin author has to bridge
      the two explicitly, e.g.:

      ```python
      runtime_services.event_bus.subscribe(
          KarcyticsEvent.PROJECT_LOADED,
          lambda payload: CentralEventBus.publish("PROJECT_LOADED", payload),
      )
      ```

      once, before starting a course that uses this step for that topic.
      See `docs/internal/27_Academy_Engine.md`, "Writing a course", for the
      full picture. If your course only waits on something the plugin's
      own code already publishes locally, none of this applies — the gap
      only bites for a genuinely Hub-sourced event.
    """

    event_name: str = ""  # KarcyticsEvent enum member name to wait for


@dataclass
class ConsentStep(BaseStep):
    """Presents a consent dialog or options to the user before proceeding.

    If accepted, triggers on_accept_action and proceeds to on_accept_step_id.
    If declined, triggers on_decline_action and proceeds to on_decline_step_id.
    """

    accept_text: str = "Accept"
    decline_text: str = "Decline"
    on_accept_step_id: str | None = None
    on_decline_step_id: str | None = None


@dataclass
class Course:
    """A collection of polymorphic steps representing a guided tutorial course."""

    id: str
    title: str
    description: str = ""
    estimated_minutes: int = 0
    badge_reward: str | None = None
    badge_icon: str = ""
    prerequisite_course_ids: list[str] = field(default_factory=list)
    steps: list[BaseStep] = field(default_factory=list)

    def get_step(self, step_id: str) -> BaseStep | None:
        """Finds a course step by its identifier.

        Parameters:
                step_id (str): Identifier of the step to find.

        Returns:
                BaseStep | None: The matching step, or `None` if no step has the specified
                identifier.
        """
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_main_path(self) -> list[str]:
        """Returns a list of step IDs representing the 'happy path' of the course."""
        path: list[str] = []
        if not self.steps:
            return path

        visited: set[str] = set()
        current_id: str | None = self.steps[0].id

        while current_id and current_id not in visited:
            path.append(current_id)
            visited.add(current_id)

            step = self.get_step(current_id)
            if not step:
                break

            on_success = getattr(step, "on_success_step_id", None)
            nxt = getattr(step, "next_step_id", None)

            if on_success:
                current_id = on_success
            elif nxt:
                current_id = nxt
            else:
                break

        return path


__all__ = [
    "IValidator",
    "BaseStep",
    "InfoStep",
    "InteractionStep",
    "VerificationStep",
    "ActionStep",
    "BranchingStep",
    "SubTask",
    "ForcedInteractionStep",
    "SubplotCheckStep",
    "WaitForEventStep",
    "ConsentStep",
    "Course",
]
