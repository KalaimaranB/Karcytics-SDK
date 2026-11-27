"""Read every Academy course in a repository into plain JSON, without importing or running it.

Courses are Python (``Course(...)`` from ``karcytics_sdk.plugin.tutorial_models``), and a course's
steps are often spread over several files: ``steps=[..., *COMPENSATION_STEPS, ...]`` pulls a list
from another module, which may itself splice in more (``[*_EMISSION_STEPS, *_LASER_STEPS]``), and
a step can be held in a variable and listed by name. This module follows all of that with ``ast``,
so it needs only the standard library, never imports PySide6 or the plugin, and is safe on any
checkout. The admin portal's Academy review shows the result.

What it can't know statically is marked rather than guessed:

* f-strings keep their literal text, with each ``{expression}`` shown as ``{…}``;
* ``step.text = ...`` / ``setattr(step, "text", ...)`` elsewhere in the code (text that changes
  with the learner's own results) is listed under the step's ``variants`` when the step is a
  module-level variable, and counted in ``warnings`` otherwise;
* anything that isn't a step, a list of steps or a name for one is reported in ``warnings``.

Run: ``python -m karcytics_sdk.academy.export <repo-or-src-dir> [--module ID] [--out FILE]``
or ``karcytics-sdk academy-export``.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT = 1

STEP_CLASSES = {
    "InfoStep",
    "InteractionStep",
    "VerificationStep",
    "ActionStep",
    "BranchingStep",
    "ForcedInteractionStep",
    "SubplotCheckStep",
    "WaitForEventStep",
    "ConsentStep",
    "QuestionStep",
}
# Fields a reviewer reads, in the order they're shown.
TEXT_FIELDS = ("text", "explanation", "failure_hint", "stuck_hint_text", "accept_text", "decline_text")
LINK_FIELDS = ("next_step_id", "on_success_step_id", "on_fail_step_id", "on_accept_step_id", "on_decline_step_id")
COURSE_FIELDS = ("title", "description", "badge_reward", "badge_icon")

PLACEHOLDER = "{…}"
MAX_IMPORT_DEPTH = 10  # from-import chains followed to find a name
MAX_SPLICE_DEPTH = 20  # lists spliced into lists
MIN_HELPER_TEXT = 20  # shorter literals in a text helper are labels, not prose
SHORT_NAMES = 3  # loop variables like t, e: not steps
SETATTR_ARGS = 3
_HEADING_RE = re.compile(r"^\s*#\s*──+\s*(.+?)\s*─*\s*$")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", "site", ".tox"}


@dataclass
class Module:
    name: str  # dotted, relative to the scan root
    path: Path
    rel: str  # path shown to reviewers
    tree: ast.Module
    lines: list[str]
    assigns: dict[str, ast.expr] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)  # local name -> (module, name)
    headings: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Ctx:
    modules: dict[str, Module]
    warnings: list[str] = field(default_factory=list)


# ── loading ──────────────────────────────────────────────────────────────────


def _discover(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).parts
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel[:-1]):
            continue
        if rel[0] in ("tests", "test"):
            continue
        out.append(p)
    return out


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(mod: Module, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    is_pkg = mod.path.name == "__init__.py"
    base = mod.name.split(".")
    base = base if is_pkg else base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def load(root: Path, repo_root: Path | None = None) -> Ctx:  # noqa: C901 - one pass over each file's top level
    """Parse every Python file under ``root`` that mentions a course or a step."""
    repo_root = repo_root or root
    ctx = Ctx(modules={})
    for path in _discover(root):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(k in source for k in ("Step(", "Course(", "_STEPS", "COURSES")):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            ctx.warnings.append(f"{path.relative_to(repo_root)}: can't parse ({e.msg}, line {e.lineno})")
            continue
        lines = source.splitlines()
        mod = Module(_module_name(root, path), path, str(path.relative_to(repo_root)), tree, lines)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        mod.assigns[t.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                mod.assigns[node.target.id] = node.value
            elif isinstance(node, ast.ImportFrom):
                src = _resolve_relative(mod, node)
                for alias in node.names:
                    mod.imports[alias.asname or alias.name] = (src, alias.name)
        mod.headings = [(i + 1, m.group(1).strip()) for i, line in enumerate(lines) if (m := _HEADING_RE.match(line))]
        ctx.modules[mod.name] = mod
    return ctx


def _lookup(ctx: Ctx, mod: Module, name: str, depth: int = 0) -> tuple[Module, ast.expr] | None:
    """The expression a module-level name stands for, following ``from x import name``."""
    if depth > MAX_IMPORT_DEPTH:
        return None
    if name in mod.assigns:
        return mod, mod.assigns[name]
    if name in mod.imports:
        src, orig = mod.imports[name]
        target = ctx.modules.get(src) or next(
            (m for k, m in ctx.modules.items() if k.endswith("." + src) or src.endswith("." + k)), None
        )
        if target is not None:
            return _lookup(ctx, target, orig, depth + 1)
    return None


# ── values ───────────────────────────────────────────────────────────────────


def _string(ctx: Ctx, mod: Module, node: ast.expr | None) -> tuple[str | None, bool]:  # noqa: PLR0911 - one return per kind of string expression
    """(text, dynamic). ``dynamic`` is True when part of it is only known at run time."""
    if node is None:
        return None, False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append(PLACEHOLDER)
        return "".join(out), True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, da = _string(ctx, mod, node.left)
        b, db = _string(ctx, mod, node.right)
        if a is None and b is None:
            return None, True
        return (a if a is not None else PLACEHOLDER) + (b if b is not None else PLACEHOLDER), True if (
            da or db or a is None or b is None
        ) else False
    if isinstance(node, ast.Name):
        found = _lookup(ctx, mod, node.id)
        if found and isinstance(found[1], (ast.Constant, ast.JoinedStr, ast.BinOp)):
            return _string(ctx, found[0], found[1])
        return PLACEHOLDER, True
    return PLACEHOLDER, True


def _literal(node: ast.expr | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {k.arg: k.value for k in call.keywords if k.arg}


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return None


def _text_hash(step: dict[str, Any]) -> str:
    parts = [step.get(f) or "" for f in TEXT_FIELDS]
    parts += [c["text"] + "\0" + c.get("feedback", "") for c in step.get("choices", [])]
    parts += [o["label"] for o in step.get("options", [])]
    parts += [t["instruction"] for t in step.get("sub_tasks", [])]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


# ── steps ────────────────────────────────────────────────────────────────────


def _heading(mod: Module, line: int, floor: int) -> str | None:
    best = None
    for ln, title in mod.headings:
        if floor <= ln < line:
            best = title
        elif ln >= line:
            break
    return best


def _arg(call: ast.Call, name: str, index: int) -> ast.expr | None:
    """A call's argument given by keyword or by position."""
    kw = _kwargs(call)
    if name in kw:
        return kw[name]
    return call.args[index] if len(call.args) > index else None


