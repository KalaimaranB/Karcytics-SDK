"""One parsed source file, shared by every rule family that reads it.

Also reads the file's inline suppressions:

    # karcytics: allow KS302 - builds the whole menu in one place
    # karcytics: allow-file KS300 - generated table of course steps
    x = f()  # noqa: PLR0913 - mirrors the Qt signature
    y = g()  # type: ignore[arg-type]  # PySide6 stub omits Optional

Every suppression must say why. One without a reason is reported (KS310),
and a `karcytics: allow` without one does not suppress anything. mypy
rejects anything after a `type: ignore` other than a second comment, hence
the `# reason` form there.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

_ALLOW = re.compile(r"karcytics:\s*allow(?P<file>-file)?\s+(?P<rules>KS\d{3}(?:\s*,\s*KS\d{3})*)(?P<rest>.*)", re.S)
_NOQA = re.compile(r"noqa\b(?::\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?(?P<rest>.*)", re.S)
_TYPE_IGNORE = re.compile(r"type:\s*ignore\b(?:\[[^\]]*\])?(?P<rest>.*)", re.S)
_DIRECTIVES = (("allow", _ALLOW), ("noqa", _NOQA), ("type: ignore", _TYPE_IGNORE))
_REASON_SEPARATORS = " \t-—–:#"


@dataclass(frozen=True)
class Directive:
    """One inline suppression comment."""

    line: int
    kind: str  # "allow", "noqa" or "type: ignore"
    reason: str
    rules: tuple[str, ...] = ()
    whole_file: bool = False
    comment_only: bool = False  # the line holds nothing but the comment


def _reason(rest: str) -> str:
    reason = rest.strip(_REASON_SEPARATORS)
    return reason if any(ch.isalpha() for ch in reason) else ""


def _parse_comment(text: str, line: int, comment_only: bool) -> list[Directive]:
    """Directives in one comment token, e.g. `# type: ignore[x]  # because`."""
    segments = [segment.strip() for segment in text.split("#")[1:]]
    found: list[Directive] = []
    for index, segment in enumerate(segments):
        for kind, pattern in _DIRECTIVES:
            match = pattern.match(segment)
            if not match:
                continue
            # The reason may follow in later `#` segments, unless those are
            # directives of their own (`# noqa: E501  # type: ignore`).
            rest = [match["rest"]]
            for later in segments[index + 1 :]:
                if any(p.match(later) for _, p in _DIRECTIVES):
                    break
                rest.append(later)
            rules = tuple(r.strip() for r in match["rules"].split(",")) if kind == "allow" else ()
            whole_file = kind == "allow" and bool(match["file"])
            found.append(Directive(line, kind, _reason(" ".join(rest)), rules, whole_file, comment_only))
            break
    return found


def parse_directives(text: str) -> list[Directive]:
    directives: list[Directive] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return directives
    lines = text.splitlines()
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        row, col = token.start
        comment_only = not lines[row - 1][:col].strip()
        directives.extend(_parse_comment(token.string, row, comment_only))
    return directives


def collect_imports(tree: ast.AST) -> dict[str, str]:
    """Map each locally-bound import name to the dotted path it refers to."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


@dataclass
class SourceFile:
    """A parsed `.py` file: its text, AST, imports and suppressions."""

    path: Path
    rel: str
    zone: str
    text: str
    tree: ast.Module
    directives: list[Directive] = field(default_factory=list)

    @classmethod
    def parse(cls, path: Path, rel: str, zone: str) -> SourceFile:
        """Raises SyntaxError/OSError/UnicodeDecodeError/ValueError when unreadable."""
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
        return cls(path, rel, zone, text, tree, parse_directives(text))

    @cached_property
    def imports(self) -> dict[str, str]:
        return collect_imports(self.tree)

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    def allows(self, rule_id: str, line: int | None) -> bool:
        """Whether an explained `karcytics: allow` covers `rule_id` at `line`.

        It covers the line it is on, or the line below when it sits on a
        line of its own. A `karcytics: allow-file` covers the whole file.
        """
        lines = self.lines
        for directive in self.directives:
            if directive.kind != "allow" or not directive.reason or rule_id not in directive.rules:
                continue
            if directive.whole_file or directive.line == line:
                return True
            if directive.comment_only and line is not None and self._next_code_line(directive.line, lines) == line:
                return True
        return False

    @staticmethod
    def _next_code_line(after: int, lines: list[str]) -> int | None:
        for number in range(after + 1, len(lines) + 1):
            stripped = lines[number - 1].strip()
            if stripped and not stripped.startswith("#"):
                return number
        return None

    def unexplained(self) -> list[Directive]:
        return [d for d in self.directives if not d.reason]
