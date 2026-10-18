"""Builds throwaway plugin repos on disk for the evaluator tests."""

from pathlib import Path

import pytest

PLUGIN_ID = "demo"
PACKAGE = f"src/karcytics_plugins/{PLUGIN_ID}"

PYPROJECT = f"""\
[project]
name = "karcytics-{PLUGIN_ID}"
version = "1.0.0"
dependencies = ["karcytics-sdk>=2.4.0"]

[tool.karcytics.plugin]
id = "{PLUGIN_ID}"
entry_point = "karcytics_plugins.{PLUGIN_ID}:initialize"
process_model = "isolated"
min_core_version = "2.0.0"
requires = ["logger"]
authors = [{{ name = "A", role = "Lead" }}]

[tool.karcytics.plugin.signing]
author_public_key = "abc123"

[tool.uv.sources]
karcytics-sdk = {{ path = "../Karcytics-SDK", editable = true }}

[tool.ruff]
line-length = 120

[tool.mypy]
python_version = "3.12"
"""

PRE_COMMIT = """\
repos:
  - repo: local
    hooks:
      - id: ruff-linter
      - id: mypy
      - id: pytest
      - id: karcytics-sign
      - id: verify-security-ledger
      - id: license-compliance
      - id: karcytics-doctor
"""

FILES = {
    "pyproject.toml": PYPROJECT,
    ".pre-commit-config.yaml": PRE_COMMIT,
    ".github/workflows/release.yml": "jobs:\n  test:\n    steps:\n      - run: uv run pytest\n",
    "security.json": "{}",
    "signature.bin": "sig",
    "trust_chain.json": "{}",
    "uv.lock": "",
    "README.md": "# Demo",
    "LICENSE": "MIT",
    "CLAUDE.md": "# Demo",
    ".claude/settings.json": '{"hooks": {"PostToolUse": [{"hooks": [{"command": "uv run karcytics-sdk doctor --claude-hook"}]}]}}',
    "mkdocs.yml": "site_name: Demo",
    "tests/unit/test_contract.py": (
        "from karcytics_sdk.testing import ContractTestBase\n\n\nclass TestContract(ContractTestBase):\n    pass\n"
    ),
    f"{PACKAGE}/__init__.py": "def initialize(context):\n    return None\n",
    f"{PACKAGE}/ui/__init__.py": "",
    f"{PACKAGE}/ui/panel.py": ("from karcytics_sdk.plugin import PluginBase\n\n\nclass Panel(PluginBase):\n    pass\n"),
    f"{PACKAGE}/analysis/__init__.py": "",
    f"{PACKAGE}/analysis/engine.py": (
        "from karcytics_sdk.plugin import AnalysisBase\n\n\nclass Engine(AnalysisBase):\n    pass\n"
    ),
}


class RepoBuilder:
    """A fully conformant plugin repo that a test then breaks in one specific way."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for relative, content in FILES.items():
            self.write(relative, content)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def remove(self, relative: str) -> None:
        (self.root / relative).unlink()

    def ui(self, source: str) -> str:
        """Write `source` as a ui/ module and return its repo-relative path."""
        return self._module("ui", source)

    def analysis(self, source: str) -> str:
        return self._module("analysis", source)

    def _module(self, zone: str, source: str) -> str:
        relative = f"{PACKAGE}/{zone}/subject.py"
        self.write(relative, source)
        return relative


@pytest.fixture
def repo(tmp_path: Path) -> RepoBuilder:
    return RepoBuilder(tmp_path / "plugin")
