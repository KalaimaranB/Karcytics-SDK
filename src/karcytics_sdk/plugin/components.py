"""Semantic UI components for Karcytics SDK.

Provides pre-styled button and component classes that respect the active theme
and maintain visual consistency across all plugins.
"""

import sys
from collections.abc import Sequence
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _get_theme_tokens():
    if "karcytics.ui.theme" in sys.modules:
        tm = sys.modules["karcytics.ui.theme"]
        return tm.Colors, tm.Fonts, tm.theme_manager
    try:
        from karcytics.ui.theme import Colors, Fonts, theme_manager

        return Colors, Fonts, theme_manager
    except ImportError:
        from karcytics_sdk.plugin.theme_fallback import Colors, Fonts, theme_manager

        return Colors, Fonts, theme_manager


Colors, Fonts, theme_manager = _get_theme_tokens()


def _connect_theme_signal(callback):
    global Colors, Fonts, theme_manager
    Colors, Fonts, theme_manager = _get_theme_tokens()
    theme_manager.theme_changed.connect(callback)
    callback()


def apply_global_sdk_styles() -> None:
    """Inject QToolTip styling globally and configure the QApplication palette for dark mode.

    No-ops (see the `isinstance(app, QApplication)` guard below) if called
    before a `QApplication` exists — which it reliably is the first time
    this runs: importing this module eagerly runs this function once at
    import time (see the bottom of this module), and for an isolated
    plugin that import happens via `ui_daemon.py`'s
    `from karcytics_sdk.plugin import run_ui_daemon`, well before
    `ui_daemon_runtime.run()` ever constructs a `QApplication`. Nothing
    calls this again afterward on its own — `theme_changed` only fires on
    an actual user theme switch, not at startup — which left native,
    app-level-only-stylable popups (`QToolTip`, a plain `QComboBox`'s
    dropdown) rendering with Qt's unstyled default palette for the entire
    session unless the user happened to switch themes. `run()` calls this
    again explicitly right after constructing its `QApplication` to close
    that gap.
    """
    import re

    from PyQt6.QtCore import QMetaObject, Qt, QThread, QTimer
    from PyQt6.QtGui import QColor, QPalette
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return

    if QThread.currentThread() is not app.thread():
        # QApplication.setStyle()/setPalette()/setStyleSheet() are not thread-safe:
        # setStyle() reparents the new QStyle onto qApp internally, which corrupts
        # Qt's thread-affinity bookkeeping (and can crash later, somewhere
        # unrelated, e.g. QWidget::sizeHint() during a layout pass) if called off
        # the GUI thread. This module is imported at plugin-load time, and hosts
        # can load plugin code on a background QThread (e.g. Karcytics's async
        # plugin-loader), so the *first* import of this module in the process
        # can land here off the main thread. Hop over via a queued single-shot
        # timer moved onto the QApplication's own thread instead of touching the
        # QApplication directly from here.
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(apply_global_sdk_styles)
        timer.moveToThread(app.thread())
        timer.setParent(app)  # keep it alive until it fires; safe now that thread affinity matches
        QMetaObject.invokeMethod(timer, "start", Qt.ConnectionType.QueuedConnection)
        return

    from karcytics_sdk.plugin.theme_fallback import get_contrast_text_color

    Colors, Fonts, _ = _get_theme_tokens()

    # --- 0. Enforce Fusion Style Engine ---
    # macOS native style engine heavily ignores custom background colors for native containers (tooltips, dropdowns).
    # Forcing Fusion ensures our theme correctly styles everything cross-platform.
    app.setStyle("Fusion")

    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(Colors.BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.FG_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(Colors.BG_MEDIUM))
    palette.setColor(QPalette.ColorRole.Text, QColor(Colors.FG_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(Colors.BG_LIGHT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.FG_PRIMARY))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(Colors.ACCENT_PRIMARY))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(get_contrast_text_color(Colors.ACCENT_PRIMARY)))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(Colors.BG_DARKEST))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(Colors.FG_PRIMARY))
    app.setPalette(palette)

    # --- 2. Global Stylesheet Overrides ---
    qss = app.styleSheet() or ""
    # Remove any existing QToolTip block
    qss = re.sub(r"QToolTip\s*\{[^}]*\}", "", qss)

    try:
        family = getattr(Fonts, "FAMILY_UI", "Arial")
    except NameError:
        family = "Arial"

    tooltip_qss = f"""
        QToolTip {{
            color: {Colors.FG_PRIMARY};
            background-color: {Colors.BG_DARKEST};
            border: 1px solid {Colors.BORDER_FOCUS};
            padding: 5px 8px;
            font-family: {family};
            font-size: 11px;
        }}
    """

    app.setStyleSheet(qss + tooltip_qss)


