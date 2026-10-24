"""StageRail: a one-line rail of stages, each with an optional status."""

from __future__ import annotations

import pytest

from karcytics_sdk.plugin.stage_rail import StageRail, StageStatus


@pytest.fixture
def rail(qtbot):
    r = StageRail(["Images", "Lanes", "Bands"])
    qtbot.addWidget(r)
    return r


def test_one_button_per_stage_with_its_label(rail):
    assert [b.text() for b in rail.buttons] == ["Images", "Lanes", "Bands"]


def test_set_current_checks_exactly_one_stage(rail):
    rail.set_current(1)
    assert rail.current() == 1
    assert [b.isChecked() for b in rail.buttons] == [False, True, False]


def test_out_of_range_current_is_ignored(rail):
    rail.set_current(0)
    rail.set_current(7)
    assert rail.current() == 0


def test_clicking_a_stage_reports_its_index(rail, qtbot):
    with qtbot.waitSignal(rail.stage_clicked) as blocker:
        rail.buttons[2].click()
    assert blocker.args == [2]


def test_a_status_shows_as_a_glyph_and_in_the_tooltip(rail):
    rail.set_status(2, StageStatus.WARNING, ["Lane 3 is saturated"])
    assert rail.buttons[2].text() == "! Bands"
    tooltip = rail.buttons[2].toolTip()
    assert "needs attention" in tooltip
    assert "• Lane 3 is saturated" in tooltip
    assert rail.status(2) is StageStatus.WARNING


@pytest.mark.parametrize(
    ("status", "glyph"),
    [(StageStatus.TODO, "○"), (StageStatus.BUSY, "…"), (StageStatus.OK, "✓"), (StageStatus.ERROR, "✕")],
)
def test_every_status_has_its_own_glyph(rail, status, glyph):
    rail.set_status(0, status)
    assert rail.buttons[0].text() == f"{glyph} Images"


def test_clearing_a_status_restores_the_plain_label(rail):
    rail.set_status(1, StageStatus.ERROR, ["No lanes"])
    rail.set_status(1, StageStatus.NONE)
    assert rail.buttons[1].text() == "Lanes"
    assert "No lanes" not in rail.buttons[1].toolTip()


def test_the_status_is_spelled_out_for_screen_readers(rail):
    rail.set_status(0, StageStatus.OK)
    assert rail.buttons[0].accessibleDescription() == "Done"
