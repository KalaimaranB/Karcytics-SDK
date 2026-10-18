"""Runs the evaluator's rules over a repo and collects the result."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import baseline as baseline_mod
from .design_rules import run_design_rules
from .functional_rules import panel_classes, run_functional_rules
from .model import Finding, Mode, Severity
from .org_rules import run_org_rules
from .registry import RULES, make_finding
from .repo import RepoLayout
from .source import SourceFile
from .test_rules import run_test_rules


@dataclass
class Report:
    """Everything one evaluator run found."""

    root: Path
    plugin_id: str | None
    modes: tuple[Mode, ...]
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)
    # Set when the run was compared against a baseline.
    comparison: baseline_mod.Comparison | None = None
    # Set when the run covered only some files (`--files`).
    scope: set[str] | None = None

    def count(self, severity: Severity) -> int:
        return sum(1 for finding in self.findings if finding.severity is severity)

    def by_mode(self, mode: Mode) -> list[Finding]:
        return [finding for finding in self.findings if RULES[finding.rule_id].mode is mode]

    def passed(self, strict: bool = False) -> bool:
        """Whether the run passes: no errors, and then...

        - against a baseline: nothing over it (warnings below it are tolerated);
        - otherwise, when `strict`: no warnings either.

        Info never fails a run.
        """
        if self.count(Severity.ERROR):
            return False
        if self.comparison is not None:
            return not self.comparison.exceeded
        return not (strict and self.count(Severity.WARNING))

    def compare_to(self, recorded: baseline_mod.Counts) -> None:
        self.comparison = baseline_mod.compare(recorded, baseline_mod.measure(self.findings), self.scope)

    def blocking(self) -> list[Finding]:
        """The findings that make the run fail, or would with `strict`."""
        if self.comparison is None:
            return [f for f in self.findings if f.severity is not Severity.INFO]
        comparison = self.comparison
        return [f for f in self.findings if f.severity is Severity.ERROR or comparison.is_exceeded(f)]


def _prefixes(values: Iterable[str]) -> tuple[str, ...]:
    """Normalised rule-id prefixes, with blanks (e.g. from splitting "") dropped."""
    return tuple(value.strip().upper() for value in values if value.strip())


def _parse_all(layout: RepoLayout, paths: Iterable[Path]) -> tuple[list[SourceFile], list[Finding]]:
    sources: list[SourceFile] = []
    failures: list[Finding] = []
    for path in paths:
        rel = layout.rel(path)
        try:
            sources.append(SourceFile.parse(path, rel, layout.zone(path) if not layout.is_test(path) else "tests"))
        except SyntaxError as exc:
            failures.append(make_finding("KS200", rel, f"syntax error: {exc.msg}", line=exc.lineno))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            failures.append(make_finding("KS200", rel, f"could not read file: {exc}"))
    return sources, failures


def _scoped_paths(layout: RepoLayout, files: Iterable[Path | str]) -> tuple[list[Path], list[Path]]:
    """The given files that the evaluator reads, split into source and test files."""
    wanted = {Path(f).resolve() for f in files}
    sources = [p for p in layout.source_files if p.resolve() in wanted]
    tests = [p for p in layout.test_files if p.resolve() in wanted]
    return sources, tests


def evaluate_repo(  # noqa: PLR0913 - one keyword per CLI option
    root: Path | str,
    modes: Iterable[Mode] = (Mode.ORG, Mode.FUNCTIONAL, Mode.DESIGN),
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    *,
    files: Iterable[Path | str] | None = None,
    baseline: baseline_mod.Counts | None = None,
) -> Report:
    """Statically evaluate the repo at `root` against the SDK's conventions.

    Args:
        root: Path to the repo. It is only read, never imported or executed.
        modes: Which rule families to run.
        select: Rule-id prefixes to keep (e.g. `["KS2"]`); empty keeps all.
        ignore: Rule-id prefixes to drop.
        files: Only check these files (fast, for an editor or agent hook).
            Repo-level checks - the org rules and anything needing every
            file at once - are skipped.
        baseline: Recorded counts to compare against (see `baseline.py`).
    """
    layout = RepoLayout.discover(root)
    modes = tuple(modes)
    select, ignore = _prefixes(select), _prefixes(ignore)
    whole_repo = files is None

    if whole_repo:
        source_paths, test_paths = layout.source_files, layout.test_files
    else:
        source_paths, test_paths = _scoped_paths(layout, files or ())
    sources, failures = _parse_all(layout, source_paths)
    tests, _ = _parse_all(layout, test_paths)

    findings: list[Finding] = []
    if Mode.ORG in modes and whole_repo:
        findings.extend(run_org_rules(layout))
    if Mode.FUNCTIONAL in modes:
        findings.extend(failures)
        findings.extend(run_functional_rules(layout, sources, whole_repo=whole_repo))
        if tests:
            all_sources = sources if whole_repo else _parse_all(layout, layout.source_files)[0]
            findings.extend(run_test_rules(tests, panel_classes(all_sources)))
    if Mode.DESIGN in modes:
        findings.extend(run_design_rules(sources, whole_repo=whole_repo, analysis_only=layout.analysis_only))

    by_path = {source.rel: source for source in [*sources, *tests]}
    findings = [f for f in findings if not (f.path in by_path and by_path[f.path].allows(f.rule_id, f.line))]
    if select:
        findings = [f for f in findings if f.rule_id.startswith(select)]
    findings = [f for f in findings if not f.rule_id.startswith(ignore)]
    findings.sort(key=lambda f: f.sort_key)

    scope = None if whole_repo else {s.rel for s in [*sources, *tests]}
    scanned = len(sources) if Mode.FUNCTIONAL in modes or Mode.DESIGN in modes else 0
    report = Report(layout.root, layout.plugin_id, modes, scanned, findings, scope=scope)
    if baseline is not None:
        report.compare_to(_filter_baseline(baseline, select, ignore))
    return report


def _filter_baseline(
    recorded: baseline_mod.Counts, select: tuple[str, ...], ignore: tuple[str, ...]
) -> baseline_mod.Counts:
    """The recorded entries for the rules this run reports on."""

    def kept(key: str) -> bool:
        return (not select or key.startswith(select)) and not key.startswith(ignore)

    return {path: {k: v for k, v in keys.items() if kept(k)} for path, keys in recorded.items()}