try:
    _connect_theme_signal(apply_global_sdk_styles)
except Exception:
    pass


def apply_component_style(widget: QWidget, component_type: str) -> None:
    """Helper function to dynamically apply theme CSS without requiring a subclass."""
    # Useful for injecting basic styles into standard Qt widgets directly
    pass


class BioButton(QPushButton):
    """Base unified button class.

    Variants: 'primary', 'secondary', 'danger'.
    """

    def __init__(self, text: str, variant: str = "primary", parent=None):
        super().__init__(text, parent)
        self.setObjectName(f"BioButton_{variant}")
        self.variant = variant
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._glow_effect = None
        self.custom_css_overrides = ""
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        global Colors, Fonts
        Colors, Fonts, _ = _get_theme_tokens()
        self.setProperty("variant", self.variant)
        self.style().unpolish(self)
        self.style().polish(self)

        if self.custom_css_overrides:
            self.setStyleSheet(f"QPushButton {{ {self.custom_css_overrides} }}")
        elif self.variant == "primary":
            css = f"""
                    QPushButton {{
                        background-color: {Colors.ACCENT_PRIMARY};
                        color: {Colors.BG_DARKEST};
                        border: none;
                        border-radius: 6px;
                        padding: 10px 20px;
                        font-size: 13px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{ background-color: {Colors.ACCENT_PRIMARY_HOVER}; }}
                    QPushButton:disabled {{ background-color: {Colors.BG_MEDIUM}; color: {Colors.FG_SECONDARY}; }}
                """
            self.setStyleSheet(css)
        elif self.variant == "secondary":
            css = f"""
                    QPushButton {{
                        background-color: {Colors.BG_MEDIUM};
                        color: {Colors.FG_PRIMARY};
                        border: 1px solid {Colors.BORDER};
                        border-radius: 6px;
                        padding: 10px 20px;
                        font-size: 13px;
                    }}
                    QPushButton:hover {{ background-color: {Colors.BG_LIGHT}; border-color: {Colors.FG_SECONDARY}; }}
                """
            self.setStyleSheet(css)
        elif self.variant == "danger":
            css = f"""
                    QPushButton {{
                        background-color: {Colors.ACCENT_DANGER};
                        color: white;
                        border: none;
                        border-radius: 6px;
                        padding: 8px 16px;
                        font-size: 13px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{ background-color: {Colors.ACCENT_CRITICAL}; }}
                """
            self.setStyleSheet(css)
        else:
            self.setStyleSheet("")

        if self.variant == "primary" and Colors.GLOW_COLOR != "transparent":
            from PyQt6.QtGui import QColor
            from PyQt6.QtWidgets import QGraphicsDropShadowEffect

            if self._glow_effect is None:
                self._glow_effect = QGraphicsDropShadowEffect(self)
                self.setGraphicsEffect(self._glow_effect)

            if self._glow_effect is not None:
                self._glow_effect.setBlurRadius(15)
                self._glow_effect.setOffset(0, 0)
                self._glow_effect.setColor(QColor(Colors.GLOW_COLOR))
        elif self._glow_effect:
            self.setGraphicsEffect(None)
            self._glow_effect = None


class PrimaryButton(BioButton):
    """Primary action button using accent color (backwards compatible)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, variant="primary", parent=parent)


class SecondaryButton(BioButton):
    """Secondary/outline button for non-critical actions (backwards compatible)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, variant="secondary", parent=parent)


