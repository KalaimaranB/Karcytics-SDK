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
        choices=["org", "functional", "all"],
        default="all",
        help="org: structure/CI/tooling. functional: SDK usage in the source. Default: all.",
    )
    doctor_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    doctor_parser.add_argument("--show-info", action="store_true", help="Also show info-level findings.")
    doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors.")
    doctor_parser.add_argument("--select", default="", help="Comma-separated rule-id prefixes to report (e.g. KS2).")
    doctor_parser.add_argument("--ignore", default="", help="Comma-separated rule-id prefixes to skip.")
    doctor_parser.add_argument("--no-color", action="store_true", help="Disable coloured output.")
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

    from PyQt6.QtWidgets import QApplication

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


def doctor_plugin(args) -> bool:
    """Statically evaluate a repo against SDK conventions. Returns False if it has errors."""
    import os
    import sys
    from pathlib import Path

    from karcytics_sdk.evaluator import Mode, evaluate_repo, render_json, render_text

    root = Path(args.plugin_dir)
    if not root.is_dir():
        print(f"ERROR: '{args.plugin_dir}' is not a directory.", file=sys.stderr)
        return False

    # Read with defaults: the legacy SDKCLI shim only passes plugin_dir.
    mode = getattr(args, "mode", "all")
    modes = (Mode.ORG, Mode.FUNCTIONAL) if mode == "all" else (Mode(mode),)
    report = evaluate_repo(
        root,
        modes=modes,
        select=getattr(args, "select", "").split(","),
        ignore=getattr(args, "ignore", "").split(","),
    )

    if getattr(args, "format", "text") == "json":
        print(render_json(report))
    else:
        color = not getattr(args, "no_color", False) and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        print(render_text(report, show_info=getattr(args, "show_info", False), color=color))
    return report.passed(strict=getattr(args, "strict", False))
