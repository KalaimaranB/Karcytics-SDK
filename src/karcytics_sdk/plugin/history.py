"""Undo/redo history owned by the plugin's own process.

An isolated plugin never has the Hub's `karcytics.core.history_manager`
importable — its `.venv` only has `karcytics_sdk` — so the history has to
live here, in the SDK, for undo/redo to work at all there.

`UndoHistory` is deliberately Qt-free and snapshot-agnostic: it stores
whatever opaque snapshot the plugin hands it (a dict, a frozen dataclass,
...) and never inspects it beyond an equality check, so the plugin alone
decides what a step captures and how it is restored.

It also answers "does the workspace have unsaved changes?" — every recorded
step gets a unique, monotonically increasing revision, and `mark_clean()`
remembers the revision that was last saved. Undoing back to exactly that
step reads as clean again; recording a new step after undoing past it
discards that branch, so the saved revision becomes unreachable and the
workspace stays dirty until the next save — the same semantics as
`QUndoStack.setClean()`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")

#: Default cap on the number of undoable steps kept in memory.
DEFAULT_MAX_STEPS = 100


@dataclass(frozen=True)
class HistoryEntry(Generic[T]):
    """One point in the history: the state *after* the step named `label`."""

    revision: int
    label: str
    snapshot: T


class UndoHistory(Generic[T]):
    """A linear undo/redo stack of snapshots with clean-state tracking.

    The stack always holds at least one entry once `reset()` has been called
    (the baseline — the state right after loading or creating a workspace).
    The *current* entry is the one the plugin's live state matches; `undo()`
    and `redo()` move that pointer and return the snapshot the plugin must
    restore.
    """

    def __init__(
        self,
        max_steps: int = DEFAULT_MAX_STEPS,
        equals: Callable[[T, T], bool] | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self._max_steps = max_steps
        self._equals: Callable[[T, T], bool] = equals or (lambda a, b: a == b)
        self._undo: list[HistoryEntry[T]] = []
        self._redo: list[HistoryEntry[T]] = []
        self._next_revision = 0
        self._clean_revision: int | None = None
        self._listeners: list[Callable[[], None]] = []

    # ── Listeners ───────────────────────────────────────────────────

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Call `callback()` after every change to the stack or clean state."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    # ── Recording ───────────────────────────────────────────────────

    @property
    def is_initialized(self) -> bool:
        """Whether `reset()` has set a baseline yet."""
        return bool(self._undo)

    def _new_entry(self, label: str, snapshot: T) -> HistoryEntry[T]:
        entry = HistoryEntry(self._next_revision, label, snapshot)
        self._next_revision += 1
        return entry

    def reset(self, snapshot: T, *, clean: bool = True) -> None:
        """Discard all history and make `snapshot` the new baseline.

        Call after loading or creating a workspace. `clean=True` (the default)
        also marks the baseline as saved.
        """
        self._undo = [self._new_entry("", snapshot)]
        self._redo = []
        self._clean_revision = self._undo[0].revision if clean else None
        self._notify()

    def record(self, label: str, snapshot: T) -> bool:
        """Push the state after a user action as a new undoable step.

        Returns False (and records nothing) if `snapshot` equals the current
        state — an action that changed nothing is not a step. Clears the
        redo stack. If no baseline exists yet, `snapshot` becomes it.
        """
        if not self._undo:
            self.reset(snapshot, clean=False)
            return False
        if self._equals(self._undo[-1].snapshot, snapshot):
            return False

        self._undo.append(self._new_entry(label, snapshot))
        self._redo.clear()
        if len(self._undo) > self._max_steps + 1:
            # +1: the baseline itself isn't an undoable step.
            del self._undo[0]
        self._notify()
        return True

    # ── Navigation ──────────────────────────────────────────────────

    def can_undo(self) -> bool:
        return len(self._undo) > 1

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        """Label of the step `undo()` would revert, or "" if none."""
        return self._undo[-1].label if self.can_undo() else ""

    def redo_label(self) -> str:
        """Label of the step `redo()` would re-apply, or "" if none."""
        return self._redo[-1].label if self._redo else ""

    def undo(self) -> T | None:
        """Step back; returns the snapshot to restore, or None if at the start."""
        if not self.can_undo():
            return None
        self._redo.append(self._undo.pop())
        self._notify()
        return self._undo[-1].snapshot

    def redo(self) -> T | None:
        """Step forward; returns the snapshot to restore, or None if at the end."""
        if not self._redo:
            return None
        self._undo.append(self._redo.pop())
        self._notify()
        return self._undo[-1].snapshot

    @property
    def current(self) -> HistoryEntry[T] | None:
        return self._undo[-1] if self._undo else None

    def replace_current(self, snapshot: T) -> None:
        """Overwrite the current entry's snapshot without creating a step.

        For state that changed in a way that must not be undoable on its own
        but must not be *lost* by a later undo/redo either (e.g. results of a
        background computation folded into the current step).
        """
        if not self._undo:
            self.reset(snapshot, clean=False)
            return
        top = self._undo[-1]
        self._undo[-1] = HistoryEntry(top.revision, top.label, snapshot)

    # ── Clean state ─────────────────────────────────────────────────

    @property
    def revision(self) -> int | None:
        """Revision of the current entry (None before `reset()`)."""
        return self._undo[-1].revision if self._undo else None

    def mark_clean(self, revision: int | None = None) -> None:
        """Record which entry was last saved to disk.

        Defaults to the current entry. Pass the `revision` read when a save
        *started* for a save that runs in the background: if the user made
        another step while it was writing, the workspace correctly stays
        dirty afterwards instead of that newer step being marked saved.
        """
        self._clean_revision = self.revision if revision is None else revision
        self._notify()

    def mark_dirty(self) -> None:
        """Forget the saved revision, e.g. after a failed save."""
        self._clean_revision = None
        self._notify()

    @property
    def is_clean(self) -> bool:
        return self._undo != [] and self._clean_revision == self.revision

    def snapshots(self) -> list[T]:
        """Every snapshot still reachable by undo or redo, oldest first.

        Lets a plugin keep heavy data that snapshots only reference by id
        (loaded event data, cached results) alive exactly as long as some
        step can still bring it back, and release it once none can.
        """
        return [e.snapshot for e in self._undo] + [e.snapshot for e in reversed(self._redo)]

    def __len__(self) -> int:
        return len(self._undo) + len(self._redo)

    def debug_state(self) -> dict[str, Any]:
        """Compact description for logging/tests."""
        return {
            "undo": [e.label for e in self._undo],
            "redo": [e.label for e in self._redo],
            "revision": self.revision,
            "clean_revision": self._clean_revision,
        }
