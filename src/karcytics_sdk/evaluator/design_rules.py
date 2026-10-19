"""Design checks (KS3xx): structure, duplication and layering.

These catch what tends to accumulate when nobody is looking at the whole
codebase at once, which is exactly the situation of an agent editing one
file at a time: files and classes that keep growing, helpers re-written
instead of reused, suppressions in place of fixes, layers of indirection
that only forward calls. Most are meant to run against a baseline (see
`baseline.py`), so existing debt is tolerated while new debt is not.

Per-file rules run on any set of files; the cross-file ones (KS320, KS321,
KS350, KS351) need the whole repo and are skipped by `--files`.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import re
import tokenize
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations

from . import catalog
from .model import Finding
from .registry import make_finding
from .source import SourceFile

MAX_FILE_LINES = 600
MAX_CLASS_METHODS = 30
MAX_FUNCTION_LINES = 80

# KS340: a class is pass-through when at least this share of its methods,
# and at least _PASS_THROUGH_MIN of them, only forward to a collaborator.
_PASS_THROUGH_SHARE = 0.6
_PASS_THROUGH_MIN = 5

# KS320: a private helper re-defined, with a similar body, in at least this
# many modules. Sharing just the name (`_draw`, `_setup_events`) is not enough.
_SAME_HELPER_MODULES = 2
_SAME_HELPER_SIMILARITY = 0.6
# KS321: smallest body (in statements, nested ones included) worth comparing.
_CLONE_MIN_STATEMENTS = 8

# Private names that are a public part of a stdlib API, so reading them from
# another object is not reaching into its internals.
_PUBLIC_PRIVATES = {
    "_replace",
    "_asdict",
    "_fields",
    "_field_defaults",
    "_make",
    "_getframe",
    "_exit",
    "_value_",
    "_name_",
    "_missing_",
    "_generate_next_value_",
}

# KS320: names that are conventionally re-defined in every class that needs
# them (Qt/SDK hooks), so a repeat is not a copied helper.
_CONVENTIONAL_HELPERS = {
    "_setup_ui",
    "_build_ui",
    "_init_ui",
    "_connect_signals",
    "_apply_theme_styles",
    "_retranslate",
    "_cleanup",
    "_refresh",
    "_update",
}

_LOG_METHODS = {"warning", "error", "critical", "exception"}
_DEBUG_TAG = re.compile(r"^\[[A-Za-z0-9_. -]{1,40}\]")
# KS332: a field annotation naming a widget-like type.
_UI_ANNOTATION = re.compile(r"\bQ[A-Z]\w+|\w*(?:Widget|Panel|Viewer|Window|Dialog|Canvas)\b")
_PLAN_REFERENCE = re.compile(r"\bPriority \d\b|\b(?:analysis|UI) #\d|\(Done\)|\b(?:per|from|in) the plan\b")

_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _body_without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return body[1:]
    return body


def _decorator_names(node: _Function | ast.ClassDef) -> set[str]:
    names = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_private(name: str) -> bool:
    return name.startswith("_") and not _is_dunder(name)


def _own_private_names(node: ast.ClassDef) -> set[str]:
    """Private methods and `self._x` attributes a class defines."""
    names = {m.name for m in node.body if isinstance(m, _Function)}
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Store):
            if isinstance(child.value, ast.Name) and child.value.id == "self":
                names.add(child.attr)
    return {n for n in names if _is_private(n)}


# ── Per-file checks ──────────────────────────────────────────────────


class _DesignChecker(ast.NodeVisitor):
    """Walks one file for the per-file design rules."""

    def __init__(self, source: SourceFile, analysis_only: tuple[str, ...] = ()) -> None:
        self.source = source
        self._analysis_only = analysis_only if source.zone != "analysis" else ()
        self.findings: list[Finding] = []
        self._scope: list[str] = []  # enclosing class/function names
        self._functions = 0  # how many functions enclose the current node
        self._dunder_method = 0  # inside __eq__ and friends, other._x is idiomatic
        self._module_classes = {n.name for n in source.tree.body if isinstance(n, ast.ClassDef)}
        # Private names each enclosing class defines: another instance of the
        # same class reading them (`other._mask` in a tree node) is its own business.
        self._class_privates: list[set[str]] = []

    def _add(self, rule_id: str, node: ast.AST, message: str, **extra) -> None:
        self.findings.append(
            make_finding(
                rule_id,
                self.source.rel,
                message,
                line=getattr(node, "lineno", None),
                col=getattr(node, "col_offset", 0) + 1,
                **extra,
            )
        )

    def run(self) -> list[Finding]:
        lines = len(self.source.lines)
        if lines > MAX_FILE_LINES:
            self.findings.append(
                make_finding(
                    "KS300",
                    self.source.rel,
                    f"{lines} lines (limit {MAX_FILE_LINES})",
                    metric=lines,
                )
            )
        self.visit(self.source.tree)
        return self.findings

    # ── Classes ───────────────────────────────────────────────────────

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        methods = [n for n in node.body if isinstance(n, _Function)]
        qualname = ".".join([*self._scope, node.name])
        if len(methods) > MAX_CLASS_METHODS:
            self._add(
                "KS301",
                node,
                f"`{node.name}` has {len(methods)} methods (limit {MAX_CLASS_METHODS})",
                subject=qualname,
                metric=len(methods),
            )
        self._check_static_only(node, methods)
        self._check_pass_through(node, methods)
        if self._is_state_class(node):
            self._check_state_fields(node)
        self._scope.append(node.name)
        self._class_privates.append(_own_private_names(node))
        self.generic_visit(node)
        self._class_privates.pop()
        self._scope.pop()

    def _check_static_only(self, node: ast.ClassDef, methods: list[_Function]) -> None:
        # A class with bases (Protocol, ABC, Enum, an exception...) is more
        # than a namespace, whatever its methods look like.
        if node.bases or node.keywords or len(methods) < 2:  # noqa: PLR2004 - one static method is just a function
            return
        if all("staticmethod" in _decorator_names(m) for m in methods):
            self._add("KS341", node, f"`{node.name}` only has static methods: it is a module of functions")

    def _check_pass_through(self, node: ast.ClassDef, methods: list[_Function]) -> None:
        candidates = [m for m in methods if not _is_dunder(m.name) and "property" not in _decorator_names(m)]
        targets = [t for t in (self._forwarded_to(m) for m in candidates) if t]
        if len(targets) < _PASS_THROUGH_MIN or len(targets) / len(candidates) < _PASS_THROUGH_SHARE:
            return
        names = ", ".join(f"`self.{t}`" for t in sorted(set(targets)))
        self._add(
            "KS340",
            node,
            f"{len(targets)} of `{node.name}`'s {len(candidates)} methods only forward a call to {names}",
        )

    def _is_state_class(self, node: ast.ClassDef) -> bool:
        for base in node.bases:
            name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
            if name.endswith("State"):
                return True
        return node.name.endswith("State") and "dataclass" in _decorator_names(node)

    def _check_state_fields(self, node: ast.ClassDef) -> None:
        for field in node.body:
            if not (isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)):
                continue
            name, annotation = field.target.id, ast.unparse(field.annotation)
            if _UI_ANNOTATION.search(annotation):
                self._add("KS332", field, f"`{node.name}.{name}: {annotation}` holds a UI object")
            elif _is_private(name) and "Any" in annotation:
                self._add("KS332", field, f"`{node.name}.{name}: {annotation}` is untyped and private: a UI object?")

    @staticmethod
    def _forwarded_to(method: _Function) -> str | None:
        """The `self.<attr>` a method's single statement forwards a call to."""
        body = _body_without_docstring(method.body)
        if len(body) != 1:
            return None
        statement = body[0]
        call = statement.value if isinstance(statement, ast.Return | ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            return None
        receiver = call.func.value
        if isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name) and receiver.value.id == "self":
            return receiver.attr
        return None

    # ── Functions ─────────────────────────────────────────────────────

    def visit_FunctionDef(self, node: _Function) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: _Function) -> None:
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        qualname = ".".join([*self._scope, node.name])
        if length > MAX_FUNCTION_LINES:
            self._add(
                "KS302",
                node,
                f"`{qualname}` is {length} lines (limit {MAX_FUNCTION_LINES})",
                subject=qualname,
                metric=length,
            )
        dunder = _is_dunder(node.name)
        self._scope.append(node.name)
        self._functions += 1
        self._dunder_method += dunder
        self.generic_visit(node)
        self._dunder_method -= dunder
        self._functions -= 1
        self._scope.pop()

    # ── Imports ───────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        self._check_local_import(node, ", ".join(a.name for a in node.names))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_local_import(node, "." * node.level + (node.module or ""))

    def _check_local_import(self, node: ast.AST, what: str) -> None:
        if self._functions:
            self._add("KS360", node, f"`{what}` imported inside a function")

    # ── Attribute access ──────────────────────────────────────────────

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_analysis_only(node, node.attr, ast.unparse(node))
        if _is_private(node.attr) and node.attr not in _PUBLIC_PRIVATES and not self._dunder_method:
            owner = node.value
            same_class = bool(self._class_privates) and node.attr in self._class_privates[-1]
            if not same_class and not self._is_own(owner):
                self._add("KS342", node, f"`{ast.unparse(node)}` reads another object's private attribute")
        self.generic_visit(node)

    def _check_analysis_only(self, node: ast.AST, name: str, dotted: str) -> None:
        for entry in self._analysis_only:
            if ("." in entry and (dotted == entry or dotted.endswith("." + entry))) or entry == name:
                self._add("KS331", node, f"`{dotted}` is reserved to analysis/ in this plugin")
                return

    def _is_own(self, owner: ast.expr) -> bool:
        """Whether `owner._x` is access to one's own (or one's class's) internals."""
        if isinstance(owner, ast.Name):
            return (
                owner.id in ("self", "cls")
                or owner.id in self._module_classes
                or owner.id in self.source.imports  # a module's private helper
            )
        if isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name):
            return owner.func.id in ("super", "type")
        return False

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self._check_analysis_only(func, func.id, func.id)
        if isinstance(func, ast.Name) and func.id in ("getattr", "hasattr", "setattr") and len(node.args) >= 2:  # noqa: PLR2004 - object and name
            self._check_reflective(node, func.id)
        elif isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS:
            self._check_tagged_log(node, func)
        self.generic_visit(node)

    def _check_reflective(self, node: ast.Call, how: str) -> None:
        target, name = node.args[0], node.args[1]
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)) or _is_dunder(name.value):
            return
        is_self = isinstance(target, ast.Name) and target.id == "self"
        if is_self and how != "setattr":
            self._add(
                "KS343",
                node,
                f"`{how}(self, {name.value!r}...)` probes an attribute of its own object",
            )
        elif not is_self and _is_private(name.value) and not self._is_own(target) and not self._dunder_method:
            self._add("KS342", node, f"`{how}(..., {name.value!r})` reads another object's private attribute")

    def _check_tagged_log(self, node: ast.Call, func: ast.Attribute) -> None:
        receiver = ast.unparse(func.value).lower()
        if "log" not in receiver or not node.args:
            return
        first = node.args[0]
        text = None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            text = first.value
        elif isinstance(first, ast.JoinedStr) and first.values and isinstance(first.values[0], ast.Constant):
            text = str(first.values[0].value)
        if text and _DEBUG_TAG.match(text):
            self._add("KS361", node, f"`{func.attr}` used for a tagged trace: {text[:40]!r}")

    # ── Comparisons ───────────────────────────────────────────────────

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(self._is_type_name(side) for side in [node.left, *node.comparators]):
            self._add("KS344", node, f"`{ast.unparse(node)[:60]}` branches on a type's name")
        self.generic_visit(node)

    @staticmethod
    def _is_type_name(node: ast.expr) -> bool:
        if not (isinstance(node, ast.Attribute) and node.attr == "__name__"):
            return False
        inner = node.value
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
            return inner.func.id == "type"
        return isinstance(inner, ast.Attribute) and inner.attr == "__class__"


