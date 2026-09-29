Each file wires one `karcytics-sdk <subcommand>` group via `argparse` subparsers (`setup_*_parser(subparsers)` + `func=` handlers), registered from `cli/main.py`. New commands follow this same shape: a `setup_x_parser` function and one handler per subcommand, not a class.

- `scaffold.py` — `create-manifest`, `bootstrap` (new plugin skeleton).
- `security.py` — `init-identity`, `sign` (wraps `host/sign_plugin.py::PluginSigner`).
- `migrate.py` — `migrate` (legacy plugin → current `pyproject.toml` + `src/` layout).
- `diagnostics.py` — `sbom`, `evaluate`, `doctor` (validates a plugin against current SDK architecture).
