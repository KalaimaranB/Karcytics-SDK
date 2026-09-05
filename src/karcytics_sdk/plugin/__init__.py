"""Plugin Tier — Core classes, UI components, and utilities for Karcytics plugins."""

from .academy import AcademyManager
from .academy_driver import AcademyStepDriver, build_academy_overlay, open_academy
from .academy_window import AcademyCatalogWindow
from .analysis import AnalysisBase, AnalysisRunnable, AnalysisWorker
from .base import PluginBase
from .components import (
    AcademyButton,
    BioButton,
    BioCancelButton,
    BioCaptionLabel,
    BioComboBox,
    BioDoubleSpinBox,
    BioHeaderLabel,
    BioHelpButton,
    BioLineEdit,
    BioListWidget,
    BioRunButton,
    BioScrollArea,
    BioSpinBox,
    BioSplitter,
    BioStatusLabel,
    BioTableWidget,
    BioToggleButton,
    DangerButton,
    HeaderLabel,
    ModuleCard,
    PrimaryButton,
    SecondaryButton,
    SubtitleLabel,
    apply_component_style,
)
from .context import PluginContext, UndeclaredCapabilityAccess
from .course_complete_overlay import CourseCompleteOverlay
from .cyto_character import CytoWidget
from .daemon import PluginDaemon, PluginUIDaemon
from .dialogs import (
    ask_ok_cancel,
    ask_yes_no,
    get_directory,
    get_double,
    get_image_path,
    get_number,
    get_save_path,
    get_text,
    show_error,
    show_info,
    show_warning,
)
from .events import CentralEventBus
from .interfaces import KarcyticsPlugin
from .io import PluginConfig, PluginPreferenceManager, load_json, save_json
from .logging import get_logger
from .manifest import PluginManifest
from .preferences import PreferenceManagerProtocol
from .rendering import (
    MPL_RASTER_LOCK,
    DirtyTrackingGraphicsScene,
    DirtyTrackingGraphicsView,
    RasterizeStage,
    RasterizeToImageTask,
    RasterLock,
    RenderComputeStage,
    RenderData,
    RenderPipelineController,
)

# LayeredMatplotlibCanvas is lazily resolved via __getattr__ below.
from .ribbon import BioRibbon
from .runtime_services import (
    DiagnosticsForwarder,
    KarcyticsEvent,
    LocalTaskScheduler,
    RemoteEventBus,
    diagnostics,
    event_bus,
    task_scheduler,
    tutorial_manager,
)
from .signals import PluginSignals
from .state import PluginState
from .tutorial_overlay import TutorialOverlay
from .ui_daemon_runtime import ClosableMainWindow, RequestDispatcher
from .ui_daemon_runtime import run as run_ui_daemon
from .validation import (
    validate_directory_exists,
    validate_file_exists,
    validate_non_negative,
    validate_not_empty,
    validate_positive,
    validate_value_range,
)
from .wizard import StepIndicator, WizardPanel, WizardStep
from .workflow import WorkflowAttachment, WorkflowContext

__all__ = [
    # Base and Core
    "PluginBase",
    "PluginDaemon",
    "PluginUIDaemon",
    "PluginSignals",
    "PluginState",
    "ClosableMainWindow",
    "RequestDispatcher",
    "run_ui_daemon",
    "PluginContext",
    "PluginManifest",
    "UndeclaredCapabilityAccess",
    "AnalysisBase",
    "AnalysisRunnable",
    "AnalysisWorker",
    "LocalTaskScheduler",
    "task_scheduler",
    "KarcyticsEvent",
    "RemoteEventBus",
    "event_bus",
    "DiagnosticsForwarder",
    "diagnostics",
    "AcademyManager",
    "AcademyStepDriver",
    "AcademyCatalogWindow",
    "build_academy_overlay",
    "open_academy",
    "tutorial_manager",
    "TutorialOverlay",
    "CytoWidget",
    "CourseCompleteOverlay",
    "CentralEventBus",
    "PreferenceManagerProtocol",
    "get_logger",
    "KarcyticsPlugin",
    # Rendering
    "MPL_RASTER_LOCK",
    "DirtyTrackingGraphicsScene",
    "DirtyTrackingGraphicsView",
    "LayeredMatplotlibCanvas",
    "RasterLock",
    "RasterizeStage",
    "RasterizeToImageTask",
    "RenderComputeStage",
    "RenderData",
    "RenderPipelineController",
    # UI Components
    "AcademyButton",
    "BioButton",
    "BioCancelButton",
    "BioCaptionLabel",
    "BioComboBox",
    "BioDoubleSpinBox",
    "BioHeaderLabel",
    "BioHelpButton",
    "BioLineEdit",
    "BioListWidget",
    "BioRibbon",
    "BioRunButton",
    "BioScrollArea",
    "BioSpinBox",
    "BioSplitter",
    "BioStatusLabel",
    "BioTableWidget",
    "BioToggleButton",
    "DangerButton",
    "HeaderLabel",
    "ModuleCard",
    "PrimaryButton",
    "SecondaryButton",
    "StepIndicator",
    "SubtitleLabel",
    "WizardPanel",
    "WizardStep",
    "apply_component_style",
    # Dialogs
    "ask_ok_cancel",
    "ask_yes_no",
    "get_directory",
    "get_double",
    "get_image_path",
    "get_number",
    "get_save_path",
    "get_text",
    "show_error",
    "show_info",
    "show_warning",
    # I/O & Configuration
    "PluginConfig",
    "PluginPreferenceManager",
    "load_json",
    "save_json",
    # Validation Helpers
    "validate_directory_exists",
    "validate_file_exists",
    "validate_non_negative",
    "validate_not_empty",
    "validate_positive",
    "validate_value_range",
    # Workflows
    "WorkflowAttachment",
    "WorkflowContext",
]


def __getattr__(name: str) -> object:
    """Lazily import matplotlib-dependent names.

    ``LayeredMatplotlibCanvas`` requires ``matplotlib`` and ``PyQt6`` to be
    installed in the calling environment.  Plugins that never use it (e.g.
    text-only or headless plugins) should not be forced to vendor those heavy
    dependencies just because they import ``karcytics_sdk.plugin``.
    """
    if name == "LayeredMatplotlibCanvas":
        from .rendering import LayeredMatplotlibCanvas  # noqa: PLC0415

        return LayeredMatplotlibCanvas
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