def _choices(ctx: Ctx, mod: Module, node: ast.expr) -> list[dict[str, Any]]:
    out = []
    for c in node.elts if isinstance(node, (ast.List, ast.Tuple)) else []:
        if not isinstance(c, ast.Call) or _call_name(c) != "AnswerChoice":
            continue
        text, _ = _string(ctx, mod, _arg(c, "text", 0))
        feedback, _ = _string(ctx, mod, _arg(c, "feedback", 2))
        correct = _literal(_arg(c, "correct", 1))
        out.append(
            {
                "text": text or "",
                **({"correct": True} if correct else {}),
                **({"feedback": feedback} if feedback else {}),
            }
        )
    return out


def _question(ctx: Ctx, mod: Module, kw: dict[str, ast.expr]) -> dict[str, Any]:
    out: dict[str, Any] = {"choices": _choices(ctx, mod, kw["choices"])}
    for k in ("kind", "question_id", "multi_select"):
        if k in kw and (v := _literal(kw[k])) is not None:
            out[k] = v
    kind = kw.get("kind")
    if "kind" not in out and isinstance(kind, ast.Name):
        out["kind"] = kind.id.removeprefix("QUESTION_").lower()
    return out


def _extras(ctx: Ctx, mod: Module, kw: dict[str, ast.expr]) -> dict[str, Any]:
    """Branch buttons, sub-tasks and links to other steps."""
    out: dict[str, Any] = {}
    opts = kw.get("options")
    if isinstance(opts, ast.Dict):
        out["options"] = [
            {"label": _string(ctx, mod, k)[0] or "", "next": _literal(v)}
            for k, v in zip(opts.keys, opts.values, strict=True)
            if k is not None
        ]
    tasks = kw.get("sub_tasks")
    if isinstance(tasks, (ast.List, ast.Tuple)):
        out["sub_tasks"] = [
            {
                "id": _literal(_kwargs(t).get("id")) or "",
                "instruction": _string(ctx, mod, _kwargs(t).get("instruction"))[0] or "",
            }
            for t in tasks.elts
            if isinstance(t, ast.Call)
        ]
    links = {f: v for f in LINK_FIELDS if f in kw and isinstance(v := _literal(kw[f]), str)}
    if links:
        out["links"] = links
    return out


