import pytest

from karcytics_sdk.evaluator import Mode, Severity, evaluate_repo

from .conftest import PACKAGE, PYPROJECT


def _ids(repo) -> list[str]:
    return [f.rule_id for f in evaluate_repo(repo.root, modes=[Mode.ORG]).findings]


def test_conformant_repo_has_no_findings(repo):
    assert evaluate_repo(repo.root).findings == []


def test_missing_pyproject(repo):
    repo.remove("pyproject.toml")
    assert "KS100" in _ids(repo)


def test_unparseable_pyproject(repo):
    repo.write("pyproject.toml", "[project\n")
    assert "KS100" in _ids(repo)


def test_non_plugin_repo_reports_missing_manifest_only_once(repo):
    repo.write("pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
    ids = _ids(repo)
    assert ids.count("KS101") == 1
    # Manifest-dependent rules stay quiet rather than piling on.
    assert not {"KS102", "KS110", "KS120", "KS121", "KS151"} & set(ids)


def test_missing_manifest_key_points_at_the_manifest_table(repo):
    repo.write("pyproject.toml", PYPROJECT.replace('requires = ["logger"]\n', ""))
    findings = [f for f in evaluate_repo(repo.root).findings if f.rule_id == "KS102"]
    assert len(findings) == 1
    assert "`requires`" in findings[0].message
    assert findings[0].line == PYPROJECT.splitlines().index("[tool.karcytics.plugin]") + 1


@pytest.mark.parametrize(
    ("old", "new", "rule_id"),
    [
        ('process_model = "isolated"', 'process_model = "threaded"', "KS103"),
        ('version = "1.0.0"\n', "", "KS104"),
        ('dependencies = ["karcytics-sdk>=2.4.0"]', "dependencies = []", "KS151"),
        ('author_public_key = "abc123"', "", "KS121"),
        (":initialize", ":missing_function", "KS113"),
        ("karcytics_plugins.demo:initialize", "karcytics_plugins.nowhere:initialize", "KS113"),
        (":initialize", "", "KS113"),
        ("[tool.ruff]", "[tool.other]", "KS160"),
        ("[tool.mypy]", "[tool.other2]", "KS161"),
    ],
)
def test_pyproject_rules(repo, old, new, rule_id):
    assert old in PYPROJECT
    repo.write("pyproject.toml", PYPROJECT.replace(old, new))
    assert rule_id in _ids(repo)


def test_uv_source_is_info_only(repo):
    repo.write("pyproject.toml", PYPROJECT.replace("[tool.uv.sources]", "[tool.uv.other]"))
    findings = evaluate_repo(repo.root).findings
    assert [(f.rule_id, f.severity) for f in findings] == [("KS152", Severity.INFO)]


def test_package_name_must_match_manifest_id(repo):
    (repo.root / PACKAGE).rename(repo.root / "src/karcytics_plugins/other_name")
    ids = _ids(repo)
    assert "KS110" in ids


def test_missing_package(repo):
    repo.write("pyproject.toml", PYPROJECT.replace('id = "demo"', 'id = "elsewhere"'))
    (repo.root / PACKAGE / "extra").mkdir()
    (repo.root / "src/karcytics_plugins/second").mkdir()
    assert "KS110" in _ids(repo)


@pytest.mark.parametrize(("folder", "rule_id"), [("analysis", "KS111"), ("ui", "KS112")])
def test_missing_layer_directory(repo, folder, rule_id):
    for path in sorted((repo.root / PACKAGE / folder).iterdir()):
        path.unlink()
    (repo.root / PACKAGE / folder).rmdir()
    assert rule_id in _ids(repo)


@pytest.mark.parametrize(
    ("relative", "rule_id"),
    [
        ("security.json", "KS120"),
        ("signature.bin", "KS120"),
        ("trust_chain.json", "KS120"),
        (".github/workflows/release.yml", "KS130"),
        (".pre-commit-config.yaml", "KS132"),
        ("tests/unit/test_contract.py", "KS142"),
        ("uv.lock", "KS150"),
        ("README.md", "KS170"),
        ("LICENSE", "KS171"),
        ("CLAUDE.md", "KS172"),
        ("mkdocs.yml", "KS173"),
    ],
)
def test_missing_file_rules(repo, relative, rule_id):
    repo.remove(relative)
    assert rule_id in _ids(repo)


def test_workflow_without_tests(repo):
    repo.write(".github/workflows/release.yml", "jobs: {}\n")
    ids = _ids(repo)
    assert "KS131" in ids
    assert "KS130" not in ids


def test_missing_pre_commit_hook_names_the_hook(repo):
    repo.write(".pre-commit-config.yaml", "repos:\n  - hooks:\n      - id: ruff\n      - id: pytest\n")
    messages = [f.message for f in evaluate_repo(repo.root).findings if f.rule_id == "KS133"]
    assert messages == [
        "no mypy hook",
        "no plugin signing hook",
        "no security ledger verification hook",
        "no license compliance hook",
    ]


@pytest.mark.parametrize("entry", ["uv run karcytics-sdk check-licenses", "python scripts/check_license_compliance.py"])
def test_either_license_check_form_counts(repo, entry):
    config = (repo.root / ".pre-commit-config.yaml").read_text().replace("      - id: license-compliance\n", "")
    repo.write(".pre-commit-config.yaml", config + f"      - id: licenses\n        entry: {entry}\n")
    assert "KS133" not in _ids(repo)


def test_missing_tests_directory(repo):
    repo.remove("tests/unit/test_contract.py")
    (repo.root / "tests/unit").rmdir()
    assert "KS141" in _ids(repo)
    (repo.root / "tests").rmdir()
    assert "KS140" in _ids(repo)
