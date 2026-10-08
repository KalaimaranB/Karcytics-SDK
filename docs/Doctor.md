# SDK Doctor

`karcytics-sdk doctor` reads a repo and reports where it departs from the SDK's conventions. It is static analysis only: the repo is never imported or run, so it needs no virtualenv, no Qt display, and works on any checkout.

It is advisory. Nothing requires a plugin to pass it today; run it when you want a list of what to tidy up.

```bash
karcytics-sdk doctor                      # check the current directory
karcytics-sdk doctor ../Karcytics-western-blot
karcytics-sdk doctor . --mode org         # structure, CI and tooling only
karcytics-sdk doctor . --mode functional  # SDK usage in the source only
karcytics-sdk doctor . --show-info        # include info-level findings
karcytics-sdk doctor . --select KS23,KS201   # only these rule-id prefixes
karcytics-sdk doctor . --ignore KS240        # everything except these
karcytics-sdk doctor . --format json      # machine-readable, always includes info
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

## What is analysed

- **A plugin repo** (one with `[tool.karcytics.plugin]` in `pyproject.toml`): every `.py` file under `src/karcytics_plugins/<id>/`.
- **Any other repo**: one `KS100`/`KS101` error says it is not a plugin, and the functional rules still run over every `.py` file outside `tests/`, virtualenvs, and build output.

Files under `analysis/` and `ui/` are treated differently where it matters: Qt is an error in `analysis/`, hard-coded colours are only checked outside it, and plain Python threads are info in `analysis/` (which cannot use Qt's) but a warning elsewhere.

The files under `tests/` are read for one rule only, `KS270`. It reports a test or fixture that builds the plugin's panel (a `PluginBase` subclass, or `create_panel()`) without passing it to anything or calling `deleteLater()`, `qtbot.addWidget()` or `shiboken6.delete()`. It recognises that shape, not the leak itself: Python then garbage-collects the panel's wrappers while Qt still owns the widgets, and an unrelated test fails at random.

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
| `KS140` | warning | No tests/ directory | Add a tests/ directory with a tests/unit/ suite. |
| `KS141` | warning | No tests/unit/ suite | Put fast, headless tests under tests/unit/ - that is what pre-commit runs. |
| `KS142` | warning | No SDK contract test | Subclass karcytics_sdk.testing.ContractTestBase in a test file and set PLUGIN_DIR. |
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
| `KS210` | warning | Raw Qt widget where the SDK provides one | Use the SDK component instead. |
| `KS211` | warning | Subclasses a raw Qt widget the SDK wraps | Subclass the SDK component instead. |
| `KS220` | warning | Raw Qt dialog | Use the helper from karcytics_sdk.plugin.dialogs instead. |
| `KS230` | warning | Hand-rolled Qt threading | Subclass karcytics_sdk.plugin.OneShotWorkerThread, or submit an AnalysisBase/FunctionalTask to karcytics_sdk.plugin.task_scheduler. |
| `KS231` | warning | Hand-rolled Python threading | Wrap the work in karcytics_sdk.plugin.managed_task.FunctionalTask and submit it to karcytics_sdk.plugin.task_scheduler. |
| `KS240` | warning | Hard-coded colour | Use a Colors token from karcytics_sdk.plugin.theme_fallback so the widget follows the active theme. |
| `KS241` | info | Direct setStyleSheet call | Prefer a Bio* component, apply_component_style(), or theme_manager.apply_style() so the style is re-applied when the theme changes. |
| `KS250` | warning | Standard-library logger | Use karcytics_sdk.plugin.get_logger(__name__) so records carry plugin metadata. |
| `KS251` | info | Hand-rolled JSON file I/O | Use load_json()/save_json() from karcytics_sdk.plugin. |
| `KS252` | warning | print() call | Use karcytics_sdk.plugin.get_logger(__name__) instead of print(). |
| `KS260` | warning | No PluginBase subclass | The plugin's main panel should subclass karcytics_sdk.plugin.PluginBase. |
| `KS261` | info | No AnalysisBase subclass | Put computation in a karcytics_sdk.plugin.AnalysisBase subclass under analysis/. |
| `KS270` | warning | Test builds the plugin panel without destroying it | Destroy it when the test ends: `qtbot.addWidget(panel)`, or `panel.deleteLater()` in a fixture's teardown. A panel left to the garbage collector makes unrelated tests fail intermittently. |

A Qt widget is only flagged when the SDK has a real replacement. `QCheckBox`, for example, has none and is never reported. The full mapping lives in `karcytics_sdk/evaluator/catalog.py`; add a row there when the SDK gains a component.

## Adding a rule

1. Declare it in `evaluator/registry.py` (id, severity, title, default fix).
2. Emit it with `make_finding(...)` from a check in `org_rules.py` or from `_FileChecker` in `functional_rules.py` (`test_rules.py` for a check over the repo's tests).
3. Add a test under `tests/unit/evaluator/` and a row to the table above (a test fails if a rule is missing from this page).
