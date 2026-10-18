"""The baseline ratchet: existing debt may shrink, never grow.

A baseline file records, per source file, how many findings of each rule
it had (or, for a measured rule such as KS300 "long file", the measurement
itself). A later run fails when any of those goes up or a new one appears,
and reports what went down so the baseline can be tightened. Only errors
and warnings are tracked; info never blocks.

    {
      "version": 1,
      "files": {
        "src/karcytics_plugins/demo/ui/panel.py": {"KS240": 12, "KS301:Panel": 41}
      }
    }

That keeps the baseline readable in a diff: tightening it is a visible,
reviewable change, and so is accepting new debt (`--reset-baseline`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .model import Finding, Severity

BASELINE_FILE = ".karcytics-baseline.json"
_VERSION = 1
_TRACKED = (Severity.ERROR, Severity.WARNING)

Counts = dict[str, dict[str, int]]  # path -> baseline key -> count or measurement


def measure(findings: Iterable[Finding]) -> Counts:
    """What the baseline would record for these findings."""
    counts: Counts = {}
    for finding in findings:
        if finding.severity not in _TRACKED:
            continue
        per_file = counts.setdefault(finding.path, {})
        key = finding.baseline_key
        if finding.metric is None:
            per_file[key] = per_file.get(key, 0) + 1
        else:
            per_file[key] = max(per_file.get(key, 0), finding.metric)
    return counts


def load(path: Path) -> Counts:
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files", {}) if isinstance(data, dict) else {}
    return {p: {k: int(v) for k, v in keys.items()} for p, keys in files.items()}


def save(path: Path, counts: Counts) -> None:
    files = {p: dict(sorted(keys.items())) for p, keys in sorted(counts.items()) if keys}
    path.write_text(json.dumps({"version": _VERSION, "files": files}, indent=2) + "\n", encoding="utf-8")


def tighten(baseline: Counts, current: Counts, scope: set[str] | None = None) -> Counts:
    """Lower every entry to its current value. Never raises one or adds one.

    Paths outside `scope` (when given) are kept as they are, so a run over a
    few files cannot drop the rest of the baseline.
    """
    tightened: Counts = {}
    for path, keys in baseline.items():
        if scope is not None and path not in scope:
            tightened[path] = dict(keys)
            continue
        now = current.get(path, {})
        kept = {key: min(value, now.get(key, 0)) for key, value in keys.items()}
        kept = {key: value for key, value in kept.items() if value > 0}
        if kept:
            tightened[path] = kept
    return tightened


@dataclass(frozen=True)
class Change:
    """One baseline entry whose current value differs from the recorded one."""

    path: str
    key: str
    current: int
    allowed: int


@dataclass
class Comparison:
    exceeded: list[Change] = field(default_factory=list)
    improved: list[Change] = field(default_factory=list)

    def is_exceeded(self, finding: Finding) -> bool:
        return any(c.path == finding.path and c.key == finding.baseline_key for c in self.exceeded)


def compare(baseline: Counts, current: Counts, scope: set[str] | None = None) -> Comparison:
    """Entries that went up (or are new) and entries that went down."""
    result = Comparison()
    paths = set(current) | set(baseline)
    if scope is not None:
        paths &= scope
    for path in sorted(paths):
        now, allowed = current.get(path, {}), baseline.get(path, {})
        for key in sorted(set(now) | set(allowed)):
            value, limit = now.get(key, 0), allowed.get(key, 0)
            if value > limit:
                result.exceeded.append(Change(path, key, value, limit))
            elif value < limit:
                result.improved.append(Change(path, key, value, limit))
    return result
