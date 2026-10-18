"""Rule catalogue for the evaluator.

Every rule the evaluator can report is declared here, so the id, default
severity, and remedy live in one place and the rule reference in
`docs/Doctor.md` can be checked against it.
"""

from __future__ import annotations

from .model import Finding, Mode, Rule, Severity

_E, _W, _I = Severity.ERROR, Severity.WARNING, Severity.INFO

_ORG_RULES = [
    ("KS100", _E, "No pyproject.toml", "Add a pyproject.toml at the repo root (see `karcytics-sdk bootstrap`)."),
    (
        "KS101",
        _E,
        "No plugin manifest",
        "Declare the plugin under a [tool.karcytics.plugin] table in pyproject.toml.",
    ),
    ("KS102", _E, "Manifest key missing", "Add the key to [tool.karcytics.plugin] in pyproject.toml."),
    ("KS103", _E, "Invalid process_model", 'Set process_model to "isolated" or "in_process".'),
    (
        "KS104",
        _E,
        "Project metadata missing",
        "Set name and version under [project] in pyproject.toml.",
    ),
    (
        "KS110",
        _E,
        "Plugin package not at the standard path",
        "Put the plugin's code under src/karcytics_plugins/<id>/ where <id> is the manifest id.",
    ),
    (
        "KS111",
        _E,
        "No analysis/ package",
        "Create src/karcytics_plugins/<id>/analysis/ for pure, Qt-free compute (AnalysisBase subclasses).",
    ),
    (
        "KS112",
        _E,
        "No ui/ package",
        "Create src/karcytics_plugins/<id>/ui/ for PySide6 code (PluginBase subclass, widgets, dialogs).",
    ),
    (
        "KS113",
        _E,
        "Entry point does not resolve",
        'Point entry_point at a real "module:function" that returns a PluginBase instance.',
    ),
    (
        "KS120",
        _E,
        "Signing artefact missing",
        "Run `karcytics-sdk sign <plugin_dir>` and commit the generated file.",
    ),
    (
        "KS121",
        _E,
        "No author signing key",
        "Run `karcytics-sdk init-identity` and set author_public_key under [tool.karcytics.plugin.signing].",
    ),
    (
        "KS130",
        _E,
        "No release workflow",
        "Add .github/workflows/release.yml (copy it from an existing plugin repo).",
    ),
    (
        "KS131",
        _W,
        "No workflow runs the tests",
        "Run pytest from a GitHub Actions workflow so every push or PR is tested.",
    ),
    (
        "KS132",
        _W,
        "No pre-commit config",
        "Add .pre-commit-config.yaml with the standard local hooks (ruff, mypy, pytest, sign, ledger).",
    ),
    ("KS133", _W, "Pre-commit hook missing", "Add the hook to .pre-commit-config.yaml."),
    ("KS140", _W, "No tests/ directory", "Add a tests/ directory with a tests/unit/ suite."),
    (
        "KS141",
        _W,
        "No tests/unit/ suite",
        "Put fast, headless tests under tests/unit/ - that is what pre-commit runs.",
    ),
    (
        "KS142",
        _W,
        "No SDK contract test",
        "Subclass karcytics_sdk.testing.ContractTestBase in a test file and set PLUGIN_DIR.",
    ),
    ("KS150", _W, "No uv.lock", "Run `uv lock` and commit uv.lock so installs are reproducible."),
    (
        "KS151",
        _E,
        "karcytics-sdk is not a declared dependency",
        'Add "karcytics-sdk>=<version>" to [project] dependencies.',
    ),
    (
        "KS152",
        _I,
        "No local source for karcytics-sdk",
        'Add `karcytics-sdk = { path = "../Karcytics-SDK", editable = true }` under [tool.uv.sources].',
    ),
    ("KS160", _W, "No ruff configuration", "Add ruff.toml or a [tool.ruff] table."),
    ("KS161", _W, "No mypy configuration", "Add a [tool.mypy] table or mypy.ini."),
    ("KS170", _W, "No README.md", "Add a README.md describing the plugin and how to develop it."),
    ("KS171", _W, "No LICENSE file", "Add a LICENSE file at the repo root."),
    ("KS172", _I, "No CLAUDE.md", "Add a CLAUDE.md that links back to the Hub's ECOSYSTEM.md."),
    ("KS173", _I, "No docs site", "Add mkdocs.yml and a docs/ directory."),
]

