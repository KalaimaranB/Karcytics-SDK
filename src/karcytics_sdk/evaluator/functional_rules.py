"""Functional checks (KS2xx): is the SDK actually used where it should be?

One AST pass per source file. Names are resolved through the file's own
imports, so `from PySide6.QtWidgets import QPushButton as Btn` and
`QtWidgets.QPushButton(...)` are both recognised, while an unrelated local
class that happens to be called `QPushButton` is not.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

from . import catalog
from .model import Finding, Severity
from .registry import make_finding
from .repo import RepoLayout
from .source import SourceFile, collect_imports

# 6- or 8-digit hex colours only: 3-digit forms collide with "#123"-style
# issue references in ordinary text.
_HEX_COLOUR = re.compile(r"(?<![\w&#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})(?![0-9a-zA-Z_])")

_SDK_BASES = ("PluginBase", "AnalysisBase")
_QT_THREAD_BASES = ("QThread", "QRunnable")
_JSON_FILE_CALLS = ("json.load", "json.dump")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Identities of the Constant nodes that are docstrings (prose, not code)."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            found.add(id(first.value))
    return found


def _interpolates_colors(node: ast.expr) -> bool:
    """An f-string that formats a `Colors.<token>` into itself."""
    if not isinstance(node, ast.JoinedStr):
        return False
    return any(
        isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "Colors"
        for value in node.values
        if isinstance(value, ast.FormattedValue)
        for n in ast.walk(value.value)
    )


def combo_receivers(tree: ast.AST) -> set[str]:
    """`self._x` / `x` names a file assigns a combo box to (`self._x = BioComboBox()`)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if "Combo" in name:
                found.update(ast.unparse(target) for target in node.targets)
    return found


