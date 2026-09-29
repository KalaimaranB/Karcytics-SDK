"""Tests for `repopulate_combo`, the shared combo-rebuild helper in
`karcytics_sdk.plugin.components`.

Replaces the blockSignals/clear/addItem-loop/findData dance that used to be
duplicated across ~14 UI call sites in the flow-cytometry plugin (and,
being pure `QComboBox` logic, any other plugin with the same need). The
behavior that matters: (1) items end up correct, (2) a still-valid prior
selection is restored, (3) nothing is touched at all — no clear, no
blockSignals, no signal emission — when neither the item set nor the
resolved selection actually changed.
"""

import pytest
from PyQt6.QtWidgets import QComboBox

from karcytics_sdk.plugin.components import repopulate_combo


@pytest.fixture
def combo(qtbot):
    box = QComboBox()
    qtbot.addWidget(box)
    return box


def test_populates_empty_combo_with_given_items(combo):
    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B", "s2")])

    assert combo.count() == 2
    assert combo.itemText(0) == "Sample A"
    assert combo.itemData(0) == "s1"
    assert combo.itemText(1) == "Sample B"
    assert combo.itemData(1) == "s2"


def test_restores_previously_selected_item_by_data(combo):
    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B", "s2")])
    combo.setCurrentIndex(1)  # "Sample B" / "s2"

    # Rebuild with the same items, e.g. after a sample was renamed elsewhere
    # and the caller wants to keep "s2" selected.
    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B (renamed)", "s2")], restore_data="s2")

    assert combo.currentData() == "s2"
    assert combo.currentText() == "Sample B (renamed)"


def test_restore_data_no_longer_present_leaves_default_selection(combo):
    repopulate_combo(
        combo,
        [("Sample A", "s1"), ("Sample B", "s2")],
        restore_data="s3",  # doesn't exist
    )

    # findData returns -1, so setCurrentIndex is never called — Qt's own
    # clear()/addItem() default (index 0) stands.
    assert combo.currentIndex() == 0


def test_does_not_emit_signals_while_rebuilding(combo):
    fired = []
    combo.currentIndexChanged.connect(lambda idx: fired.append(idx))

    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B", "s2")], restore_data="s2")

    assert fired == []


def test_is_a_true_no_op_when_items_and_selection_are_unchanged(combo, monkeypatch):
    items = [("Sample A", "s1"), ("Sample B", "s2")]
    repopulate_combo(combo, items, restore_data="s1")

    block_calls = []
    monkeypatch.setattr(combo, "blockSignals", lambda b: block_calls.append(b))
    clear_calls = []
    monkeypatch.setattr(combo, "clear", lambda: clear_calls.append(True))

    # Same items, same already-current selection — a tab switch re-triggering
    # a refresh with nothing actually different.
    repopulate_combo(combo, items, restore_data="s1")

    assert block_calls == []
    assert clear_calls == []


def test_rebuilds_when_only_the_selection_target_changed(combo):
    items = [("Sample A", "s1"), ("Sample B", "s2")]
    repopulate_combo(combo, items, restore_data="s1")
    assert combo.currentData() == "s1"

    # Same item set, but caller now wants "s2" selected — must still update
    # the selection even though clear()/addItem() would be wasted work.
    repopulate_combo(combo, items, restore_data="s2")
    assert combo.currentData() == "s2"


def test_shrinking_the_item_set_removes_stale_entries(combo):
    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B", "s2")])

    repopulate_combo(combo, [("Sample A", "s1")])

    assert combo.count() == 1
    assert combo.itemData(0) == "s1"


def test_no_restore_data_leaves_selection_unspecified(combo):
    repopulate_combo(combo, [("All Events", None), ("Lymphocytes", "g1")])

    assert combo.count() == 2
    assert combo.currentIndex() == 0


def test_no_restore_data_does_not_touch_an_existing_selection(combo):
    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B", "s2")])
    combo.setCurrentIndex(1)

    repopulate_combo(combo, [("Sample A", "s1"), ("Sample B", "s2")])

    assert combo.currentIndex() == 1


def test_handles_empty_items(combo):
    repopulate_combo(combo, [])

    assert combo.count() == 0
