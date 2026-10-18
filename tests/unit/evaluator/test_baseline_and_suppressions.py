import argparse
import io
import json

import pytest

from karcytics_sdk.cli.commands.diagnostics import doctor_plugin, setup_diagnostics_parser
from karcytics_sdk.evaluator import Mode, Severity, evaluate_repo
from karcytics_sdk.evaluator import baseline as baseline_mod
from karcytics_sdk.evaluator.model import Finding
from karcytics_sdk.evaluator.source import parse_directives

from .conftest import PACKAGE


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    setup_diagnostics_parser(parser.add_subparsers())
    return parser.parse_args(["doctor", *argv])


def _ids(repo, path, **kwargs):
    return [(f.rule_id, f.line) for f in evaluate_repo(repo.root, **kwargs).findings if f.path == path]


# ── Suppression comments ─────────────────────────────────────────────


def test_directives_and_their_reasons():
    source = (
        "import os  # noqa: F401 - re-exported\n"
        "import sys  # noqa: F401\n"
        "x: int = 'a'  # type: ignore[assignment]  # legacy fixture\n"
        "y: int = 'b'  # type: ignore[assignment]\n"
        "z = 1  # noqa: E501  # type: ignore\n"
        "# karcytics: allow KS302, KS301 - one flat table\n"
        "# karcytics: allow-file KS300\n"
    )
    found = [(d.line, d.kind, d.reason, d.rules, d.whole_file) for d in parse_directives(source)]
    assert found == [
        (1, "noqa", "re-exported", (), False),
        (2, "noqa", "", (), False),
        (3, "type: ignore", "legacy fixture", (), False),
        (4, "type: ignore", "", (), False),
        (5, "noqa", "", (), False),
        (5, "type: ignore", "", (), False),
        (6, "allow", "one flat table", ("KS302", "KS301"), False),
        (7, "allow", "", ("KS300",), True),
    ]


def test_unexplained_suppressions_are_reported(repo):
    path = repo.ui("import os  # noqa: F401\nimport sys  # noqa: F401 - re-exported\n")
    assert _ids(repo, path) == [("KS310", 1)]


def test_an_explained_allow_suppresses_on_its_line_or_the_next(repo):
    path = repo.ui(
        "print('a')  # karcytics: allow KS252 - CLI entry point\n"
        "# karcytics: allow KS252 - CLI entry point\n"
        "\n"
        "print('b')\n"
        "print('c')\n"
    )
    assert _ids(repo, path) == [("KS252", 5)]


def test_an_allow_without_a_reason_suppresses_nothing(repo):
    path = repo.ui("print('a')  # karcytics: allow KS252\n")
    assert _ids(repo, path) == [("KS310", 1), ("KS252", 1)]


def test_allow_file_covers_the_whole_file_for_its_rules_only(repo):
    path = repo.ui(
        "# karcytics: allow-file KS252 - a CLI script\nprint('a')\nprint('b')\nimport logging\nlogging.getLogger()\n"
    )
    assert _ids(repo, path) == [("KS250", 5)]


# ── Baseline arithmetic ──────────────────────────────────────────────


def _finding(rule, path="a.py", severity=Severity.WARNING, **kwargs):
    return Finding(rule, severity, path, "m", "r", **kwargs)


def test_measure_counts_findings_and_keeps_the_largest_measurement():
    findings = [
        _finding("KS240"),
        _finding("KS240"),
        _finding("KS301", subject="Panel", metric=41),
        _finding("KS301", subject="Panel", metric=39),
        _finding("KS241", severity=Severity.INFO),
    ]
    assert baseline_mod.measure(findings) == {"a.py": {"KS240": 2, "KS301:Panel": 41}}


def test_compare_reports_growth_new_entries_and_improvements():
    recorded = {"a.py": {"KS240": 2, "KS301:Panel": 41}, "gone.py": {"KS240": 1}}
    current = {"a.py": {"KS240": 3, "KS301:Panel": 30}, "new.py": {"KS252": 1}}
    result = baseline_mod.compare(recorded, current)
    assert [(c.path, c.key, c.current, c.allowed) for c in result.exceeded] == [
        ("a.py", "KS240", 3, 2),
        ("new.py", "KS252", 1, 0),
    ]
    assert [(c.path, c.key) for c in result.improved] == [("a.py", "KS301:Panel"), ("gone.py", "KS240")]
    assert baseline_mod.compare(recorded, current, scope={"gone.py"}).exceeded == []


