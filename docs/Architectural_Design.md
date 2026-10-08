# 🧠 Architectural Design & Deep Dives

This document explains the core software engineering principles, design patterns, and cryptographic strategies that govern the **Karcytics SDK**.

---

## 🏛️ 1. Decoupled UI & Engine Execution Boundary

A common anti-pattern in desktop applications is coupling computational logic (e.g. image filters, data modeling, array processing) directly inside graphical classes (e.g. clicking a button calls `numpy` operations inline). This makes the code untestable, prone to freezing the GUI, and locked to a graphical interface.

Karcytics SDK enforces a strict **Decoupled Boundary Model**:

```text
+-----------------------+              +-----------------------+
|  PySide6 Graphical UI   |              | Asynchronous Offscreen|
|  (subclasses Plugin)  |              | (subclasses Analysis) |
|                       |              |                       |
|   - Widget Forms      |              |   - Pure Algorithms   |
|   - Button Styles     | <---State--- |   - Math Computations |
|   - Progress Signals  | ---Engine--> |   - File Operations   |
|                       |              |                       |
+-----------------------+              +-----------------------+
```

### Key Engineering Decisions:
1.  **Pure Business Logic (`AnalysisBase`):** Computational algorithms must be isolated in a subclass of `AnalysisBase`. This subclass knows absolutely nothing about buttons, sliders, widgets, or labels. It is pure Python/NumPy code that takes a state dataclass and returns a results dictionary.
2.  **State-Driven UI (`PluginState`):** All configurations are modeled in serializable dataclasses. The UI binds user changes to these properties. When calculations start, a deep-copy of the state is passed to the engine. This guarantees thread isolation: the engine works on a frozen snapshot of configurations, protecting it from race conditions if the user interacts with inputs mid-calculation.
3.  **Headless Execution Capable:** Because the calculation engines are decoupled from PySide6 widgets, they can be run inside headless command-line pipelines (such as automated CI servers or high-performance computing clusters) without initializing a desktop display environment!

---

## 🧪 2. Headless Graphical Unit Testing

Testing graphical user interfaces (GUIs) usually requires a display server (X11, Wayland, or native macOS Quartz). This often causes standard automated CI runners to crash or hang indefinitely with errors like `cannot connect to X server`.

Karcytics SDK solves this by orchestrating a **Headless Mocking Architecture**:

```text
[PyTest Runner] -> [QApplication Fixture] -> [Headless Mode Enabled] -> [Mock Prefs Injected]
```

### Implementation Highlights (`tests/conftest.py`):
1.  **Global QApplication Lifecycle:** We initialize a single, persistent `QApplication` instance at the start of pytest execution and keep it alive across all test classes. This avoids the overhead of spawning and destroying multiple event loops.
2.  **No Window Visibility:** Tests instantiate widgets and programmatically trigger signals (e.g. simulating button clicks via `widget.click()`), but they never call `.show()`. The widgets remain offscreen in a lightweight headless memory state.
3.  **Preference Injection (DIP):** Instead of writing preferences directly to user home files during testing, we inject a whitelisted memory mock implementing `PreferenceManagerProtocol`. This keeps the local environment completely clean and guarantees that tests run in a sandbox.

---

## 🧹 3. RAII & Clean Up Mechanics

Scientific applications often load massive data files (high-resolution microscopy images, cytometry binary arrays) directly into RAM. If a plugin panel is closed by a user but background worker threads remain active, it creates immediate memory leaks and orphan threads.

Karcytics SDK uses **Resource Acquisition Is Initialization (RAII)** patterns:
1.  **Thread Pool Binding:** Asynchronous worker threads are registered inside a centralized `QThreadPool` on the parent plugin object.
2.  **Automated Disconnection:** When a panel is closed, the destructor (`__del__` or `closeEvent`) is triggered. The framework automatically signals any running background engines to cancel execution (`engine.cancel()`), waits for threads to exit safely, and releases file handles.
3.  **No Dangling Pointers:** We enforce standard PySide6 C++ pointer validation checks. Before any signal is emitted by background threads, the worker verifies that the graphical receiver object has not been deleted by the Qt garbage collector, preventing common `RuntimeError: wrapped C/C++ object has been deleted` crashes.