class DangerButton(BioButton):
    """Destructive action button for delete/remove operations (backwards compatible)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, variant="danger", parent=parent)


class AcademyButton(BioButton):
    """Drop-in "🎓 Cyto Academy" button for a plugin's own toolbar.

    Before the isolated-plugin migration, the Hub injected an identically
    accent-styled button into every in-process analysis panel's toolbar
    (the old `AnalysisToolBar.btn_academy`) and wired it straight to the
    Hub's own `AcademyManager`. Each module now runs in its own process with
    its own separate `AcademyManager` (`runtime_services.tutorial_manager`)
    that the Hub can no longer reach into, so a plugin wanting this entry
    point back in its own toolbar (not just the isolated window's Help
    menu) adds this button itself and hands it whatever widget hosts it.

    `panel` only needs to resolve `.window()` at click time — via
    `academy_driver.open_academy()`, the same shared entry point the
    isolated window's Help > Academy menu action uses — so this works
    whether or not `panel` is already parented into a window when the
    button is constructed.
    """

    def __init__(self, panel: QWidget, parent=None):
        super().__init__("🎓 Cyto Academy", variant="primary", parent=parent)
        self._panel = panel
        self.setObjectName("btn_academy")
        self.setToolTip("Open Karcytics Academy for this module")
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self) -> None:
        from .academy_driver import open_academy

        window = self._panel.window() or self._panel
        open_academy(window, self._panel)


class BioToggleButton(QPushButton):
    """A button that handles active/inactive state natively."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("BioToggleButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Extensible defaults that subclasses or instances can configure
        self.custom_css_overrides = ""

        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

        Colors, Fonts, _ = _get_theme_tokens()

        css = f"""
            BioToggleButton {{
                background-color: {Colors.BG_DARKEST};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: {Fonts.SIZE_NORMAL}px;
                text-align: center;
                {{self.custom_css_overrides}}
            }}
            BioToggleButton:hover {{
                background-color: {Colors.BG_LIGHT};
                border-color: {Colors.BORDER_FOCUS};
            }}
            BioToggleButton:checked {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.BG_DARKEST};
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            BioToggleButton:checked:hover {{
                background-color: {Colors.ACCENT_PRIMARY_HOVER};
            }}
        """
        self.setStyleSheet(css.replace("{self.custom_css_overrides}", self.custom_css_overrides))


class BioRunButton(PrimaryButton):
    """Pre-configured action button for starting tasks."""

    def __init__(self, text: str = "🧬 Run", parent=None):
        super().__init__(text, parent)


class BioCancelButton(SecondaryButton):
    """Pre-configured action button for cancelling tasks."""

    def __init__(self, text: str = "⏹ Cancel", parent=None):
        super().__init__(text, parent)