_FUNCTIONAL_RULES = [
    ("KS200", _W, "File could not be parsed", "Fix the syntax error so the file can be analysed."),
    (
        "KS201",
        _E,
        "Imports the Hub package",
        "A plugin may only import karcytics_sdk. Use the SDK equivalent or request one.",
    ),
    (
        "KS202",
        _E,
        "Qt imported in analysis/",
        "Keep analysis/ free of Qt: move the widget or signal code to ui/ and run compute through AnalysisBase.",
    ),
    (
        "KS203",
        _E,
        "Imports a Qt binding other than PySide6",
        "Karcytics is built on PySide6 (LGPL). Import from PySide6 instead: pyqtSignal/pyqtSlot/pyqtProperty "
        "become Signal/Slot/Property, and sip.isdeleted(x) becomes `not shiboken6.isValid(x)`.",
    ),
    (
        "KS204",
        _E,
        "Signal or Slot declared with a dict argument",
        "Declare the argument as `object`: PySide6 converts a `dict` signal argument to a QVariantMap and silently drops every non-string key.",
    ),
    (
        "KS205",
        _W,
        "Qt-backed SDK object in analysis/",
        "Keep analysis/ to pure computation: move the scheduling, debouncing or signalling that needs this "
        "into ui/ (or a controller there) and pass analysis/ plain data.",
    ),
    ("KS210", _W, "Raw Qt widget where the SDK provides one", "Use the SDK component instead."),
    ("KS211", _W, "Subclasses a raw Qt widget the SDK wraps", "Subclass the SDK component instead."),
    ("KS220", _W, "Raw Qt dialog", "Use the helper from karcytics_sdk.plugin.dialogs instead."),
    (
        "KS230",
        _W,
        "Hand-rolled Qt threading",
        "Subclass karcytics_sdk.plugin.OneShotWorkerThread, or submit an AnalysisBase/FunctionalTask "
        "to karcytics_sdk.plugin.task_scheduler.",
    ),
    (
        "KS231",
        _W,
        "Hand-rolled Python threading",
        "Wrap the work in karcytics_sdk.plugin.managed_task.FunctionalTask and submit it to "
        "karcytics_sdk.plugin.task_scheduler.",
    ),
    (
        "KS240",
        _W,
        "Hard-coded colour",
        "Use a Colors token from karcytics_sdk.plugin.theme_fallback so the widget follows the active theme.",
    ),
    (
        "KS241",
        _I,
        "Direct setStyleSheet call",
        "Prefer a Bio* component, apply_component_style(), or theme_manager.apply_style() so the "
        "style is re-applied when the theme changes.",
    ),
    (
        "KS250",
        _W,
        "Standard-library logger",
        "Use karcytics_sdk.plugin.get_logger(__name__) so records carry plugin metadata.",
    ),
    ("KS251", _I, "Hand-rolled JSON file I/O", "Use load_json()/save_json() from karcytics_sdk.plugin."),
    ("KS252", _W, "print() call", "Use karcytics_sdk.plugin.get_logger(__name__) instead of print()."),
    (
        "KS260",
        _W,
        "No PluginBase subclass",
        "The plugin's main panel should subclass karcytics_sdk.plugin.PluginBase.",
    ),
    (
        "KS261",
        _I,
        "No AnalysisBase subclass",
        "Put computation in a karcytics_sdk.plugin.AnalysisBase subclass under analysis/.",
    ),
    (
        "KS270",
        _W,
        "Test builds the plugin panel without destroying it",
        "Destroy it when the test ends: `qtbot.addWidget(panel)`, or `panel.deleteLater()` in a fixture's "
        "teardown. A panel left to the garbage collector makes unrelated tests fail intermittently.",
    ),
]

