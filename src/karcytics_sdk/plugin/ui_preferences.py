"""Unified Preferences Dialog for the SDK."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class SDKPreferencesDialog(QDialog):
    """A Preferences Dialog that plugins can extend."""

    def __init__(self, parent=None, client=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumSize(600, 400)
        self.client = client

        self._setup_ui()

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