def _imports_ui(source: SourceFile) -> bool:
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            modules = [(a.name, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules = [(node.module, a.name) for a in node.names]
        else:
            continue
        for module, name in modules:
            if module.split(".")[0] in catalog.UI_LIBRARIES:
                return True
            if module.startswith(catalog.SDK_PACKAGE) and (
                name in catalog.SDK_UI_NAMES
                or name.startswith("Bio")
                or any(part in segment for segment in module.split(".")[2:] for part in catalog.SDK_UI_MODULES)
                or name in catalog.SDK_UI_MODULES
            ):
                return True
    return False


def _check_ui_without_ui(source: SourceFile) -> Iterator[Finding]:
    if source.zone == "ui" and not source.rel.endswith("__init__.py") and not _imports_ui(source):
        yield make_finding("KS330", source.rel, "no Qt, plotting or SDK UI import: does this belong in analysis/?")


def _check_suppressions(source: SourceFile) -> Iterator[Finding]:
    for directive in source.unexplained():
        if directive.kind == "type: ignore":
            how = "add `  # <reason>` after it"
        else:
            how = "add the reason after the codes"
        yield make_finding("KS310", source.rel, f"`{directive.kind}` without a reason ({how})", line=directive.line)


def _check_plan_references(source: SourceFile) -> Iterator[Finding]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source.text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    for token in tokens:
        if token.type == tokenize.COMMENT and _PLAN_REFERENCE.search(token.string):
            yield make_finding(
                "KS362",
                source.rel,
                f"comment refers to a plan item: {token.string.strip()[:60]!r}",
                line=token.start[0],
            )


# ── Cross-file checks ────────────────────────────────────────────────


@dataclass(frozen=True)
class _Definition:
    source: SourceFile
    node: _Function
    class_name: str | None


def _definitions(sources: list[SourceFile]) -> list[_Definition]:
    found: list[_Definition] = []
    for source in sources:
        for node in source.tree.body:
            if isinstance(node, _Function):
                found.append(_Definition(source, node, None))
            elif isinstance(node, ast.ClassDef):
                found.extend(_Definition(source, m, node.name) for m in node.body if isinstance(m, _Function))
    return found


def _class_hierarchy(sources: list[SourceFile]) -> dict[str, tuple[set[str], set[str]]]:
    """Class name -> (base class names, method names), for classes in the repo."""
    classes: dict[str, tuple[set[str], set[str]]] = {}
    for source in sources:
        for node in ast.walk(source.tree):
            if isinstance(node, ast.ClassDef):
                bases = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases}
                methods = {m.name for m in node.body if isinstance(m, _Function)}
                classes[node.name] = (bases, methods)
    return classes


def _overrides(definition: _Definition, classes: dict[str, tuple[set[str], set[str]]]) -> bool:
    """Whether a method re-defines one inherited from a class in the repo."""
    seen: set[str] = set()
    pending = list(classes.get(definition.class_name or "", (set(), set()))[0])
    while pending:
        base = pending.pop()
        if base in seen or base not in classes:
            continue
        seen.add(base)
        bases, methods = classes[base]
        if definition.node.name in methods:
            return True
        pending.extend(bases)
    return False


def _shape(function: _Function) -> list[str]:
    """A function body as a token sequence: node types plus the names and constants it uses."""
    tokens: list[str] = []
    for node in ast.walk(ast.Module(body=_body_without_docstring(function.body), type_ignores=[])):
        tokens.append(type(node).__name__)
        if isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str | int | float):
            tokens.append(repr(node.value)[:20])
    return tokens


