"""Static SDK-conformance evaluator behind `karcytics-sdk doctor`.

Reads a repo (it never imports or runs it) and reports where it departs from
the SDK's conventions, in two families:

    - organizational (KS1xx): layout, manifest, signing, CI, tooling.
    - functional (KS2xx): places the SDK should have been used and was not.
"""

from .model import Finding, Mode, Rule, Severity
from .registry import RULES
from .report import render_json, render_text
from .runner import Report, evaluate_repo

__all__ = [
    "RULES",
    "Finding",
    "Mode",
    "Report",
    "Rule",
    "Severity",
    "evaluate_repo",
    "render_json",
    "render_text",
]