class _HelpPopup(QWidget):
    """Rich popup for help buttons."""

    def __init__(self, text: str, title: str = "", parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("PopupContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(6)

        if title:
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                f"font-weight: bold; color: {Colors.ACCENT_PRIMARY}; font-family: {Fonts.FAMILY_UI}; font-size: 13px;"
            )
            container_layout.addWidget(title_lbl)

        text_lbl = QLabel(text)
        text_lbl.setWordWrap(True)
        text_lbl.setMinimumWidth(200)
        text_lbl.setMaximumWidth(400)
        text_lbl.setStyleSheet(
            f"color: {Colors.FG_PRIMARY}; font-family: {Fonts.FAMILY_UI}; font-size: 12px; line-height: 1.4;"
        )
        container_layout.addWidget(text_lbl)

        layout.addWidget(container)

        self.setStyleSheet(f"""
            QFrame#PopupContainer {{
                background-color: {Colors.BG_DARKEST};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)


class BioHelpButton(QPushButton):
    """Small help (?) button that shows a rich popup dialog on click."""

    def __init__(self, parent=None):
        super().__init__("?", parent)
        self.setObjectName("BioHelpButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(20, 20)
        self._help_text = ""
        self._help_title = ""
        self.clicked.connect(self._show_popup)
        _connect_theme_signal(self._apply_theme_styles)

    def setHelpText(self, text: str, title: str = "") -> None:
        """Set the detailed help text and optional title for the popup."""
        self._help_text = text
        self._help_title = title

    def setToolTip(self, text: str | None) -> None:
        """Override to use the click popup instead of the native hover tooltip for backwards compatibility."""
        super().setToolTip("")
        self.setHelpText(text or "")

    def _show_popup(self) -> None:
        if not self._help_text:
            return

        self._popup = _HelpPopup(self._help_text, self._help_title, self.window())
        self._popup.adjustSize()

        # Position the popup below the button
        pos = self.mapToGlobal(self.rect().bottomLeft())
        # Offset slightly for aesthetics
        pos.setY(pos.y() + 5)
        pos.setX(pos.x() - 10)
        self._popup.move(pos)
        self._popup.show()

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet(f"""
            BioHelpButton {{
                background-color: transparent;
                color: {Colors.FG_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
                font-size: 11px;
                font-weight: bold;
                font-family: {Fonts.FAMILY_UI};
                padding: 0px;
                margin: 0px;
            }}
            BioHelpButton:hover {{
                background-color: {Colors.BG_LIGHT};
                color: {Colors.FG_PRIMARY};
                border-color: {Colors.FG_PRIMARY};
            }}
        """)


class ModuleCard(QFrame):
    """Standardized, interactive card for lists and grids."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("BioCard")
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        glow_css = ""
        if Colors.GLOW_COLOR != "transparent":
            glow_css = f"border: 1px solid {Colors.ACCENT_PRIMARY};"

        self.setStyleSheet(f"""
            QFrame#BioCard {{
                background-color: {Colors.BG_DARK};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
                font-family: {Fonts.FAMILY_UI};
            }}
            QFrame#BioCard:hover {{
                border: 1px solid {Colors.ACCENT_PRIMARY};
                background-color: {Colors.BG_MEDIUM};
                {glow_css}
            }}
        """)


class HeaderLabel(QLabel):
    """Standardized H1 header label."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet(
            f"font-size: {Fonts.SIZE_LARGE}px; "
            f"font-weight: bold; "
            f"font-family: {Fonts.FAMILY_HEADINGS}; "
            f"color: {Colors.FG_PRIMARY};"
        )


BioHeaderLabel = HeaderLabel


class SubtitleLabel(QLabel):
    """Standardized subtitle/secondary header label."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet(f"font-size: {Fonts.SIZE_NORMAL}px; font-weight: 600; color: {Colors.FG_PRIMARY};")


class BioCaptionLabel(QLabel):
    """For educational text or hints."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        try:
            size = getattr(Fonts, "SIZE_SMALL", 11)
        except AttributeError:
            size = 11
        self.setStyleSheet(f"font-size: {size}px; color: {Colors.FG_SECONDARY};")


class BioStatusLabel(QLabel):
    """Standardized italicized status text."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet(f"font-size: {Fonts.SIZE_NORMAL}px; font-style: italic; color: {Colors.FG_SECONDARY};")


class BioComboBox(QComboBox):
    """Standardized combo box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        from PyQt6.QtWidgets import QListView

        # Override native macOS popup view to allow proper styling
        view = QListView()
        self.setView(view)
        view.window().setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        view.window().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setItemDelegate(QStyledItemDelegate(self))
        _connect_theme_signal(self._apply_theme_styles)

    def showPopup(self) -> None:
        super().showPopup()
        self.view().window().setMinimumWidth(self.width())

    def _apply_theme_styles(self) -> None:
        from karcytics_sdk.plugin.theme_fallback import get_contrast_text_color

        Colors, Fonts, _ = _get_theme_tokens()
        selection_text_color = get_contrast_text_color(Colors.ACCENT_PRIMARY)
        self.setStyleSheet(f"""
            QComboBox {{
                combobox-popup: 0;
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QComboBox::drop-down {{
                border-left: 1px solid {Colors.BORDER};
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.BG_DARKEST};
                color: {Colors.FG_PRIMARY};
                selection-background-color: {Colors.ACCENT_PRIMARY};
                selection-color: {selection_text_color};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                outline: none;
                padding: 2px;
            }}
            QComboBox QAbstractItemView::item {{
                color: {Colors.FG_PRIMARY};
                min-height: 24px;
                padding: 2px 4px;
                border-radius: 2px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {selection_text_color};
            }}
        """)


def repopulate_combo(
    combo: QComboBox,
    items: Sequence[tuple[str, Any]],
    *,
    restore_data: Any = None,
) -> None:
    """Rebuild a combo box's items and restore a selection, without spurious signals.

    Replaces the repeated ``blockSignals(True)`` / ``clear()`` / ``addItem()``
    loop / ``findData`` restore / ``blockSignals(False)`` dance that tends to
    get duplicated at every combo-populate call site across plugins. No-ops
    entirely (touches the combo not at all) when both the item set and the
    resolved selection already match what's there — the common case where an
    unrelated refresh (e.g. a tab switch) re-triggers a combo populate call
    with nothing to actually change.

    Args:
        combo: The QComboBox to repopulate.
        items: Ordered (label, data) pairs for every item to show.
        restore_data: The `data` value to reselect if still present in
            `items` after rebuilding — typically the combo's own
            `currentData()` captured before calling this. `None` leaves
            the current index wherever `clear()`/`addItem()` land it.

    Returns:
        None.
    """
    items = list(items)
    current = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
    items_unchanged = current == items
    selection_already_right = restore_data is None or combo.currentData() == restore_data

    if items_unchanged and selection_already_right:
        return

    combo.blockSignals(True)
    if not items_unchanged:
        combo.clear()
        for label, data in items:
            combo.addItem(label, data)
    if restore_data is not None:
        idx = combo.findData(restore_data)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.blockSignals(False)


class BioSpinBox(QSpinBox):
    """Standardized numeric input."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            QSpinBox {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 4px 8px;
            }}
        """)


class BioDoubleSpinBox(QDoubleSpinBox):
    """Standardized floating point input."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 4px 8px;
            }}
        """)


class BioLineEdit(QLineEdit):
    """Standardized text input."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 4px 8px;
            }}
        """)


class BioListWidget(QListWidget):
    """Standardized list widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
            }}
            QListWidget::item {{
                color: {Colors.FG_PRIMARY};
            }}
            QListWidget::item:selected {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.BG_DARKEST};
            }}
        """)


class BioTableWidget(QTableWidget):
    """Standardized table widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: transparent;
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                gridline-color: {Colors.BORDER};
            }}
            QHeaderView::section {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}
            QTableWidget::item:selected {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.BG_DARKEST};
            }}
        """)


class BioSplitter(QSplitter):
    """Standardized splitter."""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {Colors.BORDER};
            }}
        """)