def _similar_groups(definitions: list[_Definition]) -> list[list[_Definition]]:
    """Definitions whose bodies are alike, grouped (single-link) and spanning several modules."""
    shapes = [_shape(d.node) for d in definitions]
    parent = list(range(len(definitions)))

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for i, j in combinations(range(len(definitions)), 2):
        if definitions[i].source.rel == definitions[j].source.rel:
            continue
        if SequenceMatcher(None, shapes[i], shapes[j], autojunk=False).ratio() >= _SAME_HELPER_SIMILARITY:
            parent[root(i)] = root(j)
    groups: dict[int, list[_Definition]] = defaultdict(list)
    for index, definition in enumerate(definitions):
        groups[root(index)].append(definition)
    return [g for g in groups.values() if len({d.source.rel for d in g}) >= _SAME_HELPER_MODULES]


def _check_same_helpers(sources: list[SourceFile]) -> Iterator[Finding]:
    classes = _class_hierarchy(sources)
    by_name: dict[str, list[_Definition]] = defaultdict(list)
    for definition in _definitions(sources):
        name = definition.node.name
        if not _is_private(name) or name.startswith("_on_") or name in _CONVENTIONAL_HELPERS:
            continue
        if definition.class_name and _overrides(definition, classes):
            continue
        by_name[name].append(definition)
    for name, definitions in sorted(by_name.items()):
        if len({d.source.rel for d in definitions}) < _SAME_HELPER_MODULES:
            continue
        for group in _similar_groups(definitions):
            modules = sorted({d.source.rel for d in group})
            for definition in group:
                others = [m.rsplit("/", 1)[-1] for m in modules if m != definition.source.rel]
                yield make_finding(
                    "KS320",
                    definition.source.rel,
                    f"`{name}` is also written out in {', '.join(others)}",
                    line=definition.node.lineno,
                    col=definition.node.col_offset + 1,
                )


