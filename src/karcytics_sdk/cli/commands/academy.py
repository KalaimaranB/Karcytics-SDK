"""`karcytics-sdk academy-export`: every Academy course in a repo as JSON (see academy/export.py)."""

from karcytics_sdk.academy.export import main as export_main


def setup_academy_parser(subparsers):
    p = subparsers.add_parser(
        "academy-export", help="Read every Academy course in a repository into JSON, without running it."
    )
    p.add_argument("path", help="Repository (or its src/) to read.")
    p.add_argument("--module", default="", help="Module id to record, e.g. flow_cytometry.")
    p.add_argument("--out", help="Write the JSON here instead of stdout.")
    p.add_argument("--commit", default="", help="Commit the export was made from.")
    p.set_defaults(func=academy_export)


def academy_export(args) -> bool:
    argv = [args.path, "--module", args.module, "--commit", args.commit] + (["--out", args.out] if args.out else [])
    return export_main(argv) == 0
