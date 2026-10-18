"""Locates the pieces of a repo the evaluator's rules look at.

Nothing here imports or executes the target repo - it only reads files.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

PLUGIN_NAMESPACE = "karcytics_plugins"
TESTS_DIR = "tests"

# Never descended into when collecting source files.
_SKIP_DIRS = {
    "tests",
    "test",
    "build",
    "dist",
    "site",
    "htmlcov",
    "node_modules",
    "venv",
    "__pycache__",
}


@dataclass
class RepoLayout:
    """A read-only view of one repo on disk."""

    root: Path
    pyproject: dict[str, Any] | None = None
    pyproject_error: str | None = None
    _pyproject_text: str = field(default="", repr=False)

    @classmethod
    def discover(cls, root: Path | str) -> RepoLayout:
        root = Path(root).resolve()
        layout = cls(root=root)
        path = root / "pyproject.toml"
        if not path.is_file():
            return layout
        try:
            layout._pyproject_text = path.read_text(encoding="utf-8")
            layout.pyproject = tomllib.loads(layout._pyproject_text)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            layout.pyproject_error = str(exc)
        return layout

    # ── Manifest ──────────────────────────────────────────────────────

    @property
    def manifest(self) -> dict[str, Any] | None:
        """The `[tool.karcytics.plugin]` table, or None if the repo is not a plugin."""
        table = ((self.pyproject or {}).get("tool") or {}).get("karcytics") or {}
        manifest = table.get("plugin") if isinstance(table, dict) else None
        return manifest if isinstance(manifest, dict) else None

    @property
    def plugin_id(self) -> str | None:
        plugin_id = (self.manifest or {}).get("id")
        return plugin_id if isinstance(plugin_id, str) and plugin_id else None

    def pyproject_line(self, pattern: str) -> int | None:
        """1-based line of the first pyproject.toml line matching `pattern`."""
        regex = re.compile(pattern)
        for number, text in enumerate(self._pyproject_text.splitlines(), start=1):
            if regex.search(text):
                return number
        return None

    # ── Source layout ─────────────────────────────────────────────────

    @cached_property
    def package_dir(self) -> Path | None:
        """The plugin's package: `src/karcytics_plugins/<id>/`.

        Falls back to the only package under the namespace when the manifest
        id is missing or does not match, so functional rules still run.
        """
        namespace = self.root / "src" / PLUGIN_NAMESPACE
        if self.plugin_id and (namespace / self.plugin_id).is_dir():
            return namespace / self.plugin_id
        if not namespace.is_dir():
            return None
        candidates = sorted(p for p in namespace.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
        return candidates[0] if len(candidates) == 1 else None

    @property
    def package_matches_id(self) -> bool:
        return bool(self.package_dir and self.plugin_id and self.package_dir.name == self.plugin_id)

    @cached_property
    def source_files(self) -> list[Path]:
        """Python files the functional rules analyse.

        A plugin's own package when there is one; otherwise every non-test
        `.py` file in the repo, so the evaluator is still useful on a repo
        that is not (yet) laid out as a plugin.
        """
        base = self.package_dir or self.root
        return sorted(p for p in base.rglob("*.py") if not self._is_skipped(p.relative_to(base)))

    @cached_property
    def test_files(self) -> list[Path]:
        """Python files under `tests/`, which only the test rules read."""
        tests = self.root / TESTS_DIR
        if not tests.is_dir():
            return []
        return sorted(
            p
            for p in tests.rglob("*.py")
            if not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(tests).parts)
        )

    def is_test(self, path: Path) -> bool:
        return (self.root / TESTS_DIR) in path.parents

    @staticmethod
    def _is_skipped(relative: Path) -> bool:
        return any(part in _SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1])

    def zone(self, path: Path) -> str:
        """Which architectural layer a file sits in: "analysis", "ui", or "other"."""
        base = self.package_dir or self.root
        parts = path.relative_to(base).parts[:-1]
        for part in parts:
            if part in ("analysis", "ui"):
                return part
        return "other"

    def rel(self, path: Path) -> str:
        """`path` relative to the repo root, POSIX-style, for reporting."""
        return path.relative_to(self.root).as_posix()

    def read(self, relative: str) -> str | None:
        """Text of a repo file, or None when it is missing or unreadable."""
        try:
            return (self.root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
