"""Organizational checks (KS1xx): repo structure, manifest, signing, CI, tooling.

Each check is a small generator over a `RepoLayout`. They only look at which
files exist and what the config files say - no Python source is analysed here.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator

from .model import Finding
from .registry import make_finding
from .repo import PLUGIN_NAMESPACE, RepoLayout

PYPROJECT = "pyproject.toml"
PRE_COMMIT = ".pre-commit-config.yaml"
WORKFLOWS = ".github/workflows"

_MANIFEST_KEYS = ("id", "entry_point", "process_model", "min_core_version", "requires", "authors")
_PROCESS_MODELS = ("isolated", "in_process")
_SIGNING_FILES = ("security.json", "signature.bin", "trust_chain.json")

# Hook name -> substrings, any one of which shows the hook is configured.
_PRE_COMMIT_HOOKS = {
    "ruff": ("ruff",),
    "mypy": ("mypy",),
    "pytest": ("pytest",),
    "plugin signing": ("karcytics-sdk sign", "karcytics-sign"),
    "security ledger verification": ("verify_security_ledger", "verify-security-ledger"),
}

_MANIFEST_HEADER = r"^\s*\[tool\.karcytics\.plugin\]"

Check = Callable[[RepoLayout], Iterator[Finding]]


def _check_pyproject(layout: RepoLayout) -> Iterator[Finding]:
    if layout.pyproject_error:
        yield make_finding("KS100", PYPROJECT, f"pyproject.toml could not be parsed: {layout.pyproject_error}")
    elif layout.pyproject is None:
        yield make_finding("KS100", PYPROJECT, "pyproject.toml not found")
    elif layout.manifest is None:
        yield make_finding("KS101", PYPROJECT, "no [tool.karcytics.plugin] table - this repo is not a plugin yet")


def _check_manifest(layout: RepoLayout) -> Iterator[Finding]:
    manifest = layout.manifest
    if manifest is None:
        return
    header = layout.pyproject_line(_MANIFEST_HEADER)
    for key in _MANIFEST_KEYS:
        if not manifest.get(key):
            yield make_finding("KS102", PYPROJECT, f"[tool.karcytics.plugin] has no `{key}`", line=header)

    model = manifest.get("process_model")
    if model and model not in _PROCESS_MODELS:
        line = layout.pyproject_line(r"^\s*process_model\s*=")
        yield make_finding("KS103", PYPROJECT, f"process_model is `{model}`", line=line)

    signing = manifest.get("signing")
    if not (isinstance(signing, dict) and signing.get("author_public_key")):
        yield make_finding("KS121", PYPROJECT, "no author_public_key in the manifest", line=header)


def _check_project_table(layout: RepoLayout) -> Iterator[Finding]:
    if layout.manifest is None:
        return
    project = (layout.pyproject or {}).get("project") or {}
    line = layout.pyproject_line(r"^\s*\[project\]")
    for key in ("name", "version"):
        if not project.get(key):
            yield make_finding("KS104", PYPROJECT, f"[project] has no `{key}`", line=line)

    dependencies = [str(dep).lower().replace("_", "-") for dep in project.get("dependencies") or []]
    if not any(dep.startswith("karcytics-sdk") for dep in dependencies):
        yield make_finding("KS151", PYPROJECT, "karcytics-sdk is not in [project] dependencies", line=line)

    sources = (((layout.pyproject or {}).get("tool") or {}).get("uv") or {}).get("sources") or {}
    if "karcytics-sdk" not in sources:
        yield make_finding("KS152", PYPROJECT, "karcytics-sdk has no [tool.uv.sources] entry")


def _check_package_layout(layout: RepoLayout) -> Iterator[Finding]:
    if layout.manifest is None:
        return
    expected = f"src/{PLUGIN_NAMESPACE}/{layout.plugin_id or '<id>'}"
    package = layout.package_dir
    if package is None:
        yield make_finding("KS110", expected, f"{expected}/ not found")
        return
    if not layout.package_matches_id:
        yield make_finding(
            "KS110",
            layout.rel(package),
            f"package is `{package.name}` but the manifest id is `{layout.plugin_id}`",
        )
    for rule_id, name in (("KS111", "analysis"), ("KS112", "ui")):
        if not (package / name).is_dir():
            yield make_finding(rule_id, f"{layout.rel(package)}/{name}", f"{name}/ not found in the plugin package")


def _module_defines(source: str, name: str) -> bool:
    """Whether a module binds `name` at top level (def, class, assignment, or import)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name == name:
            return True
        if isinstance(node, ast.Import | ast.ImportFrom) and any((a.asname or a.name) == name for a in node.names):
            return True
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return True
    return False