def test_tighten_only_lowers_and_keeps_paths_outside_the_scope():
    recorded = {"a.py": {"KS240": 2, "KS250": 1}, "b.py": {"KS240": 5}}
    current = {"a.py": {"KS240": 4, "KS252": 1}, "b.py": {"KS240": 1}}
    assert baseline_mod.tighten(recorded, current) == {"a.py": {"KS240": 2}, "b.py": {"KS240": 1}}
    assert baseline_mod.tighten(recorded, current, scope={"a.py"}) == {"a.py": {"KS240": 2}, "b.py": {"KS240": 5}}


# ── Runs scoped to files ─────────────────────────────────────────────


def test_files_run_checks_only_those_files_and_skips_repo_level_rules(repo):
    repo.remove("LICENSE")
    edited = repo.write(f"{PACKAGE}/ui/edited.py", "print('x')\n")
    repo.write(f"{PACKAGE}/ui/other.py", "print('y')\n")
    report = evaluate_repo(repo.root, files=[edited])
    assert [(f.rule_id, f.path) for f in report.findings] == [("KS252", f"{PACKAGE}/ui/edited.py")]
    assert report.files_scanned == 1


# ── CLI baseline workflow ────────────────────────────────────────────


def test_baseline_workflow(repo, capsys):
    root = str(repo.root)
    path = repo.ui("print('a')\n")
    assert doctor_plugin(_parse(root, "--baseline")) is False  # no baseline yet
    assert doctor_plugin(_parse(root, "--update-baseline")) is True
    recorded = json.loads((repo.root / baseline_mod.BASELINE_FILE).read_text())
    assert recorded["files"] == {path: {"KS252": 1}}

    assert doctor_plugin(_parse(root, "--baseline")) is True
    repo.ui("print('a')\nprint('b')\n")
    capsys.readouterr()
    assert doctor_plugin(_parse(root, "--baseline")) is False
    out = capsys.readouterr().out
    assert "KS252: 2 (was 1)" in out

    # --update-baseline never accepts growth; --reset-baseline does.
    assert doctor_plugin(_parse(root, "--update-baseline")) is True
    assert doctor_plugin(_parse(root, "--baseline")) is False
    assert doctor_plugin(_parse(root, "--reset-baseline")) is True
    assert doctor_plugin(_parse(root, "--baseline")) is True

    # A clean-up is locked in by --update-baseline.
    repo.ui("x = 1\n")
    assert doctor_plugin(_parse(root, "--update-baseline")) is True
    assert json.loads((repo.root / baseline_mod.BASELINE_FILE).read_text())["files"] == {}


def test_writing_a_baseline_refuses_a_partial_rule_set(repo, capsys):
    assert doctor_plugin(_parse(str(repo.root), "--update-baseline", "--select", "KS2")) is False
    assert "--select" in capsys.readouterr().err


def test_baseline_errors_always_fail(repo):
    repo.ui("import karcytics.core\n")
    assert doctor_plugin(_parse(str(repo.root), "--update-baseline")) is True
    assert (
        evaluate_repo(repo.root, baseline=baseline_mod.load(repo.root / baseline_mod.BASELINE_FILE)).passed() is False
    )


# ── Claude Code hook ─────────────────────────────────────────────────


def _hook(monkeypatch, repo, file_path: str):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": file_path}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return doctor_plugin(_parse(str(repo.root), "--claude-hook"))


def test_hook_passes_a_clean_edit_and_ignores_non_python_files(repo, monkeypatch):
    clean = repo.write(f"{PACKAGE}/ui/clean.py", "x = 1\n")
    assert _hook(monkeypatch, repo, str(clean)) is True
    assert _hook(monkeypatch, repo, str(repo.root / "README.md")) is True


def test_hook_exits_2_with_the_findings_on_stderr(repo, monkeypatch, capsys):
    edited = repo.write(f"{PACKAGE}/ui/edited.py", "print('x')\n")
    with pytest.raises(SystemExit) as exit_info:
        _hook(monkeypatch, repo, str(edited))
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "KS252" in err and "edited.py" in err


def test_hook_uses_the_repo_baseline_when_there_is_one(repo, monkeypatch):
    edited = repo.write(f"{PACKAGE}/ui/edited.py", "print('x')\n")
    assert doctor_plugin(_parse(str(repo.root), "--update-baseline")) is True
    assert _hook(monkeypatch, repo, str(edited)) is True
    repo.write(f"{PACKAGE}/ui/edited.py", "print('x')\nprint('y')\n")
    with pytest.raises(SystemExit):
        _hook(monkeypatch, repo, str(edited))


def test_design_mode_selects_only_design_rules(repo):
    path = repo.ui("print('a')  # noqa: T201\n")
    assert _ids(repo, path, modes=[Mode.DESIGN]) == [("KS310", 1)]
