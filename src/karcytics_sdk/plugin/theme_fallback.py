"""Fallback theme stubs for plugin standalone testing.

`Colors`/`theme_manager` here resolve two different ways depending on which
process imports them, transparently: inside the Hub's own process (where
`karcytics.ui.theme` is importable — the Hub installs this SDK as a plain
editable dependency, not a separate `.venv`) they delegate to the Hub's real
`Colors`/`theme_manager`; inside an isolated plugin's own `.venv` (where that
module can never be imported) they fall back to `DynamicColors`, kept live
via the `theme_changed` request handler in `ui_daemon_runtime.py`. This is
why widgets ported into the SDK that need styling (`cyto_character.py`,
`course_complete_overlay.py`, `tutorial_overlay.py`) import `Colors`/
`theme_manager` from here directly rather than taking a style provider as a
constructor argument — one canonical widget works correctly in both
processes without either side needing to know which one it's in.
"""

import sys
import weakref

from PyQt6.QtCore import QObject, pyqtSignal

_SHORT_HEX_LEN = 3
_LINEAR_THRESHOLD = 0.03928


def _wcag_relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == _SHORT_HEX_LEN:
        hex_color = "".join(c * 2 for c in hex_color)
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def linearize(c: float) -> float:
        return c / 12.92 if c <= _LINEAR_THRESHOLD else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = linearize(r), linearize(g), linearize(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def get_contrast_text_color(bg_hex: str) -> str:
    """Pick whichever of near-black/near-white contrasts better against a background hex.

    Selected-item/highlight QSS paints text over the theme's *accent* color, not
    a background token — hardcoding that text color to a token like BG_DARKEST
    silently breaks wherever that token isn't actually dark (e.g. `DynamicColors
    .LIGHT`, where `BG_DARKEST` is `"#f6f8fa"`, near-white). A simple perceived-
    brightness average (no gamma correction) also isn't enough: saturated colors
    like the default cyan accent (`#00bcd4`) score as "mid-bright" under that
    formula and get white text, but WCAG's gamma-corrected relative luminance
    puts it high enough that black text is actually ~4x higher contrast. Compute
    both candidates' WCAG contrast ratios against the real background and return
    the winner, so this stays correct for any accent/background a theme picks.
    """
    bg_luminance = _wcag_relative_luminance(bg_hex)
    contrast_with_black = (bg_luminance + 0.05) / 0.05
    contrast_with_white = 1.05 / (bg_luminance + 0.05)
    return "#0d1117" if contrast_with_black >= contrast_with_white else "#ffffff"


class DynamicColors:
    DARK = {
        "BG_DARKEST": "#0d1117",
        "BG_DARKER": "#0d1117",
        "BG_DARK": "#161b22",
        "BG_MEDIUM": "#21262d",
        "BG_LIGHT": "#30363d",
        "FG_PRIMARY": "#e6edf3",
        "FG_SECONDARY": "#8b949e",
        "FG_DISABLED": "#484f58",
        "BORDER": "#30363d",
        "BORDER_FOCUS": "#58a6ff",
        "ACCENT_PRIMARY": "#00bcd4",
        "ACCENT_PRIMARY_HOVER": "#0097a7",
        "ACCENT_NEGATIVE": "#ef5350",
        "ACCENT_SUCCESS": "#238636",
        "ACCENT_WARNING": "#d29922",
        "ACCENT_DANGER": "#f85149",
        "GLOW_COLOR": "rgba(0, 188, 212, 0.4)",
        "DNA_PRIMARY": "#00bcd4",
        "DNA_SECONDARY": "#a371f7",
        "CHART_COLORS": ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#a371f7", "#f778ba"],
    }
    LIGHT = {
        "BG_DARKEST": "#f6f8fa",
        "BG_DARKER": "#f6f8fa",
        "BG_DARK": "#ffffff",
        "BG_MEDIUM": "#f0f6fc",
        "BG_LIGHT": "#e1e4e8",
        "FG_PRIMARY": "#24292e",
        "FG_SECONDARY": "#57606a",
        "FG_DISABLED": "#959da5",
        "BORDER": "#d0d7de",
        "BORDER_FOCUS": "#0969da",
        "ACCENT_PRIMARY": "#00bcd4",
        "ACCENT_PRIMARY_HOVER": "#0097a7",
        "ACCENT_NEGATIVE": "#cf222e",
        "ACCENT_SUCCESS": "#1a7f37",
        "ACCENT_WARNING": "#9a6700",
        "ACCENT_DANGER": "#cf222e",
        "GLOW_COLOR": "rgba(0, 188, 212, 0.2)",
        "DNA_PRIMARY": "#00bcd4",
        "DNA_SECONDARY": "#8250df",
        "CHART_COLORS": ["#0969da", "#2da44e", "#bf8700", "#cf222e", "#8250df", "#bf3989"],
    }

    _mode = "dark"

    @classmethod
    def set_theme(cls, theme_name: str):
        cls._mode = "light" if "light" in theme_name.lower() else "dark"
        palette = cls.LIGHT if cls._mode == "light" else cls.DARK
        for k, v in palette.items():
            setattr(cls, k, v)

    @classmethod
    def update_from(cls, colors: dict[str, str]) -> None:
        """Overwrite the active palette with a Hub-pushed color map.

        An isolated plugin has no other way to know the Hub's actual live
        colors — the DARK/LIGHT dicts above are only a fallback for when no
        Hub connection exists at all (standalone dev/test runs). Only keys
        this fallback already recognizes are applied; the Hub's `Colors`
        class has many more (CHART_COLORS, ACCENT_SUCCESS, ...) that this
        smaller palette has no slot for, so those are silently ignored
        rather than growing this class's attribute set unpredictably.
        """
        known_keys = cls.DARK.keys() | cls.LIGHT.keys()
        for k, v in colors.items():
            if k in known_keys:
                setattr(cls, k, v)


class _ColorsProxy:
    def __getattr__(self, name):
        if "karcytics.ui.theme" in sys.modules:
            return getattr(sys.modules["karcytics.ui.theme"].Colors, name)
        return getattr(DynamicColors, name, "#0d1117")

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        elif "karcytics.ui.theme" in sys.modules:
            setattr(sys.modules["karcytics.ui.theme"].Colors, name, value)
        else:
            setattr(DynamicColors, name, value)


Colors = _ColorsProxy()
DynamicColors.set_theme("dark")


class _Fonts:
    """Mirrors the Hub's real `_Fonts` (`karcytics.ui.theme.Fonts`) closely
    enough that shared widgets (preferences pages, toasts, ...) can use the
    same `Fonts.H2`/`Fonts.BODY`/... calls in either process — see this
    module's own docstring for why `Colors`/`theme_manager` follow the same
    dual-resolution pattern. `H1`/`H2`/.../`CAPTION` are `@property`, exactly
    like the Hub's own class, so `Fonts` below must be an *instance* — a bare
    class reference would hand callers the `property` descriptor object
    instead of a `QFont`.
    """

    SIZE_SMALL = 11
    SIZE_NORMAL = 13
    SIZE_LARGE = 18
    SIZE_XLARGE = 24
    FAMILY_UI = "Inter, sans-serif"
    FAMILY_HEADINGS = "Inter, sans-serif"

    @property
    def H1(self):  # noqa: N802
        from PyQt6.QtGui import QFont

        return QFont(self.FAMILY_HEADINGS, self.SIZE_XLARGE, QFont.Weight.Bold)

    @property
    def H2(self):  # noqa: N802
        from PyQt6.QtGui import QFont

        return QFont(self.FAMILY_HEADINGS, self.SIZE_LARGE, QFont.Weight.Bold)

    @property
    def H3(self):  # noqa: N802
        from PyQt6.QtGui import QFont

        return QFont(self.FAMILY_HEADINGS, self.SIZE_NORMAL, QFont.Weight.Bold)

    @property
    def BODY(self):  # noqa: N802
        from PyQt6.QtGui import QFont

        return QFont(self.FAMILY_UI, self.SIZE_NORMAL)

    @property
    def CAPTION(self):  # noqa: N802
        from PyQt6.QtGui import QFont

        return QFont(self.FAMILY_UI, self.SIZE_SMALL)


# Singleton instance for static-like access, mirroring the Hub's own
# `Fonts = _Fonts()` (karcytics/ui/theme.py) — see _Fonts's docstring.
Fonts = _Fonts()


class _ThemeManagerProxy(QObject):
    theme_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._local_theme_name = "Karcytics Default"
        self._dynamic_widgets = weakref.WeakKeyDictionary()

    def connect(self, slot):
        if "karcytics.ui.theme" in sys.modules:
            sys.modules["karcytics.ui.theme"].theme_manager.theme_changed.connect(slot)
        self.theme_changed.connect(slot)

    def set_theme(self, theme_name: str):
        DynamicColors.set_theme(theme_name)
        self._local_theme_name = theme_name
        self.theme_changed.emit()

    @property
    def current_theme_name(self) -> str:
        if "karcytics.ui.theme" in sys.modules:
            return sys.modules["karcytics.ui.theme"].theme_manager.current_theme_name
        return self._local_theme_name

    def apply_style(self, widget, style_template: str) -> None:
        """Applies a dynamic stylesheet to a widget and tracks it for future
        theme changes — same contract as the Hub's real
        `ThemeManager.apply_style()`, so widgets shared between both
        processes (see this module's docstring) need no special-casing.
        """
        self._dynamic_widgets[widget] = style_template
        self._set_widget_style(widget, style_template)

    def _set_widget_style(self, widget, style_template: str) -> None:
        if "karcytics.ui.theme" in sys.modules:
            colors_cls = sys.modules["karcytics.ui.theme"].Colors
        else:
            colors_cls = DynamicColors

        color_dict = {k: getattr(colors_cls, k) for k in dir(colors_cls) if not k.startswith("_")}

        compiled_qss = style_template
        for key, val in color_dict.items():
            compiled_qss = compiled_qss.replace(f"{{{key}}}", str(val))

        import contextlib

        with contextlib.suppress(RuntimeError):
            widget.setStyleSheet(compiled_qss)

    def _apply_dynamic_styles(self) -> None:
        """Re-evaluates every tracked inline style — call after `DynamicColors`
        is updated so already-styled widgets pick up the new palette.
        """
        for widget, style_template in list(self._dynamic_widgets.items()):
            self._set_widget_style(widget, style_template)


theme_manager = _ThemeManagerProxy()
