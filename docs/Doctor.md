# SDK Doctor

`karcytics-sdk doctor` reads a repo and reports where it departs from the SDK's conventions. It is static analysis only: the repo is never imported or run, so it needs no virtualenv, no Qt display, and works on any checkout.

On its own it is advisory: only errors fail a run. Its real job is as a ratchet that runs on every edit and every commit (see [Baseline](#baseline) and [Running it on every edit](#running-it-on-every-edit)), so that what it finds stops growing even before it is fixed.

```bash
karcytics-sdk doctor                      # check the current directory
karcytics-sdk doctor ../Karcytics-western-blot
karcytics-sdk doctor . --mode org         # structure, CI and tooling only
karcytics-sdk doctor . --mode functional  # SDK usage in the source only
karcytics-sdk doctor . --mode design      # structure, duplication and layering only
karcytics-sdk doctor . --show-info        # include info-level findings
karcytics-sdk doctor . --select KS23,KS201   # only these rule-id prefixes
karcytics-sdk doctor . --ignore KS240        # everything except these
karcytics-sdk doctor . --format json      # machine-readable, always includes info
karcytics-sdk doctor . --files src/karcytics_plugins/demo/ui/panel.py   # just these files
karcytics-sdk doctor . --baseline         # fail only on what is over .karcytics-baseline.json
karcytics-sdk doctor . --update-baseline  # lower the baseline to what is found now
```

## Reading the output

Findings are grouped by file. Each one gives the line and column, the severity, the rule id, what was found, and how to fix it:

```text
src/karcytics_plugins/synthetic_biology/ui/views/simulate_view.py
  28:1  warning  KS230  `SimulateWorker` subclasses `QThread` directly
        fix: Subclass karcytics_sdk.plugin.OneShotWorkerThread, or submit an AnalysisBase/FunctionalTask to karcytics_sdk.plugin.task_scheduler.
```

| Severity | Meaning | Shown by default | Fails the run |
| --- | --- | --- | --- |
| error | Breaks the plugin contract (red) | yes | yes |
| warning | The SDK provides this and it was not used (yellow) | yes | only with `--strict` |
| info | Worth a look, often legitimate (cyan) | no, use `--show-info` | never |

Colour is used when writing to a terminal and switched off when piped, with `--no-color`, or when `NO_COLOR` is set. The exit code is 1 when the run fails and 0 otherwise.

## Baseline

A repo with existing debt would drown in warnings that nobody acts on. A baseline turns them into a ratchet instead: `.karcytics-baseline.json`, committed to the repo, records how many findings of each rule every file has (for a measured rule, such as `KS300` "long file", the measurement itself). Run against it, the doctor fails when any of those goes up or a new one appears, and lists only those. Everything below the line is known debt and stays quiet.

```bash
karcytics-sdk doctor . --update-baseline   # first time: record today's state
karcytics-sdk doctor . --baseline          # every commit: fail on anything new
karcytics-sdk doctor . --update-baseline   # after a clean-up: lock the improvement in
```

`--update-baseline` only ever lowers entries (it creates the file when there is none). Accepting new debt takes `--reset-baseline`, which rewrites the file from the current state; with `--files` it rewrites only those files' entries. Either way the change shows up in review as a diff of the baseline file. Only errors and warnings are tracked, and errors fail the run whether or not they are in the baseline.

## Suppressions

A finding that is genuinely fine can be allowed in place, with a reason:

```python
def build_menu(self):  # karcytics: allow KS302 - one flat list of menu entries, splitting it hides the order
    ...

# karcytics: allow KS342 - the driver is the documented owner of this cache
cache = driver._cache
```

The comment covers its own line, or the next line of code when it sits on a line of its own. `# karcytics: allow-file KS300 - <why>` covers a whole file. A `karcytics: allow` without a reason suppresses nothing.

Every suppression needs a reason, including the linters' own: `# noqa: PLR0913 - mirrors the Qt signature`, and `# type: ignore[arg-type]  # PySide6 stub omits Optional` (mypy accepts only a second comment after `type: ignore`). One without a reason is reported as `KS310`. The point is to make "suppress instead of fix" a decision someone wrote down, not a reflex.

## Running it on every edit

The doctor is most useful where code is being written, not after the fact. Three places, each with the repo's baseline:

- **The agent loop.** `--claude-hook` reads a Claude Code `PostToolUse` payload from stdin, checks just the file that was written, and exits 2 with the findings on stderr when the edit added something over the baseline (Claude Code shows those to the agent, which fixes them in the same turn). Without a baseline file it fails on any warning. In `.claude/settings.json`:

    ```json
    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [{"type": "command", "command": "uv run karcytics-sdk doctor --claude-hook"}]
          }
        ]
      }
    }
    ```

- **Pre-commit.** A `karcytics-doctor` hook running `karcytics-sdk doctor . --baseline`.
- **CI.** The same command, so a commit made with `--no-verify` is still caught.

`--files` skips what needs the whole repo at once: the organizational rules, `KS260`/`KS261`, and the cross-file design rules. Pre-commit and CI run those.

## What is analysed

- **A plugin repo** (one with `[tool.karcytics.plugin]` in `pyproject.toml`): every `.py` file under `src/karcytics_plugins/<id>/`.
- **Any other repo**: one `KS100`/`KS101` error says it is not a plugin, and the functional rules still run over every `.py` file outside `tests/`, virtualenvs, and build output.

Files under `analysis/` and `ui/` are treated differently where it matters: Qt is an error in `analysis/` (and an SDK object that is a Qt object, such as `Debouncer`, a warning), hard-coded colours are only checked outside it, and plain Python threads are info in `analysis/` (which cannot use Qt's) but a warning elsewhere.

The files under `tests/` are read by the test rules only: `KS270` here, and `KS370`-`KS372` (test quality) among the design rules. `KS270` It reports a test or fixture that builds the plugin's panel (a `PluginBase` subclass, or `create_panel()`) without passing it to anything or calling `deleteLater()`, `qtbot.addWidget()` or `shiboken6.delete()`. It recognises that shape, not the leak itself: Python then garbage-collects the panel's wrappers while Qt still owns the widgets, and an unrelated test fails at random.

Names are resolved through each file's imports, so aliased and module-qualified uses (`QtWidgets.QPushButton(...)`) are found, and a local class that merely shares a Qt name is not.

## Organizational rules

| Rule | Severity | Checks for | Fix |
| --- | --- | --- | --- |
| `KS100` | error | No pyproject.toml | Add a pyproject.toml at the repo root (see `karcytics-sdk bootstrap`). |
| `KS101` | error | No plugin manifest | Declare the plugin under a [tool.karcytics.plugin] table in pyproject.toml. |
| `KS102` | error | Manifest key missing | Add the key to [tool.karcytics.plugin] in pyproject.toml. |
| `KS103` | error | Invalid process_model | Set process_model to "isolated" or "in_process". |
| `KS104` | error | Project metadata missing | Set name and version under [project] in pyproject.toml. |
| `KS110` | error | Plugin package not at the standard path | Put the plugin's code under src/karcytics_plugins/<id>/ where <id> is the manifest id. |
| `KS111` | error | No analysis/ package | Create src/karcytics_plugins/<id>/analysis/ for pure, Qt-free compute (AnalysisBase subclasses). |
| `KS112` | error | No ui/ package | Create src/karcytics_plugins/<id>/ui/ for PySide6 code (PluginBase subclass, widgets, dialogs). |
| `KS113` | error | Entry point does not resolve | Point entry_point at a real "module:function" that returns a PluginBase instance. |
| `KS120` | error | Signing artefact missing | Run `karcytics-sdk sign <plugin_dir>` and commit the generated file. |
| `KS121` | error | No author signing key | Run `karcytics-sdk init-identity` and set author_public_key under [tool.karcytics.plugin.signing]. |
| `KS130` | error | No release workflow | Add .github/workflows/release.yml (copy it from an existing plugin repo). |
| `KS131` | warning | No workflow runs the tests | Run pytest from a GitHub Actions workflow so every push or PR is tested. |
| `KS132` | warning | No pre-commit config | Add .pre-commit-config.yaml with the standard local hooks (ruff, mypy, pytest, sign, ledger). |
| `KS133` | warning | Pre-commit hook missing | Add the hook to .pre-commit-config.yaml. |
| `KS134` | warning | No agent hook for the doctor | Add a PostToolUse hook running `uv run karcytics-sdk doctor --claude-hook` to .claude/settings.json (see docs/Doctor.md), so an agent sees what an edit broke before it moves on. |
| `KS140` | warning | No tests/ directory | Add a tests/ directory with a tests/unit/ suite. |
| `KS141` | warning | No tests/unit/ suite | Put fast, headless tests under tests/unit/ - that is what pre-commit runs. |
| `KS142` | error | No SDK contract test | Subclass karcytics_sdk.testing.ContractTestBase in a test file and set PLUGIN_DIR. |
| `KS150` | warning | No uv.lock | Run `uv lock` and commit uv.lock so installs are reproducible. |
| `KS151` | error | karcytics-sdk is not a declared dependency | Add "karcytics-sdk>=<version>" to [project] dependencies. |
| `KS152` | info | No local source for karcytics-sdk | Add `karcytics-sdk = { path = "../Karcytics-SDK", editable = true }` under [tool.uv.sources]. |
| `KS160` | warning | No ruff configuration | Add ruff.toml or a [tool.ruff] table. |
| `KS161` | warning | No mypy configuration | Add a [tool.mypy] table or mypy.ini. |
| `KS170` | warning | No README.md | Add a README.md describing the plugin and how to develop it. |
| `KS171` | warning | No LICENSE file | Add a LICENSE file at the repo root. |
| `KS172` | info | No CLAUDE.md | Add a CLAUDE.md that links back to the Hub's ECOSYSTEM.md. |
| `KS173` | info | No docs site | Add mkdocs.yml and a docs/ directory. |

## Functional rules

| Rule | Severity | Checks for | Fix |
| --- | --- | --- | --- |
| `KS200` | warning | File could not be parsed | Fix the syntax error so the file can be analysed. |
| `KS201` | error | Imports the Hub package | A plugin may only import karcytics_sdk. Use the SDK equivalent or request one. |
| `KS202` | error | Qt imported in analysis/ | Keep analysis/ free of Qt: move the widget or signal code to ui/ and run compute through AnalysisBase. |
| `KS203` | error | Imports a Qt binding other than PySide6 | Karcytics is built on PySide6 (LGPL). Import from PySide6 instead: pyqtSignal/pyqtSlot/pyqtProperty become Signal/Slot/Property, and sip.isdeleted(x) becomes `not shiboken6.isValid(x)`. |
| `KS204` | error | Signal or Slot declared with a dict argument | Declare the argument as `object`: PySide6 converts a `dict` signal argument to a QVariantMap and silently drops every non-string key. |
| `KS205` | warning | Qt-backed SDK object in analysis/ | Keep analysis/ to pure computation: move the scheduling, debouncing or signalling that needs this into ui/ (or a controller there) and pass analysis/ plain data. |
| `KS210` | warning | Raw Qt widget where the SDK provides one | Use the SDK component instead. |
| `KS211` | warning | Subclasses a raw Qt widget the SDK wraps | Subclass the SDK component instead. |
| `KS220` | warning | Raw Qt dialog | Use the helper from karcytics_sdk.plugin.dialogs instead. |
| `KS230` | warning | Hand-rolled Qt threading | Subclass karcytics_sdk.plugin.OneShotWorkerThread, or submit an AnalysisBase/FunctionalTask to karcytics_sdk.plugin.task_scheduler. |
| `KS231` | warning | Hand-rolled Python threading | Wrap the work in karcytics_sdk.plugin.managed_task.FunctionalTask and submit it to karcytics_sdk.plugin.task_scheduler. |
| `KS240` | warning | Hard-coded colour | Use a Colors token from karcytics_sdk.plugin.theme_fallback so the widget follows the active theme. |
| `KS241` | info | Direct setStyleSheet call | Prefer a Bio* component, apply_component_style(), or theme_manager.apply_style() so the style is re-applied when the theme changes. |
| `KS242` | warning | Themed stylesheet built by hand | Use theme_manager.apply_style(widget, "QLabel {{ color: {FG_PRIMARY}; }}") (tokens in braces) so the style follows theme changes, or a Bio* component that already does. |
| `KS243` | warning | Combo box rebuilt by hand | Use repopulate_combo(combo, items, restore_data=...) from karcytics_sdk.plugin: it keeps the selection, blocks signals, and does nothing when nothing changed. |
| `KS250` | warning | Standard-library logger | Use karcytics_sdk.plugin.get_logger(__name__) so records carry plugin metadata. |
| `KS251` | info | Hand-rolled JSON file I/O | Use load_json()/save_json() from karcytics_sdk.plugin. |
| `KS252` | warning | print() call | Use karcytics_sdk.plugin.get_logger(__name__) instead of print(). |
| `KS260` | warning | No PluginBase subclass | The plugin's main panel should subclass karcytics_sdk.plugin.PluginBase. |
| `KS261` | info | No AnalysisBase subclass | Put computation in a karcytics_sdk.plugin.AnalysisBase subclass under analysis/. |
| `KS270` | warning | Test builds the plugin panel without destroying it | Destroy it when the test ends: `qtbot.addWidget(panel)`, or `panel.deleteLater()` in a fixture's teardown. A panel left to the garbage collector makes unrelated tests fail intermittently. |

A Qt widget is only flagged when the SDK has a real replacement. `QCheckBox`, for example, has none and is never reported. The full mapping lives in `karcytics_sdk/evaluator/catalog.py`; add a row there when the SDK gains a component.

## Design rules

These catch what accumulates when no one looks at the whole codebase at once, which is the normal situation of an agent editing one file at a time. Most are meant to run against a [baseline](#baseline).

| Rule | Severity | Checks for | Fix |
| --- | --- | --- | --- |
| `KS300` | warning | Long file | Split it along a real responsibility (a widget, a service, a model), not just to get under the limit. |
| `KS301` | warning | Class with too many methods | It is doing several jobs: move each one into its own class and have this one delegate or compose. |
| `KS302` | warning | Long function | Extract the steps it runs through into named functions, or the widget it builds into its own class. |
| `KS310` | warning | Suppression without a reason | Say why after the codes (`# noqa: PLR0913 - mirrors the Qt signature`, `# type: ignore[arg-type]  # PySide6 stub omits Optional`, `# karcytics: allow KS302 - <why>`). Better still, fix what is being suppressed. |
| `KS320` | warning | Same helper defined in several modules | Keep one copy in a shared module (or the SDK, if it is not plugin-specific) and import it. |
| `KS321` | warning | Duplicated function body | Keep one copy and call it from both places. |
| `KS330` | info | ui/ module with no UI code | If it is computation or data handling, move it to analysis/ where it can be tested headless. |
| `KS331` | warning | Call reserved to analysis/ | Go through the analysis-layer API instead; the plugin lists this call under [tool.karcytics.evaluator] analysis_only in pyproject.toml. |
| `KS332` | warning | UI object stored on state | Keep widgets out of state: state is data the analysis layer reads. Give whatever needs the widget a reference to it, or a query object, instead. |
| `KS340` | warning | Pass-through class | Let callers use the collaborator directly, or move the logic it forwards to into this class. |
| `KS341` | warning | Class with only static methods | Make them module-level functions: a class that is never instantiated is just a namespace. |
| `KS342` | warning | Reads another object's private attribute | Give the owner a public method or property for what the caller needs, and use that. |
| `KS343` | warning | getattr/hasattr on self | Set the attribute in __init__ (to None if it has no value yet) and read it directly. |
| `KS344` | warning | Branches on a type's name | Use isinstance(), or better, a method or property the types implement differently. |
| `KS350` | warning | Event published but never subscribed | Stop publishing it, or subscribe where the event was meant to be handled. |
| `KS351` | warning | Event topic never used | Delete the constant. |
| `KS360` | warning | Import inside a function | Import at the top of the module. If it is deliberate (startup time, an optional dependency), say so: `# karcytics: allow KS360 - <why>`. An import cycle is a design problem to fix, not hide. |
| `KS361` | warning | Debug trace logged as a warning | Log it with logger.debug(), and keep warnings for what a user or maintainer should act on. |
| `KS370` | warning | Test asserts nothing | Assert on the outcome the test is about. A test that only checks nothing crashed passes when the behaviour is wrong. |
| `KS371` | warning | Test only checks that things exist | Assert on behaviour (values, emitted signals, state after an action), not that an attribute exists or an object has a type. |
| `KS372` | warning | Test patches a Qt signal | Under PySide6 a signal is resolved on the instance, so patching it on the class intercepts nothing: connect a real slot and assert on what it received. |
| `KS362` | info | Comment refers to a plan item | Say what the code does and why; the history of how it got there belongs in the commit message. |

The limits (600 lines per file, 30 methods per class, 80 lines per function) are deliberately generous: under a baseline they only stop growth, and a split should follow a real responsibility, not a number. `KS320` needs a similar body as well as the same name, and skips methods that override one from a base class in the repo and conventional hooks such as `_setup_ui` and `_apply_theme_styles`. `KS370` accepts any `assert`, a call whose name contains assert/raises/warns/expect/verify/check/wait, or a call to a helper in the same file that asserts; a test named for surviving something (`..._does_not_raise`, `..._survives_...`) is exempt. `KS371` fires when every assert is `isinstance`/`hasattr`/`callable`/`is not None`.

`KS331` reads the plugin's own list of calls that only analysis/ may make:

```toml
[tool.karcytics.evaluator]
analysis_only = ["apply_hierarchy", "fcs_data.events"]   # a name, or the end of a dotted path
```

`KS332` looks at classes that subclass a `...State` class, or are dataclasses named `...State`. `KS350`/`KS351` look only at the plugin's own topic constants (UPPER_CASE strings in a module whose name contains "event" or "topic"); a topic counts as handled when it is mentioned anywhere other than as a `publish()` argument, so subscribing through a helper or a list of topics is recognised.

## Adding a rule

1. Declare it in `evaluator/registry.py` (id, severity, title, default fix). The id's hundreds digit is its family: 1 organizational, 2 functional, 3 design.
2. Emit it with `make_finding(...)` from a check in `org_rules.py`, `_FileChecker` in `functional_rules.py`, or `design_rules.py` (`test_rules.py` for a check over the repo's tests).
3. Add a test under `tests/unit/evaluator/` and a row to the table above (a test fails if a rule is missing from this page).
