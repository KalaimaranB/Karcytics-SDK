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
