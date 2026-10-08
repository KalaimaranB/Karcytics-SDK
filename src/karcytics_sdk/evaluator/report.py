"""Renders an evaluator `Report` as coloured terminal text or JSON."""

from __future__ import annotations

import json
from collections import Counter
from itertools import groupby

from .model import Finding, Mode, Severity
from .registry import RULES
from .runner import Report

_RESET = "\033[0m"
_BOLD = "1"
_DIM = "2"
_GREEN = "32"
_SEVERITY_STYLE = {Severity.ERROR: "1;31", Severity.WARNING: "33", Severity.INFO: "36"}
_MODE_TITLE = {Mode.ORG: "Organizational", Mode.FUNCTIONAL: "Functional"}
_RULE_WIDTH = 72


class _Painter:
    """Wraps text in ANSI colour codes, or leaves it alone when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, style: str) -> str:
        return f"\033[{style}m{text}{_RESET}" if self.enabled else text


def _location(finding: Finding) -> str:
    if finding.line is None:
        return "-"
    return f"{finding.line}:{finding.col}" if finding.col else str(finding.line)


def _render_file(path: str, findings: list[Finding], paint: _Painter) -> list[str]:
    width = max(len(_location(f)) for f in findings)
    indent = " " * (width + 4)
    lines = [paint(path, _BOLD)]
    for finding in findings:
        severity = paint(f"{finding.severity.value:<7}", _SEVERITY_STYLE[finding.severity])
        location = paint(f"{_location(finding):>{width}}", _DIM)
        lines.append(f"  {location}  {severity}  {paint(finding.rule_id, _DIM)}  {finding.message}")
        lines.append(f"{indent}{paint('fix:', _GREEN)} {finding.remedy}")
    lines.append("")
    return lines


def _render_section(title: str, findings: list[Finding], paint: _Painter) -> list[str]:
    heading = f"── {title} ".ljust(_RULE_WIDTH, "─")
    lines = [paint(heading, _BOLD), ""]
    if not findings:
        return [*lines, paint("  No violations.", _GREEN), ""]
    for path, group in groupby(findings, key=lambda f: f.path):
        lines.extend(_render_file(path, list(group), paint))
    return lines


def _render_summary(shown: list[Finding], hidden_info: int, report: Report, paint: _Painter) -> list[str]:
    lines = [paint("── Summary ".ljust(_RULE_WIDTH, "─"), _BOLD), ""]
    counts = Counter((f.rule_id, f.severity) for f in shown)
    for (rule_id, severity), count in sorted(counts.items(), key=lambda item: (item[0][1].rank, item[0][0])):
        label = paint(f"{severity.value:<7}", _SEVERITY_STYLE[severity])
        lines.append(f"  {rule_id}  {label}  {count:>4}  {RULES[rule_id].title}")
    if counts:
        lines.append("")

    errors, warnings = report.count(Severity.ERROR), report.count(Severity.WARNING)
    totals = [
        paint(f"{errors} error(s)", _SEVERITY_STYLE[Severity.ERROR] if errors else _GREEN),
        paint(f"{warnings} warning(s)", _SEVERITY_STYLE[Severity.WARNING] if warnings else _GREEN),
    ]
    if hidden_info:
        totals.append(paint(f"{hidden_info} info hidden (--show-info)", _DIM))
    else:
        totals.append(paint(f"{report.count(Severity.INFO)} info", _SEVERITY_STYLE[Severity.INFO]))
    lines.append("  " + ", ".join(totals))
    return lines


def render_text(report: Report, show_info: bool = False, color: bool = True) -> str:
    """Human-readable report, grouped by mode then file. Info is hidden unless `show_info`."""
    paint = _Painter(color)
    shown = [f for f in report.findings if show_info or f.severity is not Severity.INFO]
    hidden_info = len(report.findings) - len(shown)

    plugin = f"plugin `{report.plugin_id}`" if report.plugin_id else "not a Karcytics plugin"
    lines = [paint("Karcytics SDK Doctor", _BOLD), f"  {report.root}  ({plugin})"]
    if Mode.FUNCTIONAL in report.modes:
        lines.append(f"  {report.files_scanned} source file(s) analysed")
    lines.append("")

    for mode in report.modes:
        section = [f for f in shown if RULES[f.rule_id].mode is mode]
        lines.extend(_render_section(_MODE_TITLE[mode], section, paint))
    lines.extend(_render_summary(shown, hidden_info, report, paint))
    return "\n".join(lines)


def render_json(report: Report) -> str:
    """Machine-readable report. Always contains every finding, info included."""
    payload = {
        "repo": str(report.root),
        "plugin_id": report.plugin_id,
        "modes": [mode.value for mode in report.modes],
        "files_scanned": report.files_scanned,
        "summary": {severity.value: report.count(severity) for severity in Severity},
        "findings": [finding.to_dict() for finding in report.findings],
    }
    return json.dumps(payload, indent=2)