def _step(ctx: Ctx, mod: Module, call: ast.Call, kind: str, *, floor: int, var: str | None) -> dict[str, Any]:  # noqa: PLR0913
    kw = _kwargs(call)
    step: dict[str, Any] = {"id": _literal(kw.get("id")) or "", "type": kind, "file": mod.rel, "line": call.lineno}
    if heading := _heading(mod, call.lineno, floor):
        step["section"] = heading
    dynamic = []
    for f in TEXT_FIELDS:
        text, dyn = _string(ctx, mod, kw.get(f))
        if text is not None:
            step[f] = text
            if dyn:
                dynamic.append(f)
    if "choices" in kw:
        step |= _question(ctx, mod, kw)
    step |= _extras(ctx, mod, kw)
    if dynamic:
        step["dynamic"] = dynamic
    if var:
        step["_var"] = (mod.name, var)
    step["hash"] = _text_hash(step)
    return step


def _steps(  # noqa: C901, PLR0911, PLR0913, PLR0917 - one branch per way a course can list steps
    ctx: Ctx, mod: Module, node: ast.expr, floor: int, var: str | None = None, depth: int = 0
) -> list[dict[str, Any]]:
    """Every step an expression stands for: a step, a list of steps, a name or a *splice of either."""
    if depth > MAX_SPLICE_DEPTH:
        ctx.warnings.append(f"{mod.rel}:{node.lineno}: steps nested too deeply")
        return []
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[dict[str, Any]] = []
        for el in node.elts:
            out += _steps(ctx, mod, el.value if isinstance(el, ast.Starred) else el, node.lineno, None, depth + 1)
        return out
    name = _call_name(node)
    if name in STEP_CLASSES:
        return [_step(ctx, mod, node, name, floor=floor, var=var)]  # type: ignore[arg-type]
    if isinstance(node, ast.Name):
        found = _lookup(ctx, mod, node.id)
        if found:
            target_mod, expr = found
            is_list = isinstance(expr, (ast.List, ast.Tuple))
            # A step held in a variable sits in the section where it's listed, not where it's defined.
            got = _steps(ctx, target_mod, expr, expr.lineno if is_list else 0, None if is_list else node.id, depth + 1)
            here = _heading(mod, node.lineno, floor)
            for st in got:
                if not is_list:
                    st.pop("section", None)
                if here and "section" not in st:
                    st["section"] = here  # spliced from a file with no heading of its own
            return got
        ctx.warnings.append(f"{mod.rel}:{node.lineno}: couldn't find {node.id}")
        return []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _steps(ctx, mod, node.left, floor, None, depth + 1) + _steps(
            ctx, mod, node.right, floor, None, depth + 1
        )
    ctx.warnings.append(
        f"{mod.rel}:{getattr(node, 'lineno', '?')}: a step the export can't read ({ast.unparse(node)[:60]})"
    )
    return []


# ── text set while the course runs ──────────────────────────────────────────


def _runtime_texts(ctx: Ctx) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], list[str]]:  # noqa: C901 - every form that sets text
    """``name.text = ...`` and ``setattr(name, "text", ...)``, keyed by (module, variable)."""
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    unplaced: list[str] = []
    for mod in ctx.modules.values():
        for node in ast.walk(mod.tree):
            target: str | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Attribute) and t.attr == "text":
                    target = t.value.id if isinstance(t.value, ast.Name) else "?"
                    value = node.value
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) == SETATTR_ARGS
            ):
                if _literal(node.args[1]) == "text":
                    target = node.args[0].id if isinstance(node.args[0], ast.Name) else "?"
                    value = node.args[2]
            if target is None or value is None:
                continue
            text, _ = _string(ctx, mod, value)
            where = f"{mod.rel}:{getattr(node, 'lineno', '?')}"
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                # A helper builds it (e.g. from the learner's clustering): show the helper's literal text.
                helper = mod.assigns.get(value.func.id)
                fn = next(
                    (n for n in mod.tree.body if isinstance(n, ast.FunctionDef) and n.name == value.func.id), None
                )
                if fn is not None:
                    pieces = [
                        s
                        for n in ast.walk(fn)
                        if isinstance(n, (ast.Constant, ast.JoinedStr))
                        for s in [_string(ctx, mod, n)[0]]
                        if s and len(s) > MIN_HELPER_TEXT
                    ]
                    text = " … ".join(dict.fromkeys(pieces)) or PLACEHOLDER
                elif helper is None:
                    text = PLACEHOLDER
            entry = {"text": text or PLACEHOLDER, "where": where}
            key = (mod.name, target)
            if target != "?" and key in {(m, v) for (m, v) in _all_step_vars(ctx)}:
                found.setdefault(key, []).append(entry)
            elif not (len(target) < SHORT_NAMES or target == "self" or target.endswith("label")):
                unplaced.append(where)
    return found, unplaced


