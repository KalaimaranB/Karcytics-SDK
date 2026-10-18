from karcytics_sdk.evaluator.baseline import BASELINE_FILE


def setup_diagnostics_parser(subparsers):
    # Command: sbom
    sbom_parser = subparsers.add_parser("sbom", help="Generates and prints the SBOM in the specified format.")
    sbom_parser.add_argument(
        "--format", type=str, choices=["--json", "--markdown"], default="--markdown", help="Output format"
    )
    sbom_parser.set_defaults(func=generate_sbom)

    # Command: evaluate
    eval_parser = subparsers.add_parser("evaluate", help="Perform comprehensive CLI evaluation of a plugin.")
    eval_parser.add_argument("plugin_dir", type=str, help="Path to the plugin directory.")
    eval_parser.set_defaults(func=evaluate_plugin)

    # Command: doctor
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Statically check a repo against SDK conventions (structure and SDK usage).",
    )
    doctor_parser.add_argument("plugin_dir", type=str, nargs="?", default=".", help="Path to the repo to check.")
    doctor_parser.add_argument(
        "--mode",
        choices=["org", "functional", "design", "all"],
        default="all",
        help="org: structure/CI/tooling. functional: SDK usage in the source. "
        "design: structure, duplication and layering. Default: all.",
    )
    doctor_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    doctor_parser.add_argument("--show-info", action="store_true", help="Also show info-level findings.")
    doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors.")
    doctor_parser.add_argument("--select", default="", help="Comma-separated rule-id prefixes to report (e.g. KS2).")
    doctor_parser.add_argument("--ignore", default="", help="Comma-separated rule-id prefixes to skip.")
    doctor_parser.add_argument("--no-color", action="store_true", help="Disable coloured output.")
    doctor_parser.add_argument(
        "--files",
        nargs="+",
        metavar="PATH",
        help="Only check these files (fast; skips the checks that need the whole repo).",
    )
    doctor_parser.add_argument(
        "--baseline",
        nargs="?",
        const=BASELINE_FILE,
        metavar="PATH",
        help=f"Fail only on what is over this baseline (default path: {BASELINE_FILE} in the repo).",
    )
    doctor_parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Lower the baseline to what is found now (never raises it); creates it if missing.",
    )
    doctor_parser.add_argument(
        "--reset-baseline",
        action="store_true",
        help="Rewrite the baseline from what is found now, accepting any new debt.",
    )
    doctor_parser.add_argument(
        "--claude-hook",
        action="store_true",
        help="Run as a Claude Code PostToolUse hook: check the edited file named in the hook "
        "payload on stdin and exit 2 with the findings on stderr when there is something new.",
    )
    doctor_parser.set_defaults(func=doctor_plugin)


def generate_sbom(args) -> bool:
    """Generates and prints the SBOM in the specified format."""
    try:
        import importlib

        karcytics_core_sbom = importlib.import_module("karcytics.core.sbom")
        generator = karcytics_core_sbom.SBOMGenerator()

        if args.format == "--json":
            print(generator.to_json())
        else:
            print(generator.to_markdown())
        return True
    except ImportError:
        print("ERROR: SBOM generation is only supported when running inside the Karcytics main application.")
        return False


def evaluate_plugin(args) -> bool:
    """Perform comprehensive CLI evaluation of a plugin."""
    import logging
    import sys
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from karcytics_sdk.plugin.manifest_parser import ManifestParser

    print("Running Plugin Diagnostics Evaluator (evaluate)...")
    _app = QApplication.instance() or QApplication(sys.argv)
    logging.basicConfig(level=logging.INFO)

    try:
        parser = ManifestParser()
        manifest = parser.parse_file(str(Path(args.plugin_dir) / "pyproject.toml"))
        print(f"Manifest parsed successfully: {manifest.get('id')} v{manifest.get('version')}")

        dependencies = manifest.get("python_dependencies") or manifest.get("dependencies", {})
        if dependencies:
            print("Auditing Plugin Dependencies...")
            all_pinned = True
            for dep, version in dependencies.items():
                if any(c in version for c in (">", "<", "*", "^", "~")):
                    print(f"WARNING: Dependency '{dep}' is not pinned. Recommend exact pinning.")
                    all_pinned = False
                else:
                    print(f"Dependency '{dep}' is pinned to version '{version}'")

            if all_pinned:
                print("All declared dependencies are securely pinned.")

        return True
    except Exception as e:
        print(f"Evaluation failed: {e}")
        return False


