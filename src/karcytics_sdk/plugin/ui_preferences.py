"""Unified Preferences Dialog for the SDK."""

from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .theme_fallback import Colors, Fonts, theme_manager

if TYPE_CHECKING:
    from .autosave import WorkflowAutosaveController


class SDKPreferencesDialog(QDialog):
    """A Preferences Dialog that plugins can extend.

    Styled to match the Hub's own in-process `PreferencesDialog`
    (`karcytics/ui/dialogs/preferences_dialog.py`) — same nav-list/stacked-
    page layout, same QSS — via `theme_fallback.theme_manager`, so an
    isolated plugin's Preferences dialog looks identical to the Hub's
    regardless of which process built it.
    """

    def __init__(self, parent=None, client=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumSize(600, 400)
        self.client = client

        self._setup_ui()
        self._apply_styles()
        theme_manager.connect(self._apply_styles)

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Left panel: Navigation list
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        self.nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        main_layout.addWidget(self.nav_list)

        # Right panel: Stacked widget pages
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack)

        # Bottom right buttons (Close)
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(16, 16, 16, 16)
        btn_layout.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.setMinimumWidth(80)
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)
        right_layout.addLayout(btn_layout)

        main_layout.addWidget(right_container, stretch=1)

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)

    def add_page(self, title: str, widget: QWidget):
        """Allow plugins to inject their own preference pages."""
        self.nav_list.addItem(title)
        self.stack.addWidget(widget)
        if self.nav_list.count() == 1:
            self.nav_list.setCurrentRow(0)

    def _apply_styles(self) -> None:
        theme_manager.apply_style(
            self,
            f"""
            QDialog {{
                background-color: {Colors.BG_DARKEST};
                border: 1px solid {Colors.BORDER};
            }}
            QListWidget {{
                background-color: {Colors.BG_DARK};
                border: none;
                border-right: 1px solid {Colors.BORDER};
                outline: none;
                padding-top: 12px;
            }}
            QListWidget::item {{
                color: {Colors.FG_SECONDARY};
                padding: 10px 16px;
                border: none;
            }}
            QListWidget::item:hover {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
            }}
            QListWidget::item:selected {{
                background-color: {Colors.BG_LIGHT};
                color: {Colors.ACCENT_PRIMARY};
                border-left: 3px solid {Colors.ACCENT_PRIMARY};
            }}
            QLabel, QRadioButton, QCheckBox {{
                padding-bottom: 4px;
                padding-top: 2px;
            }}
            QPushButton {{
                background-color: {Colors.BG_DARK};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                padding: 8px 16px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_MEDIUM};
                border: 1px solid {Colors.ACCENT_PRIMARY};
            }}
            """,
        )


class SDKThemePreferencesPage(QWidget):
    """The "Theme" page every isolated plugin's Preferences dialog gets by
    default (see `ui_daemon_runtime._open_preferences`) — added by the SDK
    itself so a plugin gets working theme switching without writing any of
    its own preferences UI, mirroring the Hub's own in-process
    `ThemeSettingsWidget` (karcytics/ui/dialogs/preferences_dialog.py) but
    driven through `CoreServicesClient` instead of a live `theme_manager`
    reference, since an isolated plugin has no direct access to the Hub's.
    """

    def __init__(self, client: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("Appearance & Themes")
        title_label.setFont(Fonts.H2)
        layout.addWidget(title_label)

        desc_label = QLabel("Select your preferred theme for the workspace.")
        desc_label.setObjectName("secondaryText")
        layout.addWidget(desc_label)

        self._status_label = QLabel("")
        self._status_label.setObjectName("secondaryText")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        try:
            categorized_themes = client.call("theme.list_categorized_themes")
        except Exception as exc:  # noqa: BLE001
            categorized_themes = {}
            self._status_label.setText(f"Could not reach the Hub for the theme list: {exc}")

        current_name = None
        try:
            current_name = (client.call("theme.get_current_theme_name") or {}).get("name")
        except Exception:  # noqa: BLE001
            pass  # Not fatal — themes just won't show which one is active.

        self.button_group = QButtonGroup(self)
        for category, themes in categorized_themes.items():
            cat_label = QLabel(category)
            cat_label.setFont(Fonts.H3 if hasattr(Fonts, "H3") else Fonts.H2)
            layout.addWidget(cat_label)
            for name, path in themes:
                radio = QRadioButton(name)
                if current_name is not None and name == current_name:
                    radio.setChecked(True)
                radio.toggled.connect(lambda checked, p=path: self._on_theme_toggled(checked, p))
                self.button_group.addButton(radio)
                layout.addWidget(radio)

        layout.addStretch()
        self._apply_styles()
        theme_manager.connect(self._apply_styles)

    def _apply_styles(self) -> None:
        for label in self.findChildren(QLabel):
            if label.objectName() == "secondaryText":
                theme_manager.apply_style(label, f"color: {Colors.FG_SECONDARY};")
            else:
                theme_manager.apply_style(label, f"color: {Colors.FG_PRIMARY};")
        for radio in self.findChildren(QRadioButton):
            theme_manager.apply_style(radio, f"color: {Colors.FG_PRIMARY};")

    def _on_theme_toggled(self, checked: bool, path: str) -> None:
        if not checked:
            return
        try:
            self.client.call("theme.switch_theme", path=path)
        except Exception as exc:  # noqa: BLE001
            self._status_label.setText(f"Failed to switch theme: {exc}")


class AutosaveWorkflowsPreferencesPage(QWidget):
    """The "Workspace" page a plugin's Preferences dialog gets once it has
    opted into workflow autosave via `PluginBase.setup_workflow_autosave()`
    (see `PluginBase.populate_preferences`) — no plugin writes any of this
    UI itself, only the save/state callbacks the controller needs.
    """

    def __init__(self, controller: "WorkflowAutosaveController", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("Workspace")
        title_label.setFont(Fonts.H2)
        layout.addWidget(title_label)

        self._checkbox = QCheckBox("Autosave workflows every 15 minutes")
        self._checkbox.setChecked(controller.enabled)
        self._checkbox.toggled.connect(controller.set_enabled)
        layout.addWidget(self._checkbox)

        desc_label = QLabel(
            "Only applies to a workflow that's already been saved manually at least "
            "once (that's what gives it a name). You'll get a toast confirming each "
            "autosave — or, if this is off or nothing has been saved yet, a reminder "
            "every 15 minutes instead."
        )
        desc_label.setObjectName("secondaryText")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addStretch()
        self._apply_styles()
        theme_manager.connect(self._apply_styles)

    def _apply_styles(self) -> None:
        for label in self.findChildren(QLabel):
            if label.objectName() == "secondaryText":
                theme_manager.apply_style(label, f"color: {Colors.FG_SECONDARY};")
            else:
                theme_manager.apply_style(label, f"color: {Colors.FG_PRIMARY};")
        theme_manager.apply_style(self._checkbox, f"color: {Colors.FG_PRIMARY};")