_DESIGN_RULES = [
    (
        "KS300",
        _W,
        "Long file",
        "Split it along a real responsibility (a widget, a service, a model), not just to get under the limit.",
    ),
    (
        "KS301",
        _W,
        "Class with too many methods",
        "It is doing several jobs: move each one into its own class and have this one delegate or compose.",
    ),
    (
        "KS302",
        _W,
        "Long function",
        "Extract the steps it runs through into named functions, or the widget it builds into its own class.",
    ),
    (
        "KS310",
        _W,
        "Suppression without a reason",
        "Say why after the codes (`# noqa: PLR0913 - mirrors the Qt signature`, "
        "`# type: ignore[arg-type]  # PySide6 stub omits Optional`, `# karcytics: allow KS302 - <why>`). "
        "Better still, fix what is being suppressed.",
    ),
    (
        "KS320",
        _W,
        "Same helper defined in several modules",
        "Keep one copy in a shared module (or the SDK, if it is not plugin-specific) and import it.",
    ),
    (
        "KS321",
        _W,
        "Duplicated function body",
        "Keep one copy and call it from both places.",
    ),
    (
        "KS330",
        _I,
        "ui/ module with no UI code",
        "If it is computation or data handling, move it to analysis/ where it can be tested headless.",
    ),
    (
        "KS331",
        _W,
        "Call reserved to analysis/",
        "Go through the analysis-layer API instead; the plugin lists this call under "
        "[tool.karcytics.evaluator] analysis_only in pyproject.toml.",
    ),
    (
        "KS332",
        _W,
        "UI object stored on state",
        "Keep widgets out of state: state is data the analysis layer reads. Give whatever needs the widget "
        "a reference to it, or a query object, instead.",
    ),
    (
        "KS340",
        _W,
        "Pass-through class",
        "Let callers use the collaborator directly, or move the logic it forwards to into this class.",
    ),
    (
        "KS341",
        _W,
        "Class with only static methods",
        "Make them module-level functions: a class that is never instantiated is just a namespace.",
    ),
    (
        "KS342",
        _W,
        "Reads another object's private attribute",
        "Give the owner a public method or property for what the caller needs, and use that.",
    ),
    (
        "KS343",
        _W,
        "getattr/hasattr on self",
        "Set the attribute in __init__ (to None if it has no value yet) and read it directly.",
    ),
    (
        "KS344",
        _W,
        "Branches on a type's name",
        "Use isinstance(), or better, a method or property the types implement differently.",
    ),
    (
        "KS350",
        _W,
        "Event published but never subscribed",
        "Stop publishing it, or subscribe where the event was meant to be handled.",
    ),
    (
        "KS351",
        _W,
        "Event topic never used",
        "Delete the constant.",
    ),
    (
        "KS360",
        _W,
        "Import inside a function",
        "Import at the top of the module. If it is deliberate (startup time, an optional dependency), "
        "say so: `# karcytics: allow KS360 - <why>`. An import cycle is a design problem to fix, not hide.",
    ),
    (
        "KS361",
        _W,
        "Debug trace logged as a warning",
        "Log it with logger.debug(), and keep warnings for what a user or maintainer should act on.",
    ),
    (
        "KS362",
        _I,
        "Comment refers to a plan item",
        "Say what the code does and why; the history of how it got there belongs in the commit message.",
    ),
]

RULES: dict[str, Rule] = {
    rule_id: Rule(rule_id, mode, severity, title, remedy)
    for mode, table in ((Mode.ORG, _ORG_RULES), (Mode.FUNCTIONAL, _FUNCTIONAL_RULES), (Mode.DESIGN, _DESIGN_RULES))
    for rule_id, severity, title, remedy in table
}


def make_finding(  # noqa: PLR0913 - keyword-only fields of a Finding
    rule_id: str,
    path: str,
    message: str,
    *,
    line: int | None = None,
    col: int | None = None,
    remedy: str | None = None,
    severity: Severity | None = None,
    subject: str = "",
    metric: int | None = None,
) -> Finding:
    """Build a `Finding` from a rule id, defaulting severity and remedy to the rule's own."""
    rule = RULES[rule_id]
    return Finding(
        rule_id=rule_id,
        severity=severity or rule.severity,
        path=path,
        message=message,
        remedy=remedy or rule.remedy,
        line=line,
        col=col,
        subject=subject,
        metric=metric,
    )
