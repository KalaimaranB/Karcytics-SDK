"""Reusable worker-side runtime for hosting a plugin's UI in its own process.

Run by `karcytics_sdk.plugin.PluginUIDaemon` from a plugin's own `.venv`
interpreter, never imported into the Hub's process. A plugin's own
`ui_daemon.py` should do whatever plugin-specific setup it needs (sys.path,
PluginContext construction, entry-point initialization) and then hand a
zero-arg panel factory to `run()` — everything from that point on (frame
transport, the ready handshake, request dispatch, and noticing a native
window close) is identical for every isolated plugin and lives here once.

Speaks the same length-prefixed msgpack framing as `PluginDaemon`'s worker
side, with `PluginUIDaemon`'s kind-tagged frames ({"kind": "request" |
"response" | "event"}) since this process pushes events to the Hub
unprompted (window_closed, theme acks) rather than only ever answering a
call — see `daemon.py`'s `PluginUIDaemon` docstring for the Hub-side half of
this protocol.
"""

from __future__ import annotations

import os
import struct
import sys
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

import msgpack
from PyQt6.QtCore import QMetaObject, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget

from .logging import get_logger


def write_frame(data: dict[str, Any]) -> None:
    """Write a length-prefixed msgpack frame to stdout."""
    payload = msgpack.packb(data, use_bin_type=True)
    header = struct.pack(">I", len(payload))
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()