def _check_entry_point(layout: RepoLayout) -> Iterator[Finding]:
    entry = (layout.manifest or {}).get("entry_point")
    if not isinstance(entry, str) or not entry:
        return
    line = layout.pyproject_line(r"^\s*entry_point\s*=")
    module, _, function = entry.partition(":")
    if not module or not function:
        yield make_finding("KS113", PYPROJECT, f'entry_point `{entry}` is not "module:function"', line=line)
        return
    base = "src/" + module.replace(".", "/")
    source = next((text for text in (layout.read(f"{base}/__init__.py"), layout.read(f"{base}.py")) if text), None)
    if source is None:
        yield make_finding("KS113", PYPROJECT, f"entry_point module `{module}` not found under src/", line=line)
    elif not _module_defines(source, function):
        yield make_finding("KS113", PYPROJECT, f"`{module}` does not define `{function}`", line=line)


def _check_signing_files(layout: RepoLayout) -> Iterator[Finding]:
    if layout.manifest is None:
        return
    for name in _SIGNING_FILES:
        if not (layout.root / name).is_file():
            yield make_finding("KS120", name, f"{name} not found")


def _check_workflows(layout: RepoLayout) -> Iterator[Finding]:
    directory = layout.root / WORKFLOWS
    workflows = sorted(p for p in directory.glob("*.y*ml") if p.is_file()) if directory.is_dir() else []
    if not any(p.stem == "release" for p in workflows):
        yield make_finding("KS130", f"{WORKFLOWS}/release.yml", "release workflow not found")
    if not any("pytest" in (layout.read(layout.rel(p)) or "") for p in workflows):
        yield make_finding("KS131", WORKFLOWS, "no GitHub Actions workflow runs pytest")


def _check_pre_commit(layout: RepoLayout) -> Iterator[Finding]:
    config = layout.read(PRE_COMMIT)
    if config is None:
        yield make_finding("KS132", PRE_COMMIT, f"{PRE_COMMIT} not found")
        return
    for hook, markers in _PRE_COMMIT_HOOKS.items():
        if not any(marker in config for marker in markers):
            yield make_finding("KS133", PRE_COMMIT, f"no {hook} hook", remedy=f"Add a {hook} hook to {PRE_COMMIT}.")


def _check_tests(layout: RepoLayout) -> Iterator[Finding]:
    tests = layout.root / "tests"
    if not tests.is_dir():
        yield make_finding("KS140", "tests", "tests/ not found")
        return
    if not (tests / "unit").is_dir():
        yield make_finding("KS141", "tests/unit", "tests/unit/ not found")
    if layout.manifest is None:
        return
    has_contract = any("ContractTestBase" in (layout.read(layout.rel(p)) or "") for p in tests.rglob("*.py"))
    if not has_contract:
        yield make_finding("KS142", "tests", "no test subclasses ContractTestBase")


def _check_tooling(layout: RepoLayout) -> Iterator[Finding]:
    root = layout.root
    tool = (layout.pyproject or {}).get("tool") or {}
    if not (root / "uv.lock").is_file():
        yield make_finding("KS150", "uv.lock", "uv.lock not found")
    if "ruff" not in tool and not any((root / name).is_file() for name in ("ruff.toml", ".ruff.toml")):
        yield make_finding("KS160", "ruff.toml", "no ruff configuration found")
    if "mypy" not in tool and not any((root / name).is_file() for name in ("mypy.ini", ".mypy.ini")):
        yield make_finding("KS161", PYPROJECT, "no mypy configuration found")


def _check_documents(layout: RepoLayout) -> Iterator[Finding]:
    root = layout.root
    if not (root / "README.md").is_file():
        yield make_finding("KS170", "README.md", "README.md not found")
    if not any((root / name).is_file() for name in ("LICENSE", "LICENSE.md", "LICENSE.txt")):
        yield make_finding("KS171", "LICENSE", "LICENSE not found")
    if not (root / "CLAUDE.md").is_file():
        yield make_finding("KS172", "CLAUDE.md", "CLAUDE.md not found")
    if not (root / "mkdocs.yml").is_file():
        yield make_finding("KS173", "mkdocs.yml", "mkdocs.yml not found")


ORG_CHECKS: list[Check] = [
    _check_pyproject,
    _check_manifest,
    _check_project_table,
    _check_package_layout,
    _check_entry_point,
    _check_signing_files,
    _check_workflows,
    _check_pre_commit,
    _check_tests,
    _check_tooling,
    _check_documents,
]


def run_org_rules(layout: RepoLayout) -> Iterator[Finding]:
    """Yield every organizational finding for the repo."""
    for check in ORG_CHECKS:
        yield from check(layout)
