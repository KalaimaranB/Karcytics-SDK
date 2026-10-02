"""Tests for karcytics_sdk.plugin.history.UndoHistory."""

from __future__ import annotations

import pytest

from karcytics_sdk.plugin.history import UndoHistory


def _history(*snapshots: str, max_steps: int = 100) -> UndoHistory[str]:
    h: UndoHistory[str] = UndoHistory(max_steps=max_steps)
    h.reset(snapshots[0])
    for s in snapshots[1:]:
        h.record(f"to {s}", s)
    return h


def test_empty_history_has_nothing_to_undo():
    h: UndoHistory[str] = UndoHistory()
    assert not h.is_initialized
    assert not h.can_undo()
    assert not h.can_redo()
    assert h.undo() is None
    assert h.redo() is None
    assert h.revision is None
    assert not h.is_clean


def test_first_record_without_reset_becomes_the_baseline():
    h: UndoHistory[str] = UndoHistory()
    assert h.record("x", "a") is False
    assert h.is_initialized
    assert not h.can_undo()
    assert not h.is_clean  # nothing was saved yet


def test_undo_redo_walks_the_stack():
    h = _history("a", "b", "c")
    assert h.undo_label() == "to c"
    assert h.undo() == "b"
    assert h.undo() == "a"
    assert h.undo() is None
    assert h.redo_label() == "to b"
    assert h.redo() == "b"
    assert h.redo() == "c"
    assert h.redo() is None


def test_recording_after_undo_discards_the_redo_branch():
    h = _history("a", "b", "c")
    h.undo()
    assert h.record("to d", "d") is True
    assert not h.can_redo()
    assert h.undo() == "b"
    assert h.undo() == "a"


def test_recording_an_unchanged_snapshot_is_not_a_step():
    h = _history("a", "b")
    assert h.record("noop", "b") is False
    assert h.undo() == "a"


def test_custom_equality_is_used_for_dedup():
    h: UndoHistory[dict] = UndoHistory(equals=lambda x, y: x["k"] == y["k"])
    h.reset({"k": 1, "noise": 1})
    assert h.record("same", {"k": 1, "noise": 2}) is False
    assert h.record("diff", {"k": 2, "noise": 2}) is True


def test_max_steps_drops_the_oldest_but_keeps_a_baseline():
    h = _history("a", "b", "c", "d", max_steps=2)
    assert h.undo() == "c"
    assert h.undo() == "b"
    assert h.undo() is None  # "a" was dropped; "b" is now the baseline


def test_max_steps_must_be_positive():
    with pytest.raises(ValueError):
        UndoHistory(max_steps=0)


# ── Clean state ────────────────────────────────────────────────────────


def test_reset_is_clean_by_default_and_any_step_dirties_it():
    h = _history("a")
    assert h.is_clean
    h.record("to b", "b")
    assert not h.is_clean


def test_undoing_back_to_the_saved_step_is_clean_again():
    h = _history("a", "b")
    h.mark_clean()
    h.record("to c", "c")
    assert not h.is_clean
    h.undo()
    assert h.is_clean
    h.undo()
    assert not h.is_clean
    h.redo()
    assert h.is_clean


def test_saved_step_on_a_discarded_branch_is_unreachable():
    h = _history("a", "b")
    h.mark_clean()  # saved at "b"
    h.undo()
    h.record("to b2", "b")  # same content, different step
    assert not h.is_clean


def test_mark_clean_at_an_earlier_revision_keeps_newer_steps_dirty():
    h = _history("a")
    started_at = h.revision  # a background save starts here...
    h.record("to b", "b")  # ...and the user edits before it finishes
    h.mark_clean(started_at)
    assert not h.is_clean
    h.undo()
    assert h.is_clean


def test_snapshots_lists_every_reachable_entry():
    h = _history("a", "b", "c")
    h.undo()
    assert h.snapshots() == ["a", "b", "c"]
    h.record("to d", "d")  # discards "c"
    assert h.snapshots() == ["a", "b", "d"]


def test_reset_with_clean_false_starts_dirty():
    h: UndoHistory[str] = UndoHistory()
    h.reset("a", clean=False)
    assert not h.is_clean
    h.mark_clean()
    assert h.is_clean
    h.mark_dirty()
    assert not h.is_clean


def test_revisions_are_unique_even_for_identical_content():
    h = _history("a", "b")
    rev_b = h.revision
    h.undo()
    h.record("to b again", "b")
    assert h.revision != rev_b


# ── Listeners & helpers ────────────────────────────────────────────────


def test_listeners_fire_on_every_change_and_can_be_removed():
    h: UndoHistory[str] = UndoHistory()
    calls: list[int] = []

    def listener() -> None:
        calls.append(1)

    h.add_listener(listener)
    h.reset("a")
    h.record("to b", "b")
    h.record("noop", "b")  # not a change
    h.undo()
    h.redo()
    h.mark_clean()
    assert len(calls) == 5
    h.remove_listener(listener)
    h.undo()
    assert len(calls) == 5


def test_replace_current_rewrites_without_adding_a_step():
    h = _history("a", "b")
    rev = h.revision
    h.replace_current("b+")
    assert h.revision == rev
    assert h.current is not None and h.current.snapshot == "b+"
    assert h.undo() == "a"
    assert h.redo() == "b+"


def test_len_and_debug_state():
    h = _history("a", "b", "c")
    h.undo()
    assert len(h) == 3
    assert h.debug_state()["redo"] == ["to c"]
