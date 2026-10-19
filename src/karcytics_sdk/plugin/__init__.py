"""Plugin Tier — Core classes, UI components, and utilities for Karcytics plugins."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .academy import AcademyManager
    from .academy_driver import AcademyStepDriver, build_academy_overlay, open_academy, show_academy_overlay
    from .academy_lock_card import AcademyLockCard, LockCardContent
    from .academy_window import AcademyCatalogWindow
    from .analysis import AnalysisBase, AnalysisRunnable, AnalysisWorker
    from .base import PluginBase
    from .citation_widgets import CitationLabel, CitationsPanel
    from .citations import Citation, MethodStep
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
        repopulate_combo,
    )
    from .context import PluginContext, UndeclaredCapabilityAccess
    from .course_complete_overlay import CourseCompleteOverlay
    from .cyto_character import CytoWidget
    from .daemon import PluginDaemon, PluginUIDaemon
    from .dialogs import (
        ask_ok_cancel,
        ask_yes_no,
        confirm_destructive,
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
    from .history import HistoryEntry, UndoHistory
    from .image_view import ImageView, array_to_pixmap
    from .interfaces import KarcyticsPlugin
    from .io import PluginConfig, PluginPreferenceManager, load_json, save_json
    from .logging import get_logger
    from .managed_task import FunctionalTask
    from .manifest import PluginManifest
    from .preferences import PreferenceManagerProtocol
    from .rendering import (
        MPL_RASTER_LOCK,
        AxesColors,
        DirtyTrackingGraphicsScene,
        DirtyTrackingGraphicsView,
        RasterizeStage,
        RasterizeToImageTask,
        RasterLock,
        RenderComputeStage,
        RenderData,
        RenderPipelineController,
        style_axes,
    )
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
    from .signals import PluginSignals, move_to_gui_thread, safe_disconnect
    from .state import PluginState
    from .tasks import TaskHandle, submit_task
    from .timing import Debouncer
    from .toast import (
        STYLE_ERROR,
        STYLE_INFO,
        STYLE_SUCCESS,
        STYLE_UPDATE,
        STYLE_WARNING,
        ToastManager,
        ToastNotification,
        show_toast,
        toast_manager,
    )
    from .tutorial_overlay import TutorialOverlay
    from .ui_daemon_runtime import (
        ClosableMainWindow,
        RequestDispatcher,
        patch_macos_bundle_name,
        set_windows_app_user_model_id,
    )
    from .ui_daemon_runtime import run as run_ui_daemon
    from .validation import (
        validate_directory_exists,
        validate_file_exists,
        validate_non_negative,
        validate_not_empty,
        validate_positive,
        validate_value_range,
    )
    from .version import read_plugin_version
    from .wizard import StepIndicator, WizardPanel, WizardStep
    from .worker_thread import OneShotWorkerThread
    from .workflow import WorkflowAttachment, WorkflowContext
    from .zoom_controls import ZoomControls

# Each public name maps to the submodule that defines it. They are imported on
# first access (see ``__getattr__``) so that a light import such as
# ``karcytics_sdk.plugin.manifest_parser`` -- used by the signing and
# license-check CLI -- does not drag in PySide6 widgets, which fail to load on a
# headless Linux runner without system GL libraries (libEGL).
_EXPORTS: dict[str, tuple[str, ...]] = {
    ".academy": ("AcademyManager",),
    ".academy_driver": (
        "AcademyStepDriver",
        "build_academy_overlay",
        "open_academy",
        "show_academy_overlay",
    ),
    ".academy_lock_card": ("AcademyLockCard", "LockCardContent"),
    ".academy_window": ("AcademyCatalogWindow",),
    ".analysis": ("AnalysisBase", "AnalysisRunnable", "AnalysisWorker"),
    ".base": ("PluginBase",),
    ".citation_widgets": ("CitationLabel", "CitationsPanel"),
    ".citations": ("Citation", "MethodStep"),
    ".components": (
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
        "SubtitleLabel",
        "apply_component_style",
        "repopulate_combo",
    ),
    ".context": ("PluginContext", "UndeclaredCapabilityAccess"),
    ".course_complete_overlay": ("CourseCompleteOverlay",),
    ".cyto_character": ("CytoWidget",),
    ".daemon": ("PluginDaemon", "PluginUIDaemon"),
    ".dialogs": (
        "ask_ok_cancel",
        "ask_yes_no",
        "confirm_destructive",
        "get_directory",
        "get_double",
        "get_image_path",
        "get_number",
        "get_save_path",
        "get_text",
        "show_error",
        "show_info",
        "show_warning",
    ),
    ".events": ("CentralEventBus",),
    ".history": ("HistoryEntry", "UndoHistory"),
    ".image_view": ("ImageView", "array_to_pixmap"),
    ".interfaces": ("KarcyticsPlugin",),
    ".io": ("PluginConfig", "PluginPreferenceManager", "load_json", "save_json"),
    ".logging": ("get_logger",),
    ".managed_task": ("FunctionalTask",),
    ".manifest": ("PluginManifest",),
    ".preferences": ("PreferenceManagerProtocol",),
    ".rendering": (
        "MPL_RASTER_LOCK",
        "AxesColors",
        "DirtyTrackingGraphicsScene",
        "DirtyTrackingGraphicsView",
        "RasterizeStage",
        "RasterizeToImageTask",
        "RasterLock",
        "RenderComputeStage",
        "RenderData",
        "RenderPipelineController",
        "style_axes",
    ),
    ".ribbon": ("BioRibbon",),
    ".runtime_services": (
        "DiagnosticsForwarder",
        "KarcyticsEvent",
        "LocalTaskScheduler",
        "RemoteEventBus",
        "diagnostics",
        "event_bus",
        "task_scheduler",
        "tutorial_manager",
    ),
    ".signals": ("PluginSignals", "move_to_gui_thread", "safe_disconnect"),
    ".state": ("PluginState",),
    ".tasks": ("TaskHandle", "submit_task"),
    ".timing": ("Debouncer",),
    ".toast": (
        "STYLE_ERROR",
        "STYLE_INFO",
        "STYLE_SUCCESS",
        "STYLE_UPDATE",
        "STYLE_WARNING",
        "ToastManager",
        "ToastNotification",
        "show_toast",
        "toast_manager",
    ),
    ".tutorial_overlay": ("TutorialOverlay",),
    ".ui_daemon_runtime": (
        "ClosableMainWindow",
        "RequestDispatcher",
        "patch_macos_bundle_name",
        "set_windows_app_user_model_id",
    ),
    ".validation": (
        "validate_directory_exists",
        "validate_file_exists",
        "validate_non_negative",
        "validate_not_empty",
        "validate_positive",
        "validate_value_range",
    ),
    ".version": ("read_plugin_version",),
    ".wizard": ("StepIndicator", "WizardPanel", "WizardStep"),
    ".worker_thread": ("OneShotWorkerThread",),
    ".workflow": ("WorkflowAttachment", "WorkflowContext"),
    ".zoom_controls": ("ZoomControls",),
}
_ALIASES: dict[str, tuple[str, str]] = {
    "run_ui_daemon": (".ui_daemon_runtime", "run"),
}
_LOCATIONS: dict[str, tuple[str, str]] = {
    **{name: (module, name) for module, names in _EXPORTS.items() for name in names},
    **_ALIASES,
    "LayeredMatplotlibCanvas": (".rendering", "LayeredMatplotlibCanvas"),
}

__all__ = [
    # Base and Core
    "PluginBase",
    "PluginDaemon",
    "PluginUIDaemon",
    "PluginSignals",
    "safe_disconnect",
    "move_to_gui_thread",
    "Debouncer",
    "PluginState",
    "ClosableMainWindow",
    "RequestDispatcher",
    "run_ui_daemon",
    "patch_macos_bundle_name",
    "set_windows_app_user_model_id",
    "PluginContext",
    "PluginManifest",
    "UndeclaredCapabilityAccess",
    "AnalysisBase",
    "AnalysisRunnable",
    "AnalysisWorker",
    "OneShotWorkerThread",
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
    "AcademyLockCard",
    "LockCardContent",
    "build_academy_overlay",
    "open_academy",
    "show_academy_overlay",
    "tutorial_manager",
    "TutorialOverlay",
    "CytoWidget",
    "CourseCompleteOverlay",
    "CentralEventBus",
    "HistoryEntry",
    "UndoHistory",
    "PreferenceManagerProtocol",
    # Toasts
    "STYLE_ERROR",
    "STYLE_INFO",
    "STYLE_SUCCESS",
    "STYLE_UPDATE",
    "STYLE_WARNING",
    "ToastManager",
    "ToastNotification",
    "show_toast",
    "toast_manager",
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
    "AxesColors",
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
    "confirm_destructive",
    "repopulate_combo",
    "style_axes",
    "ZoomControls",
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
    "submit_task",
    "read_plugin_version",
    "Citation",
    "MethodStep",
    "CitationLabel",
    "CitationsPanel",
    "ImageView",
    "array_to_pixmap",
    "FunctionalTask",
    "TaskHandle",
]


def __getattr__(name: str) -> object:
    """Import a public name from its submodule on first access.

    Keeps ``import karcytics_sdk.plugin`` cheap and free of hard GUI
    dependencies; ``LayeredMatplotlibCanvas`` additionally needs matplotlib.
    """
    location = _LOCATIONS.get(name)
    if location is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = location
    value = getattr(importlib.import_module(module_name, __name__), attr)
    globals()[name] = value
    return value


def _preload_exports() -> None:
    """Import every eagerly-exported name now, as the old barrel did.

    The UI daemon calls this before its stdin-reader thread starts: on
    Windows, importing native-extension-heavy modules on the main thread
    while that thread is blocked reading stdin can deadlock (see
    ``ui_daemon_runtime.run``). ``LayeredMatplotlibCanvas`` stays lazy, as it
    always was.
    """
    for name in (*(n for names in _EXPORTS.values() for n in names), *_ALIASES):
        __getattr__(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LOCATIONS))
