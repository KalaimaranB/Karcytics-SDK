# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`karcytics-sdk` — the shared package (`src/karcytics_sdk`) that both the Hub app (`Karcytics` repo) and every plugin depend on. It's the *only* thing a plugin is allowed to import from outside its own code; a compliant plugin never imports `karcytics.*` from the Hub. Versioned and released independently of the Hub (currently consumed via an editable local path from the Hub's `pyproject.toml`).

See `../Karcytics/ECOSYSTEM.md` for the full map of all 7 Karcytics repos and their relationships.

> **README caveat:** the root `README.md`'s plugin example (`KarcyticsPlugin` interface + `manifest.json` with `entrypoint`) is stale — it predates the current contract and doesn't match the code. The real contract: a plugin subclasses `karcytics_sdk.plugin.base.PluginBase` (a `QWidget`), and its `pyproject.toml` declares `[tool.karcytics.plugin] entry_point = "module:function"` returning a `PluginBase` instance. See the Hub repo's `docs/internal/15_ModuleManager_and_PluginContract.md` and `16_PluginBase_and_SDK_Contract.md` for the verified, current version — trust those over this repo's own README/docs if they disagree.

## Commands

```bash
uv run pytest tests/ -q                          # full suite (coverage gate: --cov-fail-under=20)
uv run pytest tests/unit/plugin/test_x.py -q     # single test file
uv run ruff check src tests --fix
uv run ruff format src tests
uv run mypy src
```

Pre-commit runs ruff (lint + format), mypy, and license compliance automatically — don't pre-run the full suite before committing, just the tests for what you touched. Tests use a single persistent headless `QApplication` fixture (`tests/conftest.py`); widgets are never `.show()`n.

## Structure

- `src/karcytics_sdk/plugin/` — author-facing: `base.py` (`PluginBase`), `analysis.py` (`AnalysisBase`/`AnalysisRunnable`/`AnalysisWorker` — pure, UI-free compute), `state.py` (`PluginState` dataclasses, deep-copied into the engine to keep it isolated from live UI edits), `events.py` (`CentralEventBus`), `rendering/` (Matplotlib canvas + render pipeline).
- `src/karcytics_sdk/host/` — Hub-facing: `core_services.py`, `trust_manager.py`/`trust_path.py`/`trust_storage.py`/`trust_overrides.py` (Ed25519 signature verification), `module_status_widget.py`, `marketplace_cache.py`.
- `src/karcytics_sdk/interfaces/` — Protocols implemented by the Hub and consumed by plugins: `ITaskScheduler`, `IEventBus`, `ILogger`, `ICrashReporter`.
- `src/karcytics_sdk/cli/commands/` — the `karcytics-sdk` CLI: `scaffold` (new plugin), `security` (signing/identity), `migrate`, `diagnostics`.
- `src/karcytics_sdk/testing/` — test helpers exported for *plugin repos* to use in their own test suites.

## Key architectural pattern

Every plugin panel splits into a `PluginBase` (QWidget, all UI/signals) and an `AnalysisBase` subclass (pure compute, no Qt imports) — the engine takes a frozen `PluginState` snapshot rather than reading live widget state, so it's thread-safe and independently unit-testable/headless-runnable. Don't put computation directly in a `PluginBase` method; put it in the paired `AnalysisBase`.

`theme_fallback.py` exists because `karcytics.ui.theme` (the Hub's real theme engine) is only importable when a plugin runs in-process inside the Hub; an isolated plugin process falls back to it. Code in `plugin/` that touches theming should go through `Colors`/`theme_manager` re-exported at the top of the relevant module (see the `try/except ImportError` pattern in `plugin/base.py`), not import `karcytics.ui.theme` directly.

See `docs/Architectural_Design.md` for the fuller rationale (decoupled UI/engine boundary, headless Qt testing setup, RAII cleanup for worker threads).
