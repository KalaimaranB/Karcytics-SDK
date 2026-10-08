"""Runs the evaluator's rules over a repo and collects the result."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .functional_rules import run_functional_rules
from .model import Finding, Mode, Severity
from .org_rules import run_org_rules
from .registry import RULES
from .repo import RepoLayout

_RUNNERS = {Mode.ORG: run_org_rules, Mode.FUNCTIONAL: run_functional_rules}


@dataclass
class Report:
    """Everything one evaluator run found."""

    root: Path
    plugin_id: str | None
    modes: tuple[Mode, ...]
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)

    def count(self, severity: Severity) -> int:
        return sum(1 for finding in self.findings if finding.severity is severity)

    def by_mode(self, mode: Mode) -> list[Finding]:
        return [finding for finding in self.findings if RULES[finding.rule_id].mode is mode]

    def passed(self, strict: bool = False) -> bool:
        """No errors - and, when `strict`, no warnings either. Info never fails a run."""
        failing = self.count(Severity.ERROR) + (self.count(Severity.WARNING) if strict else 0)
        return failing == 0


def _prefixes(values: Iterable[str]) -> tuple[str, ...]:
    """Normalised rule-id prefixes, with blanks (e.g. from splitting "") dropped."""
    return tuple(value.strip().upper() for value in values if value.strip())


def evaluate_repo(
    root: Path | str,
    modes: Iterable[Mode] = (Mode.ORG, Mode.FUNCTIONAL),
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
) -> Report:
    """Statically evaluate the repo at `root` against the SDK's conventions.

    Args:
        root: Path to the repo. It is only read, never imported or executed.
        modes: Which rule families to run.
        select: Rule-id prefixes to keep (e.g. `["KS2"]`); empty keeps all.
        ignore: Rule-id prefixes to drop.
    """
    layout = RepoLayout.discover(root)
    modes = tuple(modes)
    select, ignore = _prefixes(select), _prefixes(ignore)

    findings: list[Finding] = []
    for mode in modes:
        findings.extend(_RUNNERS[mode](layout))
    if select:
        findings = [f for f in findings if f.rule_id.startswith(select)]
    findings = [f for f in findings if not f.rule_id.startswith(ignore)]
    findings.sort(key=lambda f: f.sort_key)

    scanned = len(layout.source_files) if Mode.FUNCTIONAL in modes else 0
    return Report(layout.root, layout.plugin_id, modes, scanned, findings)
