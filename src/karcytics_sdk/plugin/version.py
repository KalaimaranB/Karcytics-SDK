"""A plugin's version, read from its pyproject.toml.

``[project].version`` is a plugin's single source of truth: it is also its
release trigger. A hard-coded ``__version__`` copy drifts (cytometrics' read
0.2.2 while pyproject said 0.3.2; western-blot hard-codes one too).

    __version__ = read_plugin_version(__file__, "cytometrics")
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_MAX_LEVELS_UP = 5  # src/karcytics_plugins/<pkg>/__init__.py -> repo root is 3 up
_UNKNOWN = "0.0.0"


def read_plugin_version(package_file: str | Path, distribution: str) -> str:
    """The version of `distribution`: installed metadata, else the nearest pyproject.toml.

    Plugins are usually loaded from a source tree via ``sys.path`` rather
    than pip-installed, so metadata is often missing; then the first
    ``pyproject.toml`` above `package_file` whose ``[project].name`` is
    `distribution` is read. Returns ``"0.0.0"`` if neither is found.
    """
    try:
        return version(distribution)
    except PackageNotFoundError:
        pass
    for parent in list(Path(package_file).resolve().parents)[:_MAX_LEVELS_UP]:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if project.get("name") == distribution and project.get("version"):
            return str(project["version"])
    return _UNKNOWN
