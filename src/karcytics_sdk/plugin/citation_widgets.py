"""Widgets that show `citations` entries in a plugin's UI."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from . import citations
from .theme_fallback import Colors, theme_manager


def _link(url: str, text: str) -> str:
    return f"<a href='{escape(url, quote=True)}' style='color:{Colors.ACCENT_PRIMARY};'>{escape(text)}</a>"


class CitationLabel(QLabel):
    """One line crediting a method: ``Powered by Cellpose-SAM (Pachitariu et al., 2025) · Read the paper``.

    Args:
        name: What the user sees the method as, e.g. ``"Cellpose-SAM"``.
        keys: Registered citation keys; the first is named and linked, the
            rest are linked as extra papers.
        prefix: Lead-in text.
    """

    def __init__(self, name: str, keys: Iterable[str], prefix: str = "Powered by", parent: QWidget | None = None):
        super().__init__(parent)
        self._name = name
        self._keys = tuple(keys)
        self._prefix = prefix
        self.setWordWrap(True)
        self.setOpenExternalLinks(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        theme_manager.apply_style(
            self,
            "QLabel { background: {BG_DARK}; border: 1px solid {BORDER}; border-radius: 4px;"
            " padding: 6px; color: {FG_SECONDARY}; font-size: 11px; }",
        )
        theme_manager.connect(self._render)
        self._render()

    @property
    def keys(self) -> tuple[str, ...]:
        return self._keys

    def _render(self) -> None:
        refs = citations.resolve(self._keys)
        if not refs:
            self.setText(f"{escape(self._prefix)} <b>{escape(self._name)}</b>")
            return
        first, *rest = refs
        links = [_link(first.link, "Read the paper")] if first.link else []
        links += [_link(c.link, c.short()) for c in rest if c.link]
        self.setText(
            f"{escape(self._prefix)} <b>{escape(self._name)}</b> ({escape(first.short())})"
            + (" · " + " · ".join(links) if links else "")
        )


class CitationsPanel(QWidget):
    """A numbered list of full references with links, for an About/Methods view."""

    def __init__(self, keys: Iterable[str] = (), parent: QWidget | None = None):
        super().__init__(parent)
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setOpenExternalLinks(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self._keys: tuple[str, ...] = ()
        theme_manager.connect(self._render)
        self.set_keys(keys)

    def set_keys(self, keys: Iterable[str]) -> None:
        self._keys = tuple(keys)
        self._render()

    def _render(self) -> None:
        items = []
        for c in citations.resolve(self._keys):
            text = escape(c.formatted().removesuffix(f" {c.link}") if c.link else c.formatted())
            if c.link:
                text += " " + _link(c.link, c.link)
            items.append(f"<li style='margin-bottom:6px;'>{text}</li>")
        self._label.setText(
            f"<ol style='color:{Colors.FG_PRIMARY};'>{''.join(items)}</ol>" if items else "No references."
        )
