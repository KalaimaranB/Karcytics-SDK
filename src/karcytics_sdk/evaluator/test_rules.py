"""Checks over the repo's own tests (KS27x).

A test that builds the plugin's panel and merely drops it leaves cyclic garbage
whose C++ half is still alive. When Python's collector reaches it, it empties
the wrappers' attributes before Qt tears the widgets down, and Qt then calls
back into those wrappers (pyqtgraph's `LabelItem.sizeHint` raises
AttributeError, for one). pytest-qt reports that against whichever unrelated
test is running at the time, so the suite fails intermittently.

Whether an object really ends up as garbage cannot be decided statically, so
this looks for the recognisable shape: a panel built inside a test or fixture
that neither hands it to anything nor destroys it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

from .model import Finding
from .registry import make_finding
from .source import SourceFile

_PANEL_FACTORY = "create_panel"
# A call to any of these inside the function counts as destroying the panel.
_DESTROYERS = ("deleteLater", "addWidget", "add_widget")
_SHIBOKEN_DELETE = "delete"

_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _own_nodes(function: _Function) -> Iterator[ast.AST]:
    """Nodes of `function`, without descending into functions nested inside it."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, _Function))


def _destroys(calls: list[ast.Call]) -> bool:
    for call in calls:
        name = _call_name(call)
        if name in _DESTROYERS:
            return True
        owner = call.func.value if isinstance(call.func, ast.Attribute) else None
        if name == _SHIBOKEN_DELETE and isinstance(owner, ast.Name) and owner.id.startswith("shiboken"):
            return True
    return False


def _check_function(function: _Function, panel_classes: set[str], path: str) -> Iterator[Finding]:
    calls = [node for node in _own_nodes(function) if isinstance(node, ast.Call)]
    if _destroys(calls):
        return
    # A panel passed straight into another call (`qtbot.addWidget(Panel())`, a
    # clean-up fixture) has been handed to something that owns it.
    handed_over = {id(arg) for call in calls for arg in [*call.args, *(kw.value for kw in call.keywords)]}
    for call in calls:
        name = _call_name(call)
        if id(call) in handed_over or not (name == _PANEL_FACTORY or name in panel_classes):
            continue
        yield make_finding(
            "KS270",
            path,
            f"`{function.name}` builds the plugin panel with `{name}()` and never destroys it",
            line=call.lineno,
            col=call.col_offset + 1,
        )


def run_test_rules(tests: list[SourceFile], panel_classes: set[str]) -> Iterator[Finding]:
    """Yield findings for the repo's parsed test files."""
    for source in tests:
        for node in ast.walk(source.tree):
            if isinstance(node, _Function):
                yield from _check_function(node, panel_classes, source.rel)


# ── Test quality (KS37x, design mode) ────────────────────────────────

# A call whose name contains one of these is an assertion in all but name
# (`mock.assert_called_once()`, `pytest.raises`, `qtbot.waitSignal`, ...).
_ASSERTING_CALLS = ("assert", "raises", "warns", "expect", "verify", "check", "wait")
_SKIP_MARKS = ("skip", "skipif", "xfail")
_EXISTENCE_CALLS = ("isinstance", "hasattr", "callable")
# A test named for surviving something checks that nothing raised, deliberately.
_NO_CRASH_TEST = re.compile(r"does_?n[o']?t_(?:raise|crash)|survives|no_crash|without_(?:raising|crashing)")


def _asserting_helpers(tree: ast.Module) -> set[str]:
    """Module-level helpers in a test file that assert (so calling one asserts)."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, _Function) and any(isinstance(n, ast.Assert) for n in ast.walk(node))
    }


def _is_test(function: _Function) -> bool:
    if not function.name.startswith("test"):
        return False
    for decorator in function.decorator_list:
        text = ast.unparse(decorator)
        if any(f".{mark}" in text for mark in _SKIP_MARKS):
            return False
    return True


def _asserts_something(function: _Function, helpers: set[str]) -> bool:
    for node in _own_nodes(function):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            name = (_call_name(node) or "").lower()
            if any(word in name for word in _ASSERTING_CALLS) or name in helpers:
                return True
        if isinstance(node, ast.With) and any("raises" in ast.unparse(i.context_expr) for i in node.items):
            return True
    return False


def _is_existence_check(test: ast.expr) -> bool:
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Name):
        return test.func.id in _EXISTENCE_CALLS
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot):
        return isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None
    # A bare `assert x.field` is left alone: on data it is a real content check.
    return False


def _only_checks_existence(function: _Function) -> bool:
    asserts = [n for n in _own_nodes(function) if isinstance(n, ast.Assert)]
    if not asserts or not all(_is_existence_check(a.test) for a in asserts):
        return False
    # A mock assertion or pytest.raises alongside them is a real check.
    calls = [n for n in _own_nodes(function) if isinstance(n, ast.Call)]
    return not any("assert" in (_call_name(c) or "") or "raises" in (_call_name(c) or "") for c in calls)


def signal_attributes(sources: list[SourceFile]) -> dict[str, set[str]]:
    """Class name -> names of the Qt signals it declares, for the repo's classes."""
    signals: dict[str, set[str]] = {}
    for source in sources:
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = {
                target.id
                for statement in node.body
                if isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.Call)
                and (_call_name(statement.value) or "") in ("Signal", "pyqtSignal")
                for target in statement.targets
                if isinstance(target, ast.Name)
            }
            if names:
                signals.setdefault(node.name, set()).update(names)
    return signals


def _patched_signal(call: ast.Call, signals: dict[str, set[str]]) -> str | None:
    """`Cls.signal` when `call` is `patch.object(Cls, "signal")` or `patch("...Cls.signal")`."""
    func = ast.unparse(call.func)
    if func.endswith("patch.object") and len(call.args) >= 2:  # noqa: PLR2004 - target and attribute name
        owner, name = call.args[0], call.args[1]
        owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", None)
        if isinstance(name, ast.Constant) and owner_name and name.value in signals.get(owner_name, set()):
            return f"{owner_name}.{name.value}"
    elif func.endswith("patch") and call.args and isinstance(call.args[0], ast.Constant):
        parts = str(call.args[0].value).split(".")
        if len(parts) >= 2 and parts[-1] in signals.get(parts[-2], set()):  # noqa: PLR2004 - class and attribute
            return f"{parts[-2]}.{parts[-1]}"
    return None


def run_test_quality_rules(tests: list[SourceFile], signals: dict[str, set[str]]) -> Iterator[Finding]:
    """Yield KS370-KS372 for the repo's parsed test files."""
    for source in tests:
        helpers = _asserting_helpers(source.tree)
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Call) and (patched := _patched_signal(node, signals)):
                yield make_finding(
                    "KS372", source.rel, f"patches the signal `{patched}`, which intercepts nothing", line=node.lineno
                )
            if not (isinstance(node, _Function) and _is_test(node)):
                continue
            if not _asserts_something(node, helpers) and not _NO_CRASH_TEST.search(node.name):
                yield make_finding("KS370", source.rel, f"`{node.name}` asserts nothing", line=node.lineno)
            elif _only_checks_existence(node):
                yield make_finding(
                    "KS371", source.rel, f"`{node.name}` only checks that things exist", line=node.lineno
                )
