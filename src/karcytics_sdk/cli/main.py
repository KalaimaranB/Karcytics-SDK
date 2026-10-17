import argparse
import sys

from karcytics_sdk.cli.commands.compliance import setup_compliance_parser
from karcytics_sdk.cli.commands.diagnostics import setup_diagnostics_parser
from karcytics_sdk.cli.commands.migrate import setup_migrate_parser
from karcytics_sdk.cli.commands.scaffold import setup_scaffold_parser
from karcytics_sdk.cli.commands.security import setup_security_parser


def main():
    """Main CLI execution entry point mapping command subparsers to CLI actions."""
    # ── Legacy Compatibility Shim ───────────────────────────────────
    args_list = sys.argv[1:]
    if args_list and args_list[0] == "sdk":
        print("⚠️  DEPRECATION WARNING: Running with 'sdk' prefix is deprecated.", file=sys.stderr)
        print("   Use 'karcytics-sdk <command>' directly instead.\\n", file=sys.stderr)
        args_list.pop(0)

    # ── Modern Subparser Definition ──────────────────────────────────
    parser = argparse.ArgumentParser(
        prog="karcytics-sdk",
        description="Software Development Kit and CLI for Karcytics desktop plugins.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="SDK Commands")

    # Delegate to submodules
    setup_scaffold_parser(subparsers)
    setup_security_parser(subparsers)
    setup_diagnostics_parser(subparsers)
    setup_migrate_parser(subparsers)
    setup_compliance_parser(subparsers)

    args = parser.parse_args(args_list)

    if hasattr(args, "func"):
        success = args.func(args)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