def _read_exact_stdin(num_bytes: int) -> bytes | None:
    """Read exactly `num_bytes` from stdin's raw file descriptor, or `None` on EOF.

    Deliberately bypasses `sys.stdin.buffer` — an `io.BufferedReader` — and
    the object lock it holds for the duration of any in-flight `.read()`
    call. `_RequestReader._loop()` runs this on a background thread that's
    blocked here for as long as the Hub has nothing new to say, which on
    Windows deadlocks any *other* thread that imports a native-extension-
    heavy module (numpy, matplotlib's Qt backend, ...) while that lock is
    held — reproduced live: both hung indefinitely under this exact
    condition (a background thread mid-read on `sys.stdin.buffer` while the
    main thread imported one of them), with no such hang on macOS, and no
    amount of extra timeout budget ever resolved it. `os.read()` on the raw
    fd talks to the OS pipe directly and never touches that Python-level
    lock, so it can't contend with an unrelated import on another thread
    regardless of what that import happens to be or when it runs — unlike
    pre-importing every module Phase 2 might ever need before this reader
    starts, which doesn't scale and reintroduces the exact Ready-Gate-
    blocking problem Phase 2 exists to avoid.
    """
    fd = sys.stdin.fileno()
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = os.read(fd, num_bytes - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def read_frame() -> dict[str, Any] | None:
    """Read a length-prefixed msgpack frame from stdin, or None on EOF."""
    header = _read_exact_stdin(4)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    payload = _read_exact_stdin(length)
    if payload is None:
        return None
    return msgpack.unpackb(payload, raw=False)


def send_event(topic: str, payload: Any = None) -> None:
    """Push an unsolicited event frame to the Hub (window_closed, state changes, ...)."""
    write_frame({"kind": "event", "topic": topic, "payload": payload})


class RequestDispatcher:
    """Maps request method names to handlers and turns their result (or a
    raised exception) into a response payload — the piece of the protocol
    that has nothing to do with stdio or Qt, so it's tested without either.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register(self, method: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        """Register (or replace) the handler for a request method name."""
        self._handlers[method] = handler

    def dispatch(self, frame: dict[str, Any]) -> Any:
        """Run the handler registered for `frame["method"]` and return its
        result, or an `{"error": ...}` payload if the method is unknown or
        the handler raised.
        """
        method = frame.get("method")
        kwargs = frame.get("kwargs", {})

        if not isinstance(method, str) or method not in self._handlers:
            return {"error": f"Unknown method '{method}'"}

        handler = self._handlers[method]

        try:
            return handler(kwargs)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{exc}\n{traceback.format_exc()}"}


class ClosableMainWindow(QMainWindow):
    """A `QMainWindow` that tells the Hub when the user closes it natively.

    The Hub can only track an isolated module's lifecycle through what this
    process tells it — there is no shared state to poll. A close initiated
    by the *user* (clicking the OS window's close button) has to be reported
    proactively via `window_closed`; a close initiated *by the Hub* (an
    `exit`/`close_requested` request) is already communicated through that
    request's own response, so `close_without_notifying_hub()` skips the
    callback to avoid telling the Hub the same thing twice.
    """

    def __init__(self, on_close: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_manager: Any | None = None
        self._on_close = on_close
        self._suppress_close_callback = False
        self._close_notified = False
        self._overlay: QWidget | None = None

    def close_without_notifying_hub(self) -> bool:
        """Close as a direct consequence of a Hub request, not a user action."""
        self._suppress_close_callback = True
        return self.close()

    def set_overlay(self, widget: QWidget | None) -> None:
        """Track a full-window overlay (the startup loading screen) so it
        stays sized to the central widget's content area across resizes,
        the same way the Hub's own loading overlay tracks its stack widget.
        """
        self._overlay = widget
        self._sync_overlay_geometry()

    def _sync_overlay_geometry(self) -> None:
        if self._overlay is not None and self.centralWidget() is not None:
            self._overlay.setGeometry(self.centralWidget().rect())

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_overlay_geometry()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        # Qt doesn't delete a widget on close() by default, so a second
        # close() (e.g. the Hub's `exit` request racing a user click) would
        # otherwise fire closeEvent — and this callback — a second time for
        # the same window.
        if not self._suppress_close_callback and not self._close_notified:
            self._close_notified = True
            self._on_close()
        super().closeEvent(event)


class _RequestBridge(QObject):
    """Cross-thread hop from the stdin reader thread onto the Qt GUI thread.

    Request handlers (`focus`, `theme_changed`, closing the window, ...)
    touch Qt widgets, which is only safe from the thread that owns them.
    `pyqtSignal` delivery is thread-safe and automatically queues onto the
    thread that owns the connected slot's `QObject` — since this bridge is
    constructed on the GUI thread inside `run()`, emitting `request_received`
    from the reader thread delivers the frame there instead of running the
    handler on the reader thread directly, which is what let an earlier
    version of this module deadlock on `window.close()` called off-thread.
    """

    request_received = pyqtSignal(dict)


class _RequestReader:
    """Background thread draining stdin and handing frames to `_RequestBridge`.

    Runs on its own thread so a slow/blocking request handler never stalls
    reading the next frame — mirrors `PluginUIDaemon`'s reader thread on the
    Hub side, just for the opposite direction of traffic.
    """

    def __init__(self, bridge: _RequestBridge, logger: Any) -> None:
        self._bridge = bridge
        self._logger = logger
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                frame = read_frame()
            except Exception:
                # The 4-byte length header was read successfully (frame
                # boundaries are intact), but its payload didn't unpack —
                # the stream itself isn't desynced, only this one frame's
                # content is garbage, so logging and reading the next frame
                # is safe rather than fatal (mirrors PluginUIDaemon's own
                # `_reader_loop` on the Hub side).
                self._logger.warning(
                    "ui_daemon_runtime received a malformed frame.",
                    extra={"log_event": "malformed_frame"},
                )
                continue
            if frame is None:
                # Hub process is gone; there is no one left to talk to.
                self._logger.info("Hub stdin pipe closed; exiting.", extra={"log_event": "stdin_closed"})
                os._exit(0)  # noqa: SLF001
            self._bridge.request_received.emit(frame)


def _format_about_karcytics(info: dict[str, str]) -> str:
    return (
        f"<h3>{info.get('name', 'Karcytics')}</h3>"
        f"<p>Version {info.get('version', 'unknown')}</p>"
        f"<p><b>{info.get('tagline', '')}</b></p>"
        f"<p>{info.get('description', '')}</p>"
        f"<p>{info.get('copyright', '')}</p>"
    )


def _format_about_developer(info: dict[str, str]) -> str:
    bio_paragraphs = "".join(f"<p>{p}</p>" for p in info.get("bio", "").split("\n\n"))
    return f"<h3>{info.get('name', '')}</h3><p>{info.get('role', '')}</p>{bio_paragraphs}"


def _show_fetched_about(  # noqa: PLR0913, PLR0917
    window: QMainWindow,
    client: Any,
    logger: Any,
    method: str,
    title: str,
    formatter: Callable[[dict[str, str]], str],
) -> None:
    """Fetch an About payload from the Hub and show it, or a plain warning
    if the Hub can't be reached — this menu item existing at all shouldn't
    imply CoreServices is guaranteed reachable at click time.
    """
    try:
        info = client.call(method)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch {method} from the Hub.",
            extra={"log_event": "about_fetch_failed", "method": method, "error": str(exc)},
        )
        QMessageBox.warning(window, title, "Could not reach the Hub for this information.")
        return
    QMessageBox.about(window, title, formatter(info or {}))


def _open_preferences(window: QMainWindow, client: Any) -> None:
    from .ui_preferences import SDKPreferencesDialog

    dialog = SDKPreferencesDialog(window, client)

    # Give the plugin a chance to populate preferences if it wants to
    panel = getattr(window, "wizard_panel", None)
    if panel is None:
        panel = window.centralWidget()
    if hasattr(panel, "populate_preferences"):
        panel.populate_preferences(dialog)
    else:
        # Just an empty state if no plugin preferences exist, or we can add Theme later
        pass

    dialog.exec()


def _build_menu_bar(window: QMainWindow, logger: Any) -> None:
    """Give the isolated window its own menu bar using StandardMenuBuilder."""
    from karcytics_sdk.plugin.menu_builder import StandardMenuBuilder

    builder = StandardMenuBuilder(window)

    close_action = QAction("&Close Window", window)
    close_action.triggered.connect(window.close)
    builder.add_file_menu([close_action])

    port = os.environ.get("KARCYTICS_CORE_SERVICES_PORT")
    token = os.environ.get("KARCYTICS_CORE_SERVICES_TOKEN")

    if port and token:
        from karcytics_sdk.host.core_services import CoreServicesClient

        client = CoreServicesClient(int(port), token=token)

        # Edit -> Preferences
        builder.add_edit_menu(pref_cb=lambda: _open_preferences(window, client))

        # View -> Theme
        try:
            categorized_themes = client.call("theme.get_categorized_themes")
            builder.add_theme_menu(
                switch_theme_cb=lambda path: client.call("theme.switch_theme", {"theme_path": str(path)}),
                categorized_themes=categorized_themes,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch categorized themes from core: {e}")

        # Help
        # The Academy action needs to be added manually after the builder finishes
        help_menu = builder.add_help_menu(
            docs_cb=lambda: _show_fetched_about(
                window, client, logger, "menu.get_about_karcytics", "About Karcytics", _format_about_karcytics
            ),
            # In SDK there was no docs_cb/wiki_cb originally, wait!
            # The original _build_help_menu only had "About Karcytics" and "About the Developer".
            about_cb=lambda: _show_fetched_about(
                window, client, logger, "menu.get_about_karcytics", "About Karcytics", _format_about_karcytics
            ),
            about_dev_cb=lambda: _show_fetched_about(
                window, client, logger, "menu.get_about_developer", "About the Developer", _format_about_developer
            ),
            mac_no_role=True,
        )

        academy_action = QAction("🎓 Academy", window)
        academy_action.setMenuRole(QAction.MenuRole.NoRole)
        help_menu.addAction(academy_action)
        window._academy_menu_action = academy_action  # type: ignore[attr-defined]


def _wire_academy_menu(window: QMainWindow, panel: QWidget, logger: Any) -> None:
    """Connects the Help menu's "🎓 Academy" action, built earlier by
    `_build_help_menu()`, once `panel` actually exists.

    Delegates to `academy_driver.open_academy()` — the same shared entry
    point a plugin's own in-panel triggers (e.g. `components.AcademyButton`)
    use, so the Help menu and any toolbar button stay in sync over one
    `TutorialOverlay`/`AcademyStepDriver` pair cached on this window.
    """
    action = getattr(window, "_academy_menu_action", None)
    if action is None:
        return

    from .academy_driver import open_academy

    action.triggered.connect(lambda: open_academy(window, panel))
    logger.debug("Academy menu wired.", extra={"log_event": "academy_menu_wired"})


def _fail_theme_gate(logger: Any, plugin_id: str, message: str) -> None:
    """Report a fatal, diagnosable error to the Hub and terminate — never
    silently render with a guessed palette.

    A themed window built from `theme_fallback`'s hardcoded DARK/LIGHT
    palette would *look* fine while quietly diverging from whatever the
    Hub's user actually has configured (a differently-branded theme, a
    custom accent, ...); nothing about that failure would ever surface. This
    process has no UI to show an error in, so it reports through
    `CoreServicesServer`'s own `diagnostics.report_error` RPC (the same path
    an in-process plugin's own errors already take to the Hub's error
    dialog) when reachable, then exits without ever calling `panel_factory()`
    — no window, themed or otherwise, gets built on this path.
    """
    logger.critical(message, extra={"log_event": "theme_gate_failed"})

    port = os.environ.get("KARCYTICS_CORE_SERVICES_PORT")
    token = os.environ.get("KARCYTICS_CORE_SERVICES_TOKEN")
    if port and token:
        from karcytics_sdk.host.core_services import CoreServicesClient

        try:
            CoreServicesClient(int(port), token=token).call(
                "diagnostics.report_error", message=message, plugin_id=plugin_id, fatal=True
            )
        except Exception:  # noqa: BLE001
            logger.critical(
                "Also failed to report the theme gate failure to the Hub.",
                extra={"log_event": "theme_gate_report_failed"},
            )

    os._exit(1)


def _confirm_hub_theme_or_exit(logger: Any, plugin_id: str) -> None:
    """Block until the Hub's real current colors are confirmed, applying them
    to `theme_fallback.DynamicColors` before any widget is built.

    Deliberately synchronous and gating, not best-effort: a theme mismatch
    between this window and the Hub is a real regression, not cosmetic, and
    `theme_fallback`'s DARK/LIGHT palettes existing at all means a broken
    confirmation would otherwise fail *silently* — the window would still
    render, just wrong. Refusing to build one at all keeps that failure loud
    instead.
    """
    port = os.environ.get("KARCYTICS_CORE_SERVICES_PORT")
    token = os.environ.get("KARCYTICS_CORE_SERVICES_TOKEN")
    if not port or not token:
        _fail_theme_gate(
            logger,
            plugin_id,
            "CoreServices is not configured (no KARCYTICS_CORE_SERVICES_PORT/"
            "KARCYTICS_CORE_SERVICES_TOKEN) — cannot confirm the Hub's real theme.",
        )
        return

    from karcytics_sdk.host.core_services import CoreServicesClient

    client = CoreServicesClient(int(port), token=token)
    try:
        colors = client.call("theme.get_current_colors")
    except Exception as exc:  # noqa: BLE001
        _fail_theme_gate(logger, plugin_id, f"Failed to fetch the Hub's current theme: {exc}")
        return

    if not colors:
        _fail_theme_gate(logger, plugin_id, "Hub returned no theme colors.")
        return

    from .theme_fallback import DynamicColors

    DynamicColors.update_from(colors)


def _fetch_project_manager(logger: Any) -> Any | None:
    """Best-effort fetch of the Hub's currently open project, wrapped so the
    panel can use it exactly like the in-process case did via
    `self.window().project_manager` (see `RemoteProjectManager`).

    Unlike `_confirm_hub_theme_or_exit`, this is never fatal: "no project
    open" (or CoreServices being briefly unreachable) is already a state
    every caller in the panel handles as its own standalone fallback, so a
    failure here should degrade to that same `None`, not take the window
    down.
    """
    port = os.environ.get("KARCYTICS_CORE_SERVICES_PORT")
    token = os.environ.get("KARCYTICS_CORE_SERVICES_TOKEN")
    if not port or not token:
        return None

    from karcytics_sdk.host.core_services import CoreServicesClient, fetch_remote_project_manager

    client = CoreServicesClient(int(port), token=token)
    try:
        return fetch_remote_project_manager(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to fetch the Hub's active project.",
            extra={"log_event": "project_fetch_failed", "error": str(exc)},
        )
        return None


def _reveal_panel_behind_loader(loader: Any, panel: QWidget, window: ClosableMainWindow) -> None:
    """Warp-out then fade-out `loader`, revealing `panel` underneath —
    mirrors the Hub's own `PluginLoaderManager.crossfade_to_analysis()`
    choreography, just run inside this window instead of the Hub's.

    Waits for `panel.data_ready` when the panel implements the same Ready
    Gate protocol the Hub's in-process panels already use (`panel_ready`/
    `data_ready`/`begin_async_init`) — Phase 2's real completion, not merely
    Phase 1's skeleton. A panel that doesn't implement it has nothing worth
    masking construction of, so the loader warps out immediately instead.
    """

    def _on_warp_out_finished() -> None:
        loader.fade_out(500)

    def _on_fade_out_finished() -> None:
        window.set_overlay(None)
        loader.setParent(None)
        loader.deleteLater()

    loader.warp_out_finished.connect(_on_warp_out_finished)
    loader.fade_out_finished.connect(_on_fade_out_finished)

    if hasattr(panel, "panel_ready"):
        panel.panel_ready.connect(lambda: loader.set_status_message("Rendering workspace…"))

    if hasattr(panel, "data_ready"):
        panel.data_ready.connect(loader.warp_out)
    else:
        loader.warp_out()


def run(  # noqa: C901, PLR0913, PLR0915
    panel_factory: Callable[[], QWidget],
    *,
    window_title: str = "Karcytics Module",
    window_size: tuple[int, int] = (1400, 900),
    extra_handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    configure_menus: Callable[[QMainWindow, QWidget], None] | None = None,
    on_panel_ready: Callable[[QMainWindow, QWidget], None] | None = None,
    plugin_id: str = "unknown",
) -> None:
    """Host `panel_factory()`'s widget as a standalone top-level window in
    this process, speaking `PluginUIDaemon`'s protocol over stdio until the
    Hub asks this process to exit, the user closes the window, or the Hub's
    stdin pipe closes.

    Built-in request handlers: `exit` / `close_requested` (closes the
    window and quits), `theme_changed` (calls the panel's
    `_apply_theme_styles()` if it has one), `focus` (raises and activates
    the window), `inject_workflow` (calls `panel.load_workflow`).
    Pass `extra_handlers` for anything plugin-specific
    rather than forking this function.

    The menu bar this builds (File / Theme / Help — see `_build_menu_bar`)
    is the same for every isolated plugin; different plugins wanting
    different menus is what `configure_menus` is for. It's called once,
    after `panel_factory()` has already run, as
    `configure_menus(window, panel)` — deliberately *not* folded into
    `_build_menu_bar()`, which runs before the panel exists, specifically so
    a plugin can wire its own `QMenu`/`QAction`s straight to real panel
    methods (`window.menuBar().addMenu("&Analysis")`, actions calling
    `panel.run_umap()`, etc.) instead of reaching for indirection to work
    around the panel not existing yet.

    `on_panel_ready` is the same shape as `configure_menus` (called once,
    right after it, as `on_panel_ready(window, panel)`) for plugin startup
    logic that isn't about menus — e.g. a plugin auto-starting its own local
    Academy course when the Hub hands off part of an onboarding tour to it
    (see `karcytics_plugins.flow_cytometry.ui_daemon`'s use of this to detect
    `KARCYTICS_ACADEMY_HANDOFF`). Kept separate from `configure_menus` rather
    than folded into it so that hook's contract stays literally "menus".

    Also sets `window.project_manager` to a `RemoteProjectManager` for the
    Hub's currently open project (or `None`), fetched once at startup —
    see `_fetch_project_manager`.

    `plugin_id` tags every log record this runtime emits — pass the real
    plugin id from `ui_daemon.py` so a request that hangs or errors can be
    attributed to which isolated process it came from; it defaults to
    "unknown" only so this function keeps working for a caller that hasn't
    been updated yet, not because "unknown" is an acceptable steady state.
    """
    from .logging import configure_plugin_logging

    configure_plugin_logging(plugin_id)
    send_event("loading_progress", {"message": "Bootstrapping isolated environment…"})

    logger = get_logger(__name__, plugin_id)
    _confirm_hub_theme_or_exit(logger, plugin_id)
    app = QApplication.instance() or QApplication(sys.argv)
    send_event("loading_progress", {"message": "Initializing UI framework…"})

    # `components.py` tries to do this at import time, but that import
    # (via this module's own `from karcytics_sdk.plugin import
    # run_ui_daemon`) happens before `QApplication` exists for an isolated
    # plugin, so it silently no-ops there — see `apply_global_sdk_styles()`'s
    # docstring. Native, app-level-only-stylable popups (QToolTip, a plain
    # QComboBox's dropdown) would otherwise render unstyled for the whole
    # session unless the user happened to switch themes afterward.
    from .components import apply_global_sdk_styles

    apply_global_sdk_styles()

    def _notify_window_closed() -> None:
        logger.info("Native window close.", extra={"log_event": "window_closed"})

        from karcytics_sdk.plugin.runtime_services import tutorial_manager

        if (
            tutorial_manager.active_course
            and getattr(tutorial_manager.current_step, "id", None) == "handoff_return_home"
        ):
            send_event("academy_handoff_complete", {})

        send_event("window_closed", {})

    window = ClosableMainWindow(on_close=_notify_window_closed)
    window.setWindowTitle(window_title)
    window.resize(*window_size)
    _build_menu_bar(window, logger)
    # Set before panel_factory() below so the panel can read it from its own
    # __init__ or constructor path, not only from later user-triggered
    # handlers — same attribute name and shape as WorkspaceWindow's own
    # in-process `project_manager`, so existing `self.window().project_manager`
    # call sites need no changes at all.
    window.project_manager = _fetch_project_manager(logger)

    # The loader stands in for the real panel until it's built — window.show()
    # and "ready" don't wait on panel_factory() below (see that block's own
    # comment for why not), so there needs to be *something* to show the
    # instant the window appears. GalacticLoader renders on its own scene-
    # graph thread, so it stays smooth even while this process's main thread
    # is blocked importing heavy analysis libraries during Phase 1/2.
    from .galactic_loader import GalacticLoader

    loader = GalacticLoader()
    loader.set_module(window_title)
    # setCentralWidget() alone should be enough for QMainWindow's own
    # layout to size its central widget automatically — window.set_overlay()
    # is for *after* the swap below, once loader is a plain floating child
    # no longer under that layout's control, and calling it this early set
    # loader's geometry from centralWidget().rect() before the window had
    # ever been shown or laid out (a tiny default rect). But QQuickWidget
    # wraps its own offscreen QQuickWindow/render target, which has been
    # observed to keep whatever size it had at construction (a default
    # ~100x30 QWidget size) through the *first* paint if the only resize it
    # ever receives is an implicit one from QMainWindow's deferred layout
    # pass rather than a direct resize() call — pinning the animation to a
    # small box in the corner. An explicit resize() here to the window's own
    # (already-set, non-default) size removes that dependency on layout
    # timing entirely.
    loader.resize(window.size())
    window.setCentralWidget(loader)

    panel: QWidget | None = None

    def _handle_close_request(_kwargs: dict[str, Any]) -> dict[str, Any]:
        window.close_without_notifying_hub()
        QMetaObject.invokeMethod(app, "quit")
        return {"status": "ok"}

    dispatcher = RequestDispatcher()
    dispatcher.register("exit", _handle_close_request)
    dispatcher.register("close_requested", _handle_close_request)

    def _handle_theme_changed(kwargs: dict[str, Any]) -> dict[str, Any]:
        colors = kwargs.get("colors")
        if colors:
            from .theme_fallback import DynamicColors, theme_manager

            DynamicColors.update_from(colors)
            theme_manager._apply_dynamic_styles()
            theme_manager.theme_changed.emit()
        if hasattr(panel, "_apply_theme_styles"):
            panel._apply_theme_styles()
        return {"status": "ok"}

    dispatcher.register("theme_changed", _handle_theme_changed)

    def _handle_focus(_kwargs: dict[str, Any]) -> dict[str, Any]:
        window.raise_()
        window.activateWindow()
        return {"status": "ok"}

    dispatcher.register("focus", _handle_focus)

    def _invoke_load_workflow(payload: Any, filename: Any, metadata: Any) -> None:
        import inspect

        sig = inspect.signature(panel.load_workflow)
        call_kwargs = {}
        if "filename" in sig.parameters:
            call_kwargs["filename"] = filename
        if "metadata" in sig.parameters:
            call_kwargs["metadata"] = metadata
        panel.load_workflow(payload, **call_kwargs)

    def _do_workflow_load(payload: Any, filename: Any, metadata: Any) -> None:
        # This runs on the next event-loop tick (QTimer.singleShot(0, ...)
        # below), well after this handler has already written its
        # {"status": "ok"} response — so a raise here has no request
        # in flight to carry an error back to the Hub. Qt's default
        # excepthook would otherwise just print it to this process's
        # stderr, which the Hub only surfaces as an opaque
        # `worker_stderr`-tagged log line (see docs/internal/24, "What
        # actually crosses the process boundary"). send_event() gives
        # the Hub a structured, attributable signal instead — the same
        # reasoning that makes `panel_data_ready` below worth forwarding
        # rather than leaving success silent too.
        try:
            if os.environ.get("KARCYTICS_PENDING_WORKFLOW") == "1":
                # Emulate Phase 2 workflow injection by staging the payload
                panel._deferred_workflow_payload = payload
                if filename is not None:
                    panel._deferred_workflow_filename = filename
                if metadata is not None:
                    panel._deferred_workflow_metadata = metadata

                if hasattr(panel, "begin_async_init"):
                    panel.begin_async_init()
                elif hasattr(panel, "load_workflow"):
                    panel.load_workflow(payload)
            # Dynamic load if the module is already running
            elif hasattr(panel, "load_workflow"):
                _invoke_load_workflow(payload, filename, metadata)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "inject_workflow's deferred load raised.",
                extra={"log_event": "workflow_injection_failed"},
            )
            send_event("workflow_injection_failed", {"error": str(exc)})

    def _handle_inject_workflow(kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = kwargs.get("payload")
        filename = kwargs.get("filename")
        metadata = kwargs.get("metadata")

        from PyQt6.QtCore import QTimer

        QTimer.singleShot(0, lambda: _do_workflow_load(payload, filename, metadata))

        return {"status": "ok"}

    dispatcher.register("inject_workflow", _handle_inject_workflow)

    def _handle_dispatch_event(kwargs: dict[str, Any]) -> dict[str, Any]:
        """The Hub->worker half of event bridging (see `RemoteEventBus.subscribe`
        in `runtime_services.py` for the worker->Hub subscription half, and
        `core_services_bootstrap.py`'s `event.subscribe` for the Hub-side
        fan-out this is on the receiving end of).

        The Hub only ever calls this for a topic this process itself
        subscribed to — `RemoteEventBus` is where the actual callbacks and
        the "was this ever subscribed" bookkeeping live, so this handler is
        just the wire-to-bus hop.
        """
        from .runtime_services import event_bus

        topic = kwargs.get("topic", "")
        event_bus.dispatch_event(topic, kwargs.get("payload"))
        return {"status": "ok"}

    dispatcher.register("dispatch_event", _handle_dispatch_event)

    for method, handler in (extra_handlers or {}).items():
        dispatcher.register(method, handler)

    def handle_request(frame: dict[str, Any]) -> None:
        if frame.get("kind") == "event":
            # The Hub has no legitimate reason to push an unsolicited event
            # at this worker — RequestDispatcher only ever answers `request`
            # frames, so treating this as one would extract method=None,
            # dispatch a bogus "Unknown method 'None'" error, and write back
            # a `response` tagged with this frame's (nonexistent) request_id
            # — a reply nothing is waiting for, so it vanishes with no
            # visible symptom on either side. That silent version of this
            # was a real, shipped bug (see docs/internal/24, "Known failure
            # modes" #3); logging loudly and returning here instead turns a
            # stray/legacy event frame into an obvious bug report.
            logger.warning(
                "Worker received an unsolicited event frame from the Hub; "
                "the Hub must use call(), not push an event, to reach a worker.",
                extra={"log_event": "unexpected_event_frame", "topic": frame.get("topic")},
            )
            return

        request_id = frame.get("request_id")
        method = frame.get("method")
        start_time = time.monotonic()
        result = dispatcher.dispatch(frame)
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        if isinstance(result, dict) and "error" in result:
            logger.warning(
                "Request handler returned an error.",
                extra={
                    "log_event": "request_error",
                    "method": method,
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                },
            )
        else:
            logger.debug(
                "Request handled.",
                extra={
                    "log_event": "request_handled",
                    "method": method,
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                },
            )
        write_frame({"kind": "response", "request_id": request_id, "payload": result})

    bridge = _RequestBridge()
    bridge.request_received.connect(handle_request)

    reader = _RequestReader(bridge, logger)
    reader.start()

    window.show()
    # .show() alone doesn't reliably make this a genuinely activated,
    # frontmost app on macOS for a bare interpreter process spawned via
    # subprocess (no .app bundle/Info.plist) — and an app that never
    # activates can end up with its native menu bar never synced into the
    # global menu bar at all ("menu options ... not available in the
    # plugins"). Forcing activation here is a no-op on platforms where
    # .show() already did it.
    window.raise_()
    window.activateWindow()
    geometry = window.geometry()
    logger.info("Worker ready.", extra={"log_event": "worker_ready"})
    send_event(
        "ready",
        {"geometry": [geometry.x(), geometry.y(), geometry.width(), geometry.height()]},
    )

    send_event("loading_progress", {"message": "Constructing module interface…"})
    # panel_factory() — Phase 1 — deliberately runs *after* the ready
    # handshake above, same reasoning as begin_async_init() below: the
    # loader is already up and visible, so nothing about this window's
    # startup is gated behind it, and the loading screen now genuinely masks
    # Phase 1's construction time too instead of only Phase 2's.
    panel = panel_factory()

    if hasattr(panel, "data_ready"):
        # Forwards the panel's own one-shot "initial (or injected) data
        # finished loading" signal across the process boundary — connected
        # here, immediately after the panel exists, so it can never miss an
        # emission regardless of whether that happens via the panel's own
        # begin_async_init() (started automatically below unless
        # KARCYTICS_PENDING_WORKFLOW gates it) or later, dynamically, via an
        # inject_workflow request. Without this, a caller driving the panel
        # purely over IPC (this runtime has no other way to observe it) has
        # no way to tell "still loading" from "done" apart from waiting a
        # fixed, guessed amount of time — see docs/internal/24, `event`
        # table, `panel_data_ready`.
        panel.data_ready.connect(lambda: send_event("panel_data_ready", {}))

    _wire_academy_menu(window, panel, logger)
    if on_panel_ready is not None:
        try:
            on_panel_ready(window, panel)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plugin's on_panel_ready() raised; continuing without it.",
                extra={"log_event": "on_panel_ready_failed", "error": str(exc)},
            )
    if configure_menus is not None:
        try:
            configure_menus(window, panel)
        except Exception as exc:  # noqa: BLE001
            # A plugin's own menu-building code is no less capable of
            # raising than any other plugin code, but the window itself
            # (already visible, already showing the loader) shouldn't go
            # down over it — the standard File/Theme/Help menus built by
            # _build_menu_bar() are unaffected either way.
            logger.warning(
                "Plugin's configure_menus() raised; continuing without its menus.",
                extra={"log_event": "configure_menus_failed", "error": str(exc)},
            )
    # Detach the loader before replacing the central widget — QMainWindow's
    # ownership handling of a *previous* central widget varies across Qt
    # versions, and this widget must survive the swap to keep animating on
    # top of the real panel, not be silently deleted out from under it.
    loader.setParent(None)
    window.setCentralWidget(panel)
    # setCentralWidget() only *schedules* a layout pass — it doesn't run one
    # synchronously — so at this exact point panel.rect() is still whatever
    # size Qt gave it before it was ever laid out (a bare QWidget's default
    # 640x480), not the window's real content area. window.set_overlay()
    # below reads centralWidget().rect() to size the loader, so without this
    # the overlay was getting pinned to that stale 640x480 box instead of
    # the window's actual size — same class of bug as the loader's own
    # startup sizing fix above, just one step later in the sequence.
    panel.resize(window.size())
    loader.setParent(window)
    window.set_overlay(loader)
    loader.raise_()
    loader.show()
    _reveal_panel_behind_loader(loader, panel, window)

    if hasattr(panel, "begin_async_init"):
        # Deliberately deferred to *after* the ready handshake above, not
        # called eagerly by panel_factory(): a real panel's begin_async_init()
        # can import heavy, slow-to-cold-start dependencies (matplotlib,
        # umap/numba JIT, ...) as its very first step. Calling it before
        # send_event("ready") blocks that event behind however long that
        # import takes — comfortably past the Hub's own 45s Ready Gate
        # timeout in practice — even though nothing about building the
        # window itself was slow. QTimer.singleShot(0, ...) runs it on the
        # very next event-loop tick instead, after "ready" is already on
        # the wire, mirroring how the in-process Hub always sequenced this
        # (Phase 1's fast panel_ready before any Phase 2 import).
        if os.environ.get("KARCYTICS_PENDING_WORKFLOW") == "1":
            logger.info("Delaying begin_async_init until workflow payload is injected via RPC.")
        else:
            QTimer.singleShot(0, panel.begin_async_init)

    assert QThread.currentThread() is app.thread()
    exit_code = app.exec()

    logger.info("Worker exiting.", extra={"log_event": "worker_exit", "exit_code": exit_code})

    # Not sys.exit(): the reader thread (daemon, non-Python-frame) is very
    # likely still blocked inside os.read() on stdin's raw fd at this point
    # (see _read_exact_stdin — deliberately not sys.stdin.buffer.read(), but
    # normal interpreter finalization still tries to flush/close sys.stdin
    # itself regardless of which call the reader thread is actually blocked
    # in). A thread it can't join blocked on that fd is exactly the shape of
    # CPython's fatal abort at shutdown (`_enter_buffered_busy: could not
    # acquire lock ... at interpreter shutdown`) — os._exit() terminates the
    # process immediately at the OS level, skipping that finalization
    # sequence entirely, rather than relying on this being safe in every
    # CPython version. The reader thread was always going to be abandoned
    # anyway once this process exits.
    os._exit(exit_code)