class _Normaliser(ast.NodeTransformer):
    """Renames a function's own variables so copies that differ only in naming compare equal."""

    def __init__(self, local_names: set[str]) -> None:
        self._local = local_names
        self._renamed: dict[str, str] = {}

    def _rename(self, name: str) -> str:
        if name not in self._local:
            return name
        return self._renamed.setdefault(name, f"v{len(self._renamed)}")

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self._rename(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._rename(node.arg)
        node.annotation = None
        return node


def _fingerprint(function: _Function) -> str | None:
    body = _body_without_docstring(function.body)
    if sum(isinstance(n, ast.stmt) for stmt in body for n in ast.walk(stmt)) < _CLONE_MIN_STATEMENTS:
        return None
    local = {a.arg for a in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
    local |= {n.id for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    clone = copy.deepcopy(ast.Module(body=body, type_ignores=[]))
    normalised = _Normaliser(local).visit(clone)
    dump = ast.dump(normalised, annotate_fields=False, include_attributes=False)
    return hashlib.sha1(dump.encode(), usedforsecurity=False).hexdigest()


def _check_clones(sources: list[SourceFile]) -> Iterator[Finding]:
    groups: dict[str, list[_Definition]] = defaultdict(list)
    for definition in _definitions(sources):
        fingerprint = _fingerprint(definition.node)
        if fingerprint:
            groups[fingerprint].append(definition)
    for definitions in groups.values():
        if len(definitions) < 2:  # noqa: PLR2004 - a clone needs a pair
            continue
        for definition in definitions:
            others = [f"{d.source.rel.rsplit('/', 1)[-1]}:{d.node.lineno}" for d in definitions if d is not definition]
            yield make_finding(
                "KS321",
                definition.source.rel,
                f"`{definition.node.name}` has the same body as {', '.join(others)}",
                line=definition.node.lineno,
                col=definition.node.col_offset + 1,
            )


def _topic_key(node: ast.expr, by_value: dict[str, str]) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return by_value.get(node.value)
    return None


def _topic_constants(sources: list[SourceFile]) -> dict[str, tuple[SourceFile, ast.Assign]]:
    """UPPER_CASE string constants defined in a module about events or topics."""
    constants: dict[str, tuple[SourceFile, ast.Assign]] = {}
    for source in sources:
        module = source.rel.rsplit("/", 1)[-1]
        if "event" not in module and "topic" not in module:
            continue
        for node in source.tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.isupper()
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.targets[0].id] = (source, node)
    return constants


@dataclass
class _BusUsage:
    published: dict[str, list[tuple[SourceFile, ast.Call]]]
    # How often each name is mentioned anywhere, and how often as a publish() topic.
    mentions: dict[str, int]
    publish_mentions: dict[str, int]


def _bus_usage(sources: list[SourceFile], constants: dict[str, tuple[SourceFile, ast.Assign]]) -> _BusUsage:
    """Where topics are published, and how often each one is mentioned at all.

    A string literal equal to a topic's value is a mention of that topic, so a
    subscription written as `subscribe("flow.gate.renamed", ...)` still counts.
    """
    by_value = {
        assign.value.value: key
        for key, (_, assign) in constants.items()
        if isinstance(assign.value, ast.Constant) and isinstance(assign.value.value, str)
    }
    definitions = {id(assign.value) for _, assign in constants.values()}
    usage = _BusUsage(defaultdict(list), defaultdict(int), defaultdict(int))
    for source in sources:
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Attribute):
                usage.mentions[node.attr] += 1
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                usage.mentions[node.id] += 1
            elif isinstance(node, ast.Constant) and id(node) not in definitions:
                key = _topic_key(node, by_value)
                if key is not None:
                    usage.mentions[key] += 1
            elif isinstance(node, ast.Call):
                _record_publish(node, source, usage, by_value)
    return usage


def _record_publish(node: ast.Call, source: SourceFile, usage: _BusUsage, by_value: dict[str, str]) -> None:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "publish" and node.args):
        return
    if "bus" not in ast.unparse(func.value).lower():
        return
    key = _topic_key(node.args[0], by_value)
    if key is not None:
        usage.published[key].append((source, node))
        usage.publish_mentions[key] += 1