def _hook_files(stdin_text: str) -> list[str]:
    """The Python file a Claude Code PostToolUse payload says was written."""
    import json

    try:
        payload = json.loads(stdin_text or "{}")
    except json.JSONDecodeError:
        return []
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    return [path] if isinstance(path, str) and path.endswith(".py") else []


def _load_baseline(root, baseline_arg: str | None, writing: bool):
    """(path, recorded counts) for `--baseline`; raises FileNotFoundError when it must exist."""
    from karcytics_sdk.evaluator import baseline as baseline_mod

    if baseline_arg is None:
        return None, None
    path = root / baseline_arg
    if path.is_file():
        return path, baseline_mod.load(path)
    if writing:
        return path, None
    raise FileNotFoundError(f"baseline '{path}' not found (create it with --update-baseline)")


def _write_baseline(path, report, recorded, reset: bool) -> None:
    """Tighten the recorded baseline to `report`, or rewrite it when `reset` (or missing)."""
    from karcytics_sdk.evaluator import baseline as baseline_mod

    current = baseline_mod.measure(report.findings)
    if reset or recorded is None:
        # A scoped run only rewrites the files it looked at.
        kept = {p: v for p, v in (recorded or {}).items() if report.scope is not None and p not in report.scope}
        baseline_mod.save(path, {**kept, **current})
    else:
        baseline_mod.save(path, baseline_mod.tighten(recorded, current, report.scope))
    print(f"Baseline written to {path}.")


def _fail_hook(report) -> None:
    import sys

    from karcytics_sdk.evaluator import render_text

    print(
        "karcytics-sdk doctor: this edit added problems. Fix them rather than suppressing them "
        "(a suppression needs a written reason; see docs/Doctor.md).\n",
        file=sys.stderr,
    )
    print(render_text(report, color=False), file=sys.stderr)
    sys.exit(2)


def _baseline_arg(args, root, hook: bool) -> str | None:
    """The baseline path to use: as given, implied by writing one, or the repo's own in a hook."""
    baseline_arg = getattr(args, "baseline", None)
    if baseline_arg is None and (getattr(args, "update_baseline", False) or getattr(args, "reset_baseline", False)):
        return BASELINE_FILE
    if baseline_arg is None and hook and (root / BASELINE_FILE).is_file():
        return BASELINE_FILE
    return baseline_arg


def doctor_plugin(args) -> bool:
    """Statically evaluate a repo against SDK conventions. Returns False if the run fails."""
    import os
    import sys
    from pathlib import Path

    from karcytics_sdk.evaluator import Mode, evaluate_repo, render_json, render_text

    root = Path(args.plugin_dir)
    if not root.is_dir():
        print(f"ERROR: '{args.plugin_dir}' is not a directory.", file=sys.stderr)
        return False

    # Read with defaults: the legacy SDKCLI shim only passes plugin_dir.
    hook = getattr(args, "claude_hook", False)
    # A hook payload naming no Python file leaves nothing to check: an empty run passes.
    files = _hook_files(sys.stdin.read()) if hook else getattr(args, "files", None)
    select, ignore = getattr(args, "select", ""), getattr(args, "ignore", "")
    update, reset = getattr(args, "update_baseline", False), getattr(args, "reset_baseline", False)
    if (update or reset) and (select or ignore):
        print("ERROR: --update-baseline/--reset-baseline cover every rule; drop --select/--ignore.", file=sys.stderr)
        return False
    try:
        baseline_path, recorded = _load_baseline(root, _baseline_arg(args, root, hook), writing=update or reset)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}.", file=sys.stderr)
        return False

    mode = getattr(args, "mode", "all")
    modes = (Mode.ORG, Mode.FUNCTIONAL, Mode.DESIGN) if mode == "all" else (Mode(mode),)
    report = evaluate_repo(
        root, modes=modes, select=select.split(","), ignore=ignore.split(","), files=files, baseline=recorded
    )

    if baseline_path is not None and (update or reset):
        _write_baseline(baseline_path, report, recorded, reset)
        return True
    if hook:
        if not report.passed(strict=True):
            _fail_hook(report)
        return True

    if getattr(args, "format", "text") == "json":
        print(render_json(report))
    else:
        color = not getattr(args, "no_color", False) and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        print(render_text(report, show_info=getattr(args, "show_info", False), color=color))
    return report.passed(strict=getattr(args, "strict", False))
