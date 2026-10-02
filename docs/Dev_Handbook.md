# 📖 Karcytics Developer Handbook

Welcome to the comprehensive guide for building custom plugins in the Karcytics ecosystem! This handbook provides deep practical guides and design patterns to help you build responsive, multi-threaded, and professional-grade scientific widgets.

---

## 🏛️ Essential File Layout

All Karcytics plugins must follow a structured directory layout. Let's look at the standard layout (using the production-grade `hello_world` sandbox template as a blueprint):

```text
my_plugin/
├── pyproject.toml        # Declarative meta details, dependencies, and configuration
├── src/
│   ├── __init__.py       # Package entry point and analysis logic
│   └── ui.py             # Core QWidget GUI logic subclassing PluginBase
└── docs/
    └── 01_getting_started.md # Plugin specific documentation
```

### 1. Declarative Metadata (`pyproject.toml`)
The V2 architecture uses a split-manifest approach, storing the plugin configuration inside the standard Python `pyproject.toml`:
```toml
[project]
name = "my-discovery-tool"
version = "1.0.0"
description = "Custom high-throughput gel imaging normalization module."
requires-python = ">=3.11"
authors = [
    { name = "Dr. Scientist" }
]

[tool.karcytics.plugin]
id = "my_plugin"
min_core_version = "1.4.9"
entry_point = "my_plugin:initialize"
```

### 2. Entry Point Connection (`__init__.py`)
The host application uses standard lazy-loading. It imports `__init__.py` and calls `get_panel_class()` to dynamically resolve the widget constructor:
```python
"""Entry point for my_plugin."""

from PyQt6.QtWidgets import QWidget

__version__ = "1.0.0"
__plugin_id__ = "my_plugin"


def get_panel_class() -> type[QWidget]:
    """Return the main QWidget class for Karcytics integration."""
    from .ui import MyFirstPanel

    return MyFirstPanel
```

---

## 🏗️ Core SDK Frameworks

### 1. State Modeling (`PluginState`)
In Karcytics, all dynamic UI choices (like sliders, spinners, checkboxes, and input texts) are tracked inside a dedicated `@dataclass` extending `PluginState`. This decouples the widget state from individual widget properties.

```python
from dataclasses import dataclass
from karcytics_sdk.plugin import PluginState


@dataclass
class NormalizationState(PluginState):
    """Scientific configuration variables for the normalization panel."""

    threshold: float = 0.5
    filter_type: str = "Gaussian"
    user_notes: str = ""
```

### 2. The UI Controller (`PluginBase`)
The primary UI widget class must subclass `PluginBase`. By inheriting from `PluginBase`, you automatically gain:
*   **Automatic Theme Adaptation:** Refreshes and repaints stylesheets dynamically.
*   **Undo/Redo History Checkpoints:** Call `self.push_state("Change Threshold")` after each completed user action. The history lives in your plugin's own process (`karcytics_sdk.plugin.UndoHistory`), and the isolated window's **Edit → Undo/Redo** (Cmd/Ctrl+Z, Cmd+Shift+Z / Ctrl+Y) are wired to it automatically. A plugin with its own state store can call `self.bind_undo_history(history, restore)` instead.
*   **Decoupled Worker Factories:** Create non-blocking calculations via `self.create_worker()`.

```python
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QVBoxLayout, QLabel, QLineEdit
from karcytics_sdk.plugin import PluginBase
from karcytics_sdk.plugin.components import PrimaryButton


class MyFirstPanel(PluginBase):
    def __init__(self, parent=None):
        super().__init__(plugin_id="my_plugin", parent=parent)
        self.state = NormalizationState()

        layout = QVBoxLayout(self)
        self.input = QLineEdit()
        self.btn = PrimaryButton("Apply Filter")

        layout.addWidget(QLabel("Notes:"))
        layout.addWidget(self.input)
        layout.addWidget(self.btn)

        # Connect slots
        self.input.textChanged.connect(self.on_notes_changed)
        self.btn.clicked.connect(self.on_apply)

    @pyqtSlot(str)
    def on_notes_changed(self, text: str) -> None:
        self.state.user_notes = text

    @pyqtSlot()
    def on_apply(self) -> None:
        # Commit checkpoint to the undo/redo stack
        self.push_state()
        print(f"Applied normalizations! Notes: {self.state.user_notes}")
```

### 3. Concurrency Separation (`AnalysisBase`)
**CRITICAL RULE:** Never block the main UI thread with heavy calculations (e.g., NumPy loops, server REST fetches). Any work taking longer than **100ms** must run in a background worker thread.

To do this:
1.  Subclass `AnalysisBase` to isolate your mathematical algorithms.
2.  Implement `run(self, state) -> dict`.

```python
from typing import Any
from karcytics_sdk.plugin import AnalysisBase, PluginState


class NormalizationEngine(AnalysisBase):
    def __init__(self):
        super().__init__(plugin_id="my_plugin")

    def run(self, state: PluginState | None = None) -> dict[str, Any]:
        # Heavy computation occurs here, offscreen!
        # Frequently check if cancellation has been requested:
        if self.is_cancelled():
            return {"status": "cancelled"}

        # Emit progress to the UI
        self.signals.analysis_progress.emit(50)

        return {"status": "completed", "mean_deviation": 0.045}
```

Dispatched using `PluginBase` helpers:
```python
# Inside your UI panel class:
engine = NormalizationEngine()
worker = self.create_worker(engine, self.state)

# Bind worker signal hooks BEFORE dispatch, so no early emission is missed
worker.progress.connect(self.on_progress)
worker.finished.connect(self.on_success)
worker.error.connect(self.on_failure)

# Dispatches to standard QThreadPool
self.start_worker(worker)
```

---

## 💾 Local Configurations (`PluginConfig`)
To save user preferences (like paths, recent files, or hardware selections) between application sessions, use `PluginConfig`. It serializes variables to a secure JSON file at `~/.karcytics/plugin_configs/{plugin_id}.json`.

```python
from karcytics_sdk.plugin import PluginConfig


class MyPreferences:
    def __init__(self):
        self.config = PluginConfig("my_plugin")

    def load_gpu_enabled(self) -> bool:
        return self.config.get("use_gpu", False)

    def save_gpu_enabled(self, enabled: bool) -> None:
        self.config.set("use_gpu", enabled)
        self.config.save()  # Flushes to local disk JSON
```

---

## 🎨 Theme Guidelines & Semantic Components
Karcytics handles full HSL color themes. To ensure your custom widgets look stunning in both light and dark mode layouts:
1.  **Never hardcode styling:** Avoid styling components with raw hex values (e.g. `background: white` or `color: black`).
2.  **Use Semantic Components:** Inherit buttons from `PrimaryButton` or `SecondaryButton` to get premium styling, outline curves, and glowing hover states automatically.
3.  **Implement `_apply_theme_styles`:** Override this method inside your custom widgets to dynamically recolor or repaint graphic assets when theme change events fire.