def _all_step_vars(ctx: Ctx) -> list[tuple[str, str]]:
    out = []
    for mod in ctx.modules.values():
        for name, expr in mod.assigns.items():
            if _call_name(expr) in STEP_CLASSES:
                out.append((mod.name, name))
    return out


# ── courses ──────────────────────────────────────────────────────────────────


def _course_order(ctx: Ctx) -> list[str]:
    """Names in a module-level ``COURSES = (...)``, if there is one."""
    for mod in ctx.modules.values():
        expr = mod.assigns.get("COURSES")
        if isinstance(expr, (ast.List, ast.Tuple)):
            return [e.id for e in expr.elts if isinstance(e, ast.Name)]
    return []


def export(root: Path, module_id: str = "", repo_root: Path | None = None) -> dict[str, Any]:  # noqa: C901, PLR0912 - assembles every course in one pass
    ctx = load(root, repo_root)
    runtime, unplaced = _runtime_texts(ctx)
    courses = []
    for mod in ctx.modules.values():
        for var, expr in mod.assigns.items():
            if _call_name(expr) != "Course":
                continue
            kw = _kwargs(expr)  # type: ignore[arg-type]
            course: dict[str, Any] = {
                "id": _literal(kw.get("id")) or var,
                "var": var,
                "file": mod.rel,
                "line": expr.lineno,
            }
            for f in COURSE_FIELDS:
                s, _ = _string(ctx, mod, kw.get(f))
                if s:
                    course[f] = s
            for f in ("estimated_minutes",):
                if (v := _literal(kw.get(f))) is not None:
                    course[f] = v
            course["prerequisites"] = _literal(kw.get("prerequisite_course_ids")) or []
            steps = _steps(ctx, mod, kw["steps"], expr.lineno) if "steps" in kw else []
            seen: set[str] = set()
            for st in steps:
                key = st.pop("_var", None)
                if key and tuple(key) in runtime:
                    st["variants"] = runtime[tuple(key)]
                if st["id"] in seen:
                    ctx.warnings.append(f"{course['id']}: step id {st['id']} appears twice")
                seen.add(st["id"])
            course["steps"] = steps
            course["files"] = sorted({st["file"] for st in steps} | {mod.rel})
            courses.append(course)
    order = _course_order(ctx)
    courses.sort(key=lambda c: (order.index(c["var"]) if c["var"] in order else len(order), c["file"], c["line"]))
    for i, c in enumerate(courses):
        c["order"] = i + 1
    if unplaced:
        ctx.warnings.append(
            f"{len(unplaced)} text changes made while a course runs aren't tied to a step: " + ", ".join(unplaced[:8])
        )
    return {
        "format": FORMAT,
        "module": module_id,
        "generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "courses": courses,
        "warnings": ctx.warnings,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="academy-export", description=__doc__.split("\n\n")[0])
    ap.add_argument("path", help="repository (or its src/) to read")
    ap.add_argument("--module", default="", help="module id to record, e.g. flow_cytometry")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--commit", default="", help="commit the export was made from (recorded as-is)")
    a = ap.parse_args(argv)
    root = Path(a.path).resolve()
    scan = root / "src" if (root / "src").is_dir() else root
    data = export(scan, a.module, repo_root=root)
    if a.commit:
        data["commit"] = a.commit
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:  # bytes, so a Windows console's code page can't reject the text
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.flush()
    n = sum(len(c["steps"]) for c in data["courses"])
    print(f"{len(data['courses'])} courses, {n} steps, {len(data['warnings'])} warnings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