class BioScrollArea(QScrollArea):
    """Standardized scroll area with transparent background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWidgetResizable(True)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
        """)


class BioProgressDialog(QProgressDialog):
    """Standardized progress dialog."""

    def __init__(self, labelText: str, cancelButtonText: str, minimum: int, maximum: int, parent=None):
        super().__init__(labelText, cancelButtonText, minimum, maximum, parent)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            QProgressDialog {{
                background-color: {Colors.BG_DARK};
                color: {Colors.FG_PRIMARY};
            }}
            QLabel {{
                color: {Colors.FG_PRIMARY};
                font-size: {Fonts.SIZE_NORMAL}px;
            }}
            QProgressBar {{
                background-color: {Colors.BG_DARKEST};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                text-align: center;
                color: {Colors.FG_PRIMARY};
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ACCENT_PRIMARY};
                border-radius: 3px;
            }}
            QPushButton {{
                background-color: {Colors.BG_LIGHT};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.BG_DARKEST};
            }}
        """)


class BioFooter(QWidget):
    """Standardized footer matching Karcytics core status bar."""

    def __init__(
        self,
        initial_text: str = "Welcome to Karcytics — choose a module to begin",
        copyright_text: str = "© Kalaimaran Balasothy",
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)

        self.message_label = QLabel(initial_text)
        self.copyright_label = QLabel(copyright_text)

        layout.addWidget(self.message_label)
        layout.addStretch()
        layout.addWidget(self.copyright_label)

        _connect_theme_signal(self._apply_theme_styles)

    def show_message(self, text: str) -> None:
        self.message_label.setText(text)

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()
        self.setStyleSheet(f"""
            BioFooter {{
                background-color: {Colors.BG_DARKEST};
                border-top: 1px solid {Colors.BORDER};
            }}
            QLabel {{
                color: {Colors.FG_SECONDARY};
                font-size: {Fonts.SIZE_SMALL}px;
                border: none;
                background-color: transparent;
            }}
        """)


class BioMenu(QMenu):
    """Standardized themed context menu."""

    def __init__(self, parent=None, title: str | None = None):
        if title:
            super().__init__(title, parent)
        else:
            super().__init__(parent)
        _connect_theme_signal(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        from karcytics_sdk.plugin.theme_fallback import get_contrast_text_color

        Colors, Fonts, _ = _get_theme_tokens()
        selection_text_color = get_contrast_text_color(Colors.ACCENT_PRIMARY)
        self.setStyleSheet(f"""
            QMenu {{
                background-color: {Colors.BG_DARKEST};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                font-family: {Fonts.FAMILY_UI};
                font-size: {Fonts.SIZE_NORMAL}px;
            }}
            QMenu::item {{
                padding: 4px 20px 4px 20px;
                background-color: transparent;
                color: {Colors.FG_PRIMARY};
            }}
            QMenu::item:disabled {{
                color: {Colors.FG_DISABLED};
            }}
            QMenu::item:selected {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {selection_text_color};
            }}
            QMenu::separator {{
                height: 1px;
                background: {Colors.BORDER};
                margin: 4px 0px;
            }}
        """)


class WorkspaceSaveButton(QToolButton):
    """Smart split-button for saving workflows, defaulting to Update or Save New depending on state."""

    save_requested = pyqtSignal()
    update_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkspaceSaveButton")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._menu = BioMenu(self)
        self.action_save_new = self._menu.addAction("💾 Save As New Workflow...")
        self.action_save_new.triggered.connect(self.save_requested.emit)

        self.setMenu(self._menu)

        self.clicked.connect(self._on_main_click)

        self._is_dirty = False
        self._has_workflow = False

        _connect_theme_signal(self._apply_theme_styles)
        self.set_dirty(False)

    def _on_main_click(self):
        if self._has_workflow:
            self.update_requested.emit()
        else:
            self.save_requested.emit()

    def set_workflow_active(self, active: bool):
        """Set whether there is currently an active workflow loaded or saved."""
        self._has_workflow = active

    def set_dirty(self, dirty: bool):
        self._is_dirty = dirty
        if dirty:
            self.setText("⚠️ Save Workspace")
        else:
            self.setText("✔️ All Changes Saved")
        self._apply_theme_styles()

    def _apply_theme_styles(self) -> None:
        Colors, Fonts, _ = _get_theme_tokens()

        if self._is_dirty:
            color = Colors.ACCENT_PRIMARY
            border = Colors.ACCENT_PRIMARY
            font_weight = "bold"
        else:
            color = Colors.FG_SECONDARY
            border = Colors.BORDER
            font_weight = "normal"

        self.setStyleSheet(f"""
            QToolButton {{
                background-color: {Colors.BG_DARK};
                color: {color};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 10px 30px 10px 20px;
                font-size: 13px;
                font-weight: {font_weight};
            }}
            QToolButton:hover {{
                background-color: {Colors.BG_MEDIUM};
                border-color: {Colors.BORDER_FOCUS};
            }}
            QToolButton::menu-button {{
                border-left: 1px solid {border};
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
                width: 24px;
            }}
            QToolButton::menu-indicator {{
                image: none;
            }}
        """)
