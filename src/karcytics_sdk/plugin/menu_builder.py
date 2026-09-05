"""Standard Menu Builder for Karcytics."""

from collections.abc import Callable

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMenu


class StandardMenuBuilder:
    def __init__(self, window: QMainWindow):
        self.window = window
        self.menubar = window.menuBar()
        if self.menubar is None:
            raise ValueError("Window does not have a valid menubar.")

        self.file_menu: QMenu | None = None
        self.edit_menu: QMenu | None = None
        self.view_menu: QMenu | None = None
        self.help_menu: QMenu | None = None

    def add_file_menu(self, extra_actions: list[QAction] | None = None) -> QMenu:
        if not self.file_menu:
            self.file_menu = self.menubar.addMenu("&File")

        if extra_actions:
            for action in extra_actions:
                self.file_menu.addAction(action)
        assert self.file_menu is not None
        return self.file_menu

    def add_edit_menu(
        self,
        undo_cb: Callable | None = None,
        redo_cb: Callable | None = None,
        pref_cb: Callable | None = None,
        pref_action_name: str = "Preferences...",
    ) -> QMenu:
        if not self.edit_menu:
            self.edit_menu = self.menubar.addMenu("&Edit")

        if undo_cb:
            undo_action = QAction("&Undo", self.window)
            undo_action.setShortcut(QKeySequence.StandardKey.Undo)
            undo_action.triggered.connect(undo_cb)
            self.edit_menu.addAction(undo_action)

        if redo_cb:
            redo_action = QAction("&Redo", self.window)
            redo_action.setShortcut(QKeySequence.StandardKey.Redo)
            redo_action.triggered.connect(redo_cb)
            self.edit_menu.addAction(redo_action)

        if pref_cb:
            if undo_cb or redo_cb:
                self.edit_menu.addSeparator()
            pref_action = QAction(pref_action_name, self.window)
            pref_action.setMenuRole(QAction.MenuRole.PreferencesRole)
            pref_action.setShortcut("Ctrl+,")
            pref_action.triggered.connect(pref_cb)
            self.edit_menu.addAction(pref_action)

        assert self.edit_menu is not None
        return self.edit_menu

    def add_view_menu(self) -> QMenu:
        if not self.view_menu:
            self.view_menu = self.menubar.addMenu("&View")
        assert self.view_menu is not None
        return self.view_menu

    def add_theme_menu(self, switch_theme_cb: Callable, categorized_themes: dict) -> QMenu:
        view = self.add_view_menu()
        theme_menu = view.addMenu("&Theme")

        for category, themes in categorized_themes.items():
            submenu = QMenu(category, self.window)
            for name, path in themes:
                action = QAction(name, self.window)
                action.triggered.connect(lambda _checked, p=path, cb=switch_theme_cb: cb(p))
                submenu.addAction(action)
            theme_menu.addMenu(submenu)
        assert theme_menu is not None
        return theme_menu

    def add_help_menu(  # noqa: PLR0913, PLR0917
        self,
        docs_cb: Callable | None = None,
        wiki_cb: Callable | None = None,
        about_cb: Callable | None = None,
        about_dev_cb: Callable | None = None,
        onboarding_cb: Callable | None = None,
        mac_no_role: bool = False,
    ) -> QMenu:
        if not self.help_menu:
            self.help_menu = self.menubar.addMenu("&Help")

        if docs_cb:
            docs_action = QAction("📖 Karcytics &Help Center", self.window)
            docs_action.triggered.connect(docs_cb)
            self.help_menu.addAction(docs_action)

        if wiki_cb:
            wiki_action = QAction("🌐 View GitHub Wiki Online", self.window)
            wiki_action.triggered.connect(wiki_cb)
            self.help_menu.addAction(wiki_action)

        if onboarding_cb:
            onboarding_action = QAction("♻️ Restart Onboarding Tour", self.window)
            onboarding_action.triggered.connect(onboarding_cb)
            self.help_menu.addAction(onboarding_action)

        if (docs_cb or wiki_cb or onboarding_cb) and (about_cb or about_dev_cb):
            self.help_menu.addSeparator()

        about_action = QAction("About Karcytics", self.window)
        about_me_action = QAction("About the Developer", self.window)

        if mac_no_role:
            about_action.setMenuRole(QAction.MenuRole.NoRole)
            about_me_action.setMenuRole(QAction.MenuRole.NoRole)
        else:
            about_action.setMenuRole(QAction.MenuRole.AboutRole)
            about_me_action.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)

        if about_cb:
            about_action.triggered.connect(about_cb)
            self.help_menu.addAction(about_action)
        else:
            about_action.setVisible(False)

        if about_dev_cb:
            about_me_action.triggered.connect(about_dev_cb)
            self.help_menu.addAction(about_me_action)
        else:
            about_me_action.setVisible(False)

        assert self.help_menu is not None
        return self.help_menu
