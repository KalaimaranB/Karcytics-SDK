from karcytics_sdk.plugin.ribbon import BioRibbon, ThemedToolbarContainer
from karcytics_sdk.plugin.theme_fallback import DynamicColors, theme_manager


def test_themed_toolbar_container_gets_an_objectname_scoped_stylesheet(qapp):
    widget = ThemedToolbarContainer()

    assert widget.objectName() == "ThemedToolbarContainer"
    style = widget.styleSheet()
    assert "QWidget#ThemedToolbarContainer" in style
    assert DynamicColors.BG_DARK in style
    assert DynamicColors.BORDER in style


def test_themed_toolbar_container_subclass_uses_its_own_class_name(qapp):
    class MyToolbar(ThemedToolbarContainer):
        pass

    widget = MyToolbar()

    assert widget.objectName() == "MyToolbar"
    assert "QWidget#MyToolbar" in widget.styleSheet()


def test_themed_toolbar_container_restyles_on_theme_change(qapp):
    widget = ThemedToolbarContainer()
    original_bg = DynamicColors.BG_DARK
    try:
        DynamicColors.BG_DARK = "#123456"
        theme_manager._apply_dynamic_styles()

        assert "#123456" in widget.styleSheet()
    finally:
        DynamicColors.BG_DARK = original_bg
        theme_manager._apply_dynamic_styles()


def test_bioribbon_creation(qapp):
    ribbon = BioRibbon()
    assert ribbon.run_button.text() == "🧬 Run"
    assert not ribbon.cancel_button.isEnabled()
    assert ribbon.cancel_button.isHidden()


def test_bioribbon_is_a_themed_toolbar_container(qapp):
    ribbon = BioRibbon()

    assert isinstance(ribbon, ThemedToolbarContainer)
    assert ribbon.objectName() == "BioRibbon"
    assert ribbon.styleSheet() != ""
