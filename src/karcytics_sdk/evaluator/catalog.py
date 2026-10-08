"""Qt/stdlib constructs that have an SDK equivalent, and what that equivalent is.

This is the evaluator's knowledge of "the SDK already provides this". Adding
a component to the SDK should come with a row here. Only list a Qt class
when the SDK has a real drop-in for it - e.g. there is no SDK checkbox, so
`QCheckBox` is deliberately absent.
"""

from __future__ import annotations

_COMPONENTS = "karcytics_sdk.plugin"
_DIALOGS = "karcytics_sdk.plugin.dialogs"

# Raw Qt widget class -> the SDK component(s) to use instead.
WIDGET_EQUIVALENTS: dict[str, str] = {
    "QPushButton": (
        f"BioButton / PrimaryButton / SecondaryButton / DangerButton "
        f"(BioToggleButton for a checkable button) from {_COMPONENTS}"
    ),
    "QComboBox": f"BioComboBox from {_COMPONENTS}",
    "QSpinBox": f"BioSpinBox from {_COMPONENTS}",
    "QDoubleSpinBox": f"BioDoubleSpinBox from {_COMPONENTS}",
    "QLineEdit": f"BioLineEdit from {_COMPONENTS}",
    "QListWidget": f"BioListWidget from {_COMPONENTS}",
    "QTableWidget": f"BioTableWidget from {_COMPONENTS}",
    "QSplitter": f"BioSplitter from {_COMPONENTS}",
    "QScrollArea": f"BioScrollArea from {_COMPONENTS}",
    "QProgressDialog": "BioProgressDialog from karcytics_sdk.plugin.components",
}

# Raw Qt dialog class -> {static method -> SDK helper}. The "" key is the
# remedy for instantiating the class directly.
DIALOG_EQUIVALENTS: dict[str, dict[str, str]] = {
    "QMessageBox": {
        "": f"show_info / show_warning / show_error / ask_yes_no / ask_ok_cancel from {_DIALOGS}",
        "information": f"show_info() from {_DIALOGS}",
        "about": f"show_info() from {_DIALOGS}",
        "warning": f"show_warning() from {_DIALOGS}",
        "critical": f"show_error() from {_DIALOGS}",
        "question": f"ask_yes_no() or ask_ok_cancel() from {_DIALOGS}",
    },
    "QFileDialog": {
        "getOpenFileName": f"get_image_path() from {_DIALOGS} when selecting an image",
        "getOpenFileNames": f"get_image_paths() from {_DIALOGS} when selecting images",
        "getSaveFileName": f"get_save_path() from {_DIALOGS}",
        "getExistingDirectory": f"get_directory() from {_DIALOGS}",
    },
    "QInputDialog": {
        "getText": f"get_text() from {_DIALOGS}",
        "getInt": f"get_number() from {_DIALOGS}",
        "getDouble": f"get_double() from {_DIALOGS}",
    },
}

# Dialog methods whose SDK helper only covers part of what the Qt call can do
# (the SDK's open-file helpers are image-only), so a hit is advisory.
PARTIAL_DIALOG_METHODS = {"getOpenFileName", "getOpenFileNames"}

# Qt threading primitives a plugin should not drive by hand.
QT_THREAD_CLASSES = {"QThread", "QRunnable", "QThreadPool"}

# Fully-qualified stdlib concurrency primitives.
PY_THREAD_CALLABLES = {
    "threading.Thread",
    "concurrent.futures.ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
    "concurrent.futures.thread.ThreadPoolExecutor",
    "concurrent.futures.process.ProcessPoolExecutor",
    "multiprocessing.Process",
    "multiprocessing.Pool",
}

QT_BINDINGS = ("PyQt6", "PyQt5", "PySide6", "PySide2")
HUB_PACKAGE = "karcytics"
SDK_PACKAGE = "karcytics_sdk"