def _check_event_topics(sources: list[SourceFile]) -> Iterator[Finding]:
    """KS350/KS351, for the plugin's own topic constants.

    A topic counts as handled when its name appears anywhere but as a
    publish() argument: subscribed directly, through a helper, in a list of
    topics, or in a lookup table. That is what keeps this free of false
    positives; the cost is missing a topic that is only mentioned in passing.
    """
    constants = _topic_constants(sources)
    if not constants:
        return
    usage = _bus_usage(sources, constants)
    for key, sites in sorted(usage.published.items()):
        if key in constants and usage.mentions[key] <= usage.publish_mentions[key]:
            for source, call in sites:
                yield make_finding(
                    "KS350",
                    source.rel,
                    f"`{key}` is published but nothing in the plugin handles it",
                    line=call.lineno,
                    col=call.col_offset + 1,
                )
    for key, (source, assign) in sorted(constants.items()):
        if not usage.mentions[key]:
            yield make_finding("KS351", source.rel, f"topic `{key}` is never used", line=assign.lineno)


# ── Entry point ──────────────────────────────────────────────────────


def run_design_rules(
    sources: list[SourceFile], *, whole_repo: bool = True, analysis_only: tuple[str, ...] = ()
) -> Iterator[Finding]:
    """Yield every design finding for `sources`.

    The cross-file rules need every file at once, so they only run when
    `whole_repo` is set. `analysis_only` is the plugin's list of calls only
    analysis/ may make (KS331).
    """
    for source in sources:
        yield from _DesignChecker(source, analysis_only).run()
        yield from _check_ui_without_ui(source)
        yield from _check_suppressions(source)
        yield from _check_plan_references(source)
    if whole_repo:
        yield from _check_same_helpers(sources)
        yield from _check_clones(sources)
        yield from _check_event_topics(sources)