def _combo_rebuilds(function: ast.FunctionDef | ast.AsyncFunctionDef, combos: set[str]) -> Iterator[ast.Call]:
    """`x.clear()` calls in `function` followed by an `x.addItem()` loop on a combo box.

    Only receivers known to be combos count (named so, or assigned a combo in
    the file): QListWidget has the same two methods and repopulate_combo() is
    for combo boxes.
    """
    calls = [(n, n.func) for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    cleared = {ast.unparse(func.value): call for call, func in calls if func.attr == "clear" and not call.args}
    looped = {
        ast.unparse(n.func.value)
        for loop in ast.walk(function)
        if isinstance(loop, ast.For)
        for n in ast.walk(loop)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "addItem"
    }
    for receiver, call in cleared.items():
        if receiver in looped and ("combo" in receiver.lower() or receiver in combos):
            yield call


class _FileChecker(ast.NodeVisitor):
    """Walks one file and records its findings."""

    def __init__(self, path: str, zone: str, tree: ast.AST) -> None:
        self.path = path
        self.zone = zone
        self.findings: list[Finding] = []
        self.sdk_bases_seen: set[str] = set()
        self.panel_classes: set[str] = set()
        self._names = collect_imports(tree)
        self._docstrings = _docstring_nodes(tree)
        self._colour_lines: set[int] = set()
        self._combo_calls: set[int] = set()
        self._combos = combo_receivers(tree)

    # ── Helpers ───────────────────────────────────────────────────────

    def _resolve(self, node: ast.expr) -> str | None:
        """Dotted origin of a Name/Attribute chain, or None if it is not an import."""
        if isinstance(node, ast.Name):
            return self._names.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    @staticmethod
    def _is_qt(dotted: str) -> bool:
        return dotted.split(".")[0] in catalog.QT_BINDINGS

    def _add(self, rule_id: str, node: ast.AST, message: str, **overrides) -> None:
        self.findings.append(
            make_finding(
                rule_id,
                self.path,
                message,
                line=getattr(node, "lineno", None),
                col=getattr(node, "col_offset", 0) + 1,
                **overrides,
            )
        )

    # ── Imports ───────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import(node, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and not node.level:
            self._check_import(node, node.module)
            if self.zone == "analysis" and node.module.startswith(catalog.SDK_PACKAGE):
                for alias in node.names:
                    if alias.name in catalog.QT_BACKED_SDK_EXPORTS:
                        self._add("KS205", node, f"imports `{alias.name}`, a Qt object, inside analysis/")

    def _check_import(self, node: ast.AST, module: str) -> None:
        top = module.split(".")[0]
        if top == catalog.HUB_PACKAGE:
            self._add("KS201", node, f"imports `{module}` from the Hub application")
        elif top in catalog.QT_BINDINGS:
            if top != catalog.SUPPORTED_QT_BINDING:
                self._add("KS203", node, f"imports `{module}`; the supported binding is PySide6")
            if self.zone == "analysis":
                self._add("KS202", node, f"imports `{module}` inside analysis/")

    # ── Classes ───────────────────────────────────────────────────────

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            dotted = self._resolve(base)
            if dotted:
                self._check_base(node, dotted)
        self.generic_visit(node)

    def _check_base(self, node: ast.ClassDef, dotted: str) -> None:
        name = dotted.rsplit(".", 1)[-1]
        if dotted.startswith(catalog.SDK_PACKAGE) and name in _SDK_BASES:
            self.sdk_bases_seen.add(name)
            if name == "PluginBase":
                self.panel_classes.add(node.name)
        if not self._is_qt(dotted):
            return
        if name in catalog.WIDGET_EQUIVALENTS:
            remedy = f"Subclass the SDK component instead: {catalog.WIDGET_EQUIVALENTS[name]}."
            self._add("KS211", node, f"`{node.name}` subclasses `{name}` directly", remedy=remedy)
        elif name in _QT_THREAD_BASES:
            self._add("KS230", node, f"`{node.name}` subclasses `{name}` directly")

    # ── Calls ─────────────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        dotted = self._resolve(node.func)
        if dotted is None:
            self._check_unresolved_call(node)
        elif self._is_qt(dotted):
            self._check_qt_call(node, dotted)
        else:
            self._check_stdlib_call(node, dotted)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self.zone != "analysis":
            # A nested function is walked again on its own; report each call once.
            for call in (c for c in _combo_rebuilds(node, self._combos) if id(c) not in self._combo_calls):
                self._combo_calls.add(id(call))
                self._add(
                    "KS243", call, f"`{ast.unparse(call.func)}()` then an addItem() loop rebuilds a combo by hand"
                )
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _check_unresolved_call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "print":
            self._add("KS252", node, "`print()` used for output")
        elif isinstance(func, ast.Attribute) and func.attr == "setStyleSheet":
            if node.args and _interpolates_colors(node.args[0]):
                self._add("KS242", node, "stylesheet built by interpolating `Colors` into an f-string")
            else:
                self._add("KS241", node, "`setStyleSheet()` called directly")

    def _check_qt_call(self, node: ast.Call, dotted: str) -> None:
        owner, _, name = dotted.rpartition(".")
        owner = owner.rpartition(".")[2]
        if name in catalog.SIGNAL_DECLARATORS:
            if any(isinstance(arg, ast.Name) and arg.id == "dict" for arg in node.args):
                self._add("KS204", node, f"`{name}` declared with a `dict` argument")
        elif name in catalog.WIDGET_EQUIVALENTS:
            remedy = f"Use {catalog.WIDGET_EQUIVALENTS[name]}."
            self._add("KS210", node, f"`{name}` instantiated directly", remedy=remedy)
        elif name == "QThread":
            self._add("KS230", node, "`QThread` instantiated directly")
        elif owner == "QThreadPool" and name == "globalInstance":
            self._add("KS230", node, "`QThreadPool.globalInstance()` used directly")
        elif "" in catalog.DIALOG_EQUIVALENTS.get(name, {}):
            remedy = f"Use {catalog.DIALOG_EQUIVALENTS[name]['']}."
            self._add("KS220", node, f"`{name}` built by hand", remedy=remedy)
        elif name in catalog.DIALOG_EQUIVALENTS.get(owner, {}):
            self._add_dialog_call(node, owner, name)

    def _add_dialog_call(self, node: ast.Call, owner: str, method: str) -> None:
        severity = Severity.INFO if method in catalog.PARTIAL_DIALOG_METHODS else None
        remedy = f"Use {catalog.DIALOG_EQUIVALENTS[owner][method]}."
        self._add("KS220", node, f"`{owner}.{method}()` called directly", remedy=remedy, severity=severity)

    def _check_stdlib_call(self, node: ast.Call, dotted: str) -> None:
        if dotted in catalog.PY_THREAD_CALLABLES:
            # analysis/ cannot use Qt, so a plain thread pool there can be a
            # legitimate choice - worth a look, not a warning.
            severity = Severity.INFO if self.zone == "analysis" else None
            self._add("KS231", node, f"`{dotted}` used directly", severity=severity)
        elif dotted == "logging.getLogger":
            self._add("KS250", node, "`logging.getLogger()` used instead of the SDK logger")
        elif dotted in _JSON_FILE_CALLS:
            self._add("KS251", node, f"`{dotted}()` used for file I/O")

    # ── Literals ──────────────────────────────────────────────────────

    def visit_Constant(self, node: ast.Constant) -> None:
        if self.zone == "analysis" or not isinstance(node.value, str) or id(node) in self._docstrings:
            return
        for match in _HEX_COLOUR.finditer(node.value):
            line = node.lineno + node.value.count("\n", 0, match.start())
            if line not in self._colour_lines:
                self._colour_lines.add(line)
                self.findings.append(
                    make_finding("KS240", self.path, f"hard-coded colour `{match.group()}`", line=line)
                )


def check_source(source: SourceFile) -> _FileChecker:
    checker = _FileChecker(source.rel, source.zone, source.tree)
    checker.visit(source.tree)
    return checker


def run_functional_rules(
    layout: RepoLayout, sources: list[SourceFile], *, whole_repo: bool = True
) -> Iterator[Finding]:
    """Yield every functional finding for `sources`.

    Repo-level expectations (KS260/KS261) need every file, so they are only
    checked when `whole_repo` is set.
    """
    sdk_bases_seen: set[str] = set()
    for source in sources:
        result = check_source(source)
        sdk_bases_seen |= result.sdk_bases_seen
        yield from result.findings

    # Repo-level expectations only make sense for an actual plugin package.
    if whole_repo and layout.package_dir and layout.source_files:
        package = layout.rel(layout.package_dir)
        if "PluginBase" not in sdk_bases_seen:
            yield make_finding("KS260", package, "no class subclasses `karcytics_sdk` PluginBase")
        if "AnalysisBase" not in sdk_bases_seen:
            yield make_finding("KS261", package, "no class subclasses `karcytics_sdk` AnalysisBase")


def panel_classes(sources: list[SourceFile]) -> set[str]:
    """Names of the classes that subclass the SDK's PluginBase."""
    found: set[str] = set()
    for source in sources:
        found |= check_source(source).panel_classes
    return found
