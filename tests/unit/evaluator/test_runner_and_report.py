import argparse
import json
from pathlib import Path

from karcytics_sdk.cli.commands.diagnostics import doctor_plugin, setup_diagnostics_parser
from karcytics_sdk.evaluator import RULES, Mode, Severity, evaluate_repo, render_json, render_text

RAW = (
    "from PySide6.QtWidgets import QPushButton\nfrom karcytics.core import x\nb = QPushButton()\nb.setStyleSheet('')\n"
)


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    setup_diagnostics_parser(parser.add_subparsers())
    return parser.parse_args(["doctor", *argv])


def test_rule_ids_are_namespaced_by_mode():
    for rule_id, rule in RULES.items():
        assert rule_id.startswith("KS1" if rule.mode is Mode.ORG else "KS2")
        assert rule.title and rule.remedy


def test_every_rule_is_documented():
    docs = (Path(__file__).parents[3] / "docs" / "Doctor.md").read_text(encoding="utf-8")
    assert [rule_id for rule_id in RULES if f"`{rule_id}`" not in docs] == []


def test_modes_select_and_ignore(repo):
    repo.ui(RAW)
    repo.remove("LICENSE")

    def ids(**kwargs):
        return [f.rule_id for f in evaluate_repo(repo.root, **kwargs).findings]

    assert ids() == ["KS171", "KS201", "KS210", "KS241"]
    assert ids(modes=[Mode.ORG]) == ["KS171"]
    assert ids(modes=[Mode.FUNCTIONAL]) == ["KS201", "KS210", "KS241"]
    assert ids(select=["ks2"]) == ["KS201", "KS210", "KS241"]
    assert ids(ignore=["KS21", " KS171 "]) == ["KS201", "KS241"]
    # A blank entry (what "".split(",") produces) must not filter everything out.
    assert ids(select=[""], ignore=[""]) == ["KS171", "KS201", "KS210", "KS241"]


def test_passed_ignores_info_and_respects_strict(repo):
    path = repo.ui("w.setStyleSheet('')\n")
    assert evaluate_repo(repo.root).passed(strict=True)
    repo.write(path, "print('x')\n")
    report = evaluate_repo(repo.root)
    assert report.passed() and not report.passed(strict=True)
    repo.write(path, "import karcytics\n")
    assert not evaluate_repo(repo.root).passed()


def test_text_report_hides_info_by_default(repo):
    path = repo.ui(RAW)
    report = evaluate_repo(repo.root)

    text = render_text(report, color=False)
    assert "\033[" not in text
    assert path in text
    assert "3:5  warning  KS210  `QPushButton` instantiated directly" in text
    assert "fix: Use BioButton" in text
    assert "KS241" not in text
    assert "1 error(s), 1 warning(s), 1 info hidden (--show-info)" in text
    assert "Organizational" in text and "No violations." in text

    with_info = render_text(report, show_info=True, color=False)
    assert "KS241" in with_info
    assert "1 error(s), 1 warning(s), 1 info" in with_info


def test_text_report_colours_by_severity(repo):
    repo.ui(RAW)
    text = render_text(evaluate_repo(repo.root), color=True)
    assert "\033[1;31merror" in text
    assert "\033[33mwarning" in text


def test_json_report_contains_every_finding(repo):
    path = repo.ui(RAW)
    payload = json.loads(render_json(evaluate_repo(repo.root)))
    assert payload["plugin_id"] == "demo"
    assert payload["summary"] == {"error": 1, "warning": 1, "info": 1}
    assert payload["findings"][0] == {
        "rule_id": "KS201",
        "severity": Severity.ERROR.value,
        "path": path,
        "message": "imports `karcytics.core` from the Hub application",
        "remedy": RULES["KS201"].remedy,
        "line": 2,
        "col": 1,
    }


def test_cli_exit_status_and_output(repo, capsys):
    repo.ui("print('x')\n")
    assert doctor_plugin(_parse(str(repo.root))) is True
    assert "KS252" in capsys.readouterr().out
    assert doctor_plugin(_parse(str(repo.root), "--strict")) is False
    assert doctor_plugin(_parse(str(repo.root), "--strict", "--ignore", "KS252")) is True
    capsys.readouterr()

    assert doctor_plugin(_parse(str(repo.root), "--mode", "org", "--format", "json")) is True
    assert json.loads(capsys.readouterr().out)["modes"] == ["org"]


def test_cli_rejects_a_missing_directory(tmp_path, capsys):
    assert doctor_plugin(_parse(str(tmp_path / "nope"))) is False
    assert "not a directory" in capsys.readouterr().err


def test_cli_accepts_legacy_namespace_with_only_plugin_dir(repo, capsys):
    assert doctor_plugin(argparse.Namespace(plugin_dir=str(repo.root))) is True
