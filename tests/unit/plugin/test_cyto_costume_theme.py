"""Cyto's costume follows the theme's name, which an isolated plugin only
learns from the Hub (set_theme_name) — colors alone can't name a theme.
"""

from __future__ import annotations

from karcytics_sdk.plugin.cyto_character import CytoWidget
from karcytics_sdk.plugin.cyto_costumes import CostumeFactory
from karcytics_sdk.plugin.theme_fallback import theme_manager


def test_a_named_theme_change_reaches_cytos_costume():
    original = theme_manager.current_theme_name
    cyto = CytoWidget()
    try:
        theme_manager.set_theme_name("Galactic Dark Side")
        theme_manager.theme_changed.emit()

        expected = type(CostumeFactory.get_costume("Galactic Dark Side"))
        assert theme_manager.current_theme_name == "Galactic Dark Side"
        assert isinstance(cyto.current_costume, expected)
        assert type(cyto.current_costume) is not type(CostumeFactory.get_costume("Karcytics Default"))
    finally:
        theme_manager.set_theme_name(original)
        theme_manager.theme_changed.emit()
        cyto.deleteLater()
