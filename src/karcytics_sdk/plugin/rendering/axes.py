"""Theme-aware styling for a matplotlib Axes.

Every plugin that draws with matplotlib ended up writing its own "colour the
ticks and spines to match the theme" helper. This is the one copy. It takes
colours as plain data so it works in headless renderers that must not import
the theme (they run off the GUI thread); widget code builds them with
`AxesColors.from_theme()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class AxesColors:
    """Colours for `style_axes()`. `background=None` leaves the faces as they are."""

    foreground: str
    border: str
    background: str | None = None

    @classmethod
    def from_theme(cls, *, with_background: bool = True) -> AxesColors:
        """The active theme's secondary text, border and dark background colours."""
        from ..theme_fallback import Colors  # noqa: PLC0415 - keeps this module importable headless

        return cls(Colors.FG_SECONDARY, Colors.BORDER, Colors.BG_DARK if with_background else None)


def style_axes(
    ax: Any,
    colors: AxesColors,
    *,
    open_frame: bool = False,
    grid: Literal["x", "y", "both"] | None = None,
) -> None:
    """Colour `ax`'s ticks and spines (and faces, if `colors.background` is set).

    Args:
        ax: The matplotlib Axes.
        colors: What to colour it with.
        open_frame: Hide the top and right spines.
        grid: Draw light grid lines along this axis (behind the data).
    """
    if colors.background is not None:
        ax.set_facecolor(colors.background)
        ax.figure.patch.set_facecolor(colors.background)
    ax.tick_params(colors=colors.foreground, labelsize=9)
    for name, spine in ax.spines.items():
        spine.set_color(colors.border)
        if open_frame and name in ("top", "right"):
            spine.set_visible(False)
    if grid is not None:
        ax.set_axisbelow(True)
        ax.grid(True, axis=grid, color=colors.border, linewidth=0.5, alpha=0.6)
