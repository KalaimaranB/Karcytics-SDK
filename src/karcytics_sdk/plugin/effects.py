from PySide6.QtGui import QColor  # noqa: D100
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsItem, QWidget


def apply_glow_effect(
    widget: QWidget | QGraphicsItem, color: QColor | str, blur_radius: int = 15
) -> QGraphicsDropShadowEffect:
    """Applies a glow (DropShadow) effect to a widget and returns the effect instance."""
    glow = QGraphicsDropShadowEffect()
    glow.setBlurRadius(blur_radius)
    glow.setOffset(0, 0)
    if isinstance(color, str):
        color = QColor(color)
    glow.setColor(color)
    widget.setGraphicsEffect(glow)
    return glow


def apply_shadow_effect(
    widget: QWidget,
    blur_radius: int = 15,
    x_offset: int = 0,
    y_offset: int = 4,
    color: QColor | str = "#80000000",
) -> QGraphicsDropShadowEffect:
    """Applies a standard drop shadow to a widget and returns the effect instance."""
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(blur_radius)
    shadow.setOffset(x_offset, y_offset)
    if isinstance(color, str):
        color = QColor(color)
    shadow.setColor(color)
    widget.setGraphicsEffect(shadow)
    return shadow
