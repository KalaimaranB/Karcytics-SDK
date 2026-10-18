"""Data model for the SDK conformance evaluator (`karcytics-sdk doctor`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class Severity(StrEnum):
    """How serious a finding is. Only `ERROR` fails the run by default."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


class Mode(StrEnum):
    """Which family a rule belongs to."""

    ORG = "org"
    FUNCTIONAL = "functional"
    DESIGN = "design"


@dataclass(frozen=True)
class Rule:
    """Static description of one check: what it is and how to fix it."""

    id: str
    mode: Mode
    severity: Severity
    title: str
    remedy: str


@dataclass(frozen=True)
class Finding:
    """One violation, located as precisely as static analysis allows.

    `path` is relative to the evaluated repo root. `line`/`col` are 1-based
    and `None` for repo-level findings (a missing file has no line).

    A measured finding (a long file, a class with too many methods) carries
    the measurement in `metric` and what was measured in `subject` (the class
    or function name, "" for the file itself). The baseline ratchets the
    measurement rather than counting the finding.
    """

    rule_id: str
    severity: Severity
    path: str
    message: str
    remedy: str
    line: int | None = None
    col: int | None = None
    subject: str = ""
    metric: int | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

    @property
    def sort_key(self) -> tuple:
        return (self.path, self.line or 0, self.col or 0, self.rule_id)

    @property
    def baseline_key(self) -> str:
        """What the baseline tracks this finding under, within its file."""
        return f"{self.rule_id}:{self.subject}" if self.subject else self.rule_id
