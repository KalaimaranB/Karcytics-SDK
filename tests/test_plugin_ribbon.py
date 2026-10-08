from PySide6.QtWidgets import QWidget

from karcytics_sdk.plugin.ribbon import BioRibbon


def test_bioribbon_creation(qapp):
    ribbon = BioRibbon()
    assert ribbon.run_button.text() == "🧬 Run"
    assert not ribbon.cancel_button.isEnabled()
    assert ribbon.cancel_button.isHidden()


def test_bioribbon_run_lifecycle(qapp):
    ribbon = BioRibbon()

    # Simulate start run
    ribbon.start_run()
    assert not ribbon.run_button.isEnabled()
    assert ribbon.run_button.isHidden()
    assert ribbon.cancel_button.isEnabled()
    assert not ribbon.cancel_button.isHidden()

    # Simulate cancel run
    ribbon.cancel_run()
    assert not ribbon.cancel_button.isEnabled()
    assert ribbon.cancel_button.isHidden()
    assert ribbon.run_button.isEnabled()
    assert not ribbon.run_button.isHidden()

    # Simulate finish run
    ribbon.start_run()
    ribbon.finish_run()
    assert not ribbon.cancel_button.isEnabled()
    assert ribbon.run_button.isEnabled()


def test_bioribbon_add_widgets(qapp):
    ribbon = BioRibbon()
    ribbon.add_separator()
    w = QWidget()
    ribbon.add_widget(w)
    ribbon.add_stretch()
