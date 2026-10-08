"""Cinematic hyperspace loading screen — QML scene shared with the Hub's own
loader, Python wiring kept separate per process.

`galactic_loader.qml` in this directory is the single canonical copy of the
actual animation (star field, warp/fade sequencing) — it has no Python or
Hub dependency of its own, so the Hub's `karcytics.ui.widgets.galactic_loader`
loads it from here via `QML_PATH` rather than keeping a second copy that
could drift out of sync.

Only the *Python* wrapper is duplicated, deliberately: the Hub's copy wires
QML properties to `karcytics.ui.theme.Colors`, this one to
`theme_fallback.Colors`, because those two processes can never import the
same theme module (different `.venv`s — the entire point of isolation).
That's the same "mirror the interface, don't share the module" shape
`theme_fallback.py` itself already uses for the same reason; each side stays
a plain, explicit ~20-line binding rather than one shared class routed
through a runtime environment check.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtQuickWidgets import QQuickWidget

from .theme_fallback import Colors

logger = logging.getLogger(__name__)

QML_PATH = Path(__file__).parent / "galactic_loader.qml"


class GalacticLoader(QQuickWidget):
    """A cinematic hyperspace loading screen, rendered on its own scene-graph
    thread so it stays smooth even while this process's main thread is
    blocked importing heavy analysis libraries (matplotlib/umap/sklearn/...).
    """

    warp_out_finished = Signal()
    fade_out_finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self.setClearColor(Qt.GlobalColor.transparent)
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        if not QML_PATH.exists():
            logger.error("QML file not found at %s — the loader will fail to render.", QML_PATH)

        self.setSource(QUrl.fromLocalFile(str(QML_PATH)))

        if self.status() == QQuickWidget.Status.Error:
            errors = "\n".join(e.toString() for e in self.errors())
            logger.error("Failed to load QML source: %s", errors)

        root = self.rootObject()
        if root:
            if hasattr(root, "warpOutFinished"):
                root.warpOutFinished.connect(self.warp_out_finished.emit)  # type: ignore[attr-defined]
            if hasattr(root, "fadeOutFinished"):
                root.fadeOutFinished.connect(self.fade_out_finished.emit)  # type: ignore[attr-defined]
            self.update_colors()

    def update_colors(self) -> None:
        """Apply the current `theme_fallback.Colors` palette to the QML scene."""
        root = self.rootObject()
        if not root:
            return
        root.setProperty("bgColor", Colors.BG_DARKEST)
        root.setProperty("accentColor", Colors.ACCENT_PRIMARY)
        root.setProperty("textColor", Colors.FG_PRIMARY)

    def set_module(self, name: str) -> None:
        """Reset the loader and assign it to a new module name."""
        root = self.rootObject()
        if root:
            root.setProperty("moduleName", name)
            QtCore.QMetaObject.invokeMethod(root, "reset")

    def set_status_message(self, msg: str) -> None:
        """Update the secondary status line (e.g. while Phase 2 builds)."""
        root = self.rootObject()
        if root:
            root.setProperty("statusMessage", msg)

    def warp_out(self) -> None:
        """Begin the cinematic warp-out sequence."""
        root = self.rootObject()
        if root:
            QtCore.QMetaObject.invokeMethod(root, "warpOut")

    def fade_out(self, duration_ms: int = 500) -> None:
        """Begin the loader's fade-out transition."""
        root = self.rootObject()
        if root:
            QtCore.QMetaObject.invokeMethod(root, "fadeOut", QtCore.Q_ARG("QVariant", duration_ms))
