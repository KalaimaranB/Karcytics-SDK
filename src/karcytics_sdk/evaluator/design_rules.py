"""Design checks (KS3xx): structure, duplication and layering.

These catch what tends to accumulate when nobody is looking at the whole
codebase at once, which is exactly the situation of an agent editing one
file at a time: files and classes that keep growing, helpers re-written
instead of reused, suppressions in place of fixes, UI code doing science.
Most of them are meant to run against a baseline (see `baseline.py`), so
existing debt is tolerated while new debt is not.
"""

from __future__ import annotations

from collections.abc import Iterator

from .model import Finding
from .registry import make_finding
from .source import SourceFile


def _check_suppressions(source: SourceFile) -> Iterator[Finding]:
    for directive in source.unexplained():
        if directive.kind == "type: ignore":
            how = "add `  # <reason>` after it"
        else:
            how = "add the reason after the codes"
        yield make_finding(
            "KS310",
            source.rel,
            f"`{directive.kind}` without a reason ({how})",
            line=directive.line,
        )


def run_design_rules(sources: list[SourceFile]) -> Iterator[Finding]:
    """Yield every design finding for `sources`."""
    for source in sources:
        yield from _check_suppressions(source)
