"""Plugin self-update checking for Karcytics SDK.

A plugin runs in its own process (isolated) or the Hub's (in-process) either
way, and either way it's the one thing that actually knows its own repo and
its own installed version — the Hub's marketplace machinery
(``karcytics_sdk.host.marketplace_cache``, ``karcytics.core.network.*``)
exists to drive the Store UI and is a Hub-only concern a plugin never
imports. This module is the plugin-side counterpart: a small, dependency-free
check a plugin can opt into (see ``PluginBase.check_for_updates``) to find
out whether a newer release exists, so it can tell the user via a toast
(``karcytics_sdk.plugin.toast``) to close the module and grab it from the
Store.

No Qt imports — pure logic, safe to unit test and safe to call from a
background thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of a `check_for_plugin_update` call.

    `status` is one of:
        "update_available" — a newer release exists; `remote_version` is set.
        "up_to_date"        — the installed version is already the newest
                               one published; `remote_version` is set (and
                               equals `current_version`).
        "check_failed"      — the check itself couldn't be completed
                               (network error, bad response, unparseable
                               version, ...). Callers must treat this as
                               "unknown", never silently as "up to date".
    """

    status: str
    remote_version: str | None = None


# Same raw-content template karcytics.core.network.plugin_registry_fetcher.py
# uses to resolve a plugin's own pyproject.toml from its GitHub repo.
_RAW_PYPROJECT_TEMPLATE = "https://raw.githubusercontent.com/{owner}/{repo}/main/pyproject.toml"

_OWNER_REPO_PART_COUNT = 2

# How far to walk up from a plugin's own module file looking for its
# pyproject.toml — generously bounded so a package nested unusually deep
# still resolves, without ever risking an unbounded walk to filesystem root.
_PYPROJECT_SEARCH_DEPTH = 8


def resolve_installed_version(plugin_id: str, instance: object) -> str | None:
    """Resolve the version of the plugin `instance` belongs to.

    Tries `importlib.metadata.version(plugin_id)` first — correct once a
    plugin is actually installed as its own distribution. Falls back to
    walking up from `instance`'s own module file to find a `pyproject.toml`
    and reading `[project].version` directly, because that's how a plugin
    actually reaches its process today: every isolated plugin here is loaded
    via a `sys.path` insert into its dev checkout (see e.g. `PluginDaemon`),
    never a real `pip install` — so `importlib.metadata` has no entry for it
    and silently raises `PackageNotFoundError`. Mirrors the same fallback
    Flow Cytometry's own `__init__.py:_read_version()` already needed for
    exactly this reason.

    Returns `None` if neither approach resolves anything — callers should
    treat that as "can't check for updates", not as a version of `"0.0.0"`
    or similar that would make every real release look newer.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        return _pkg_version(plugin_id)
    except PackageNotFoundError:
        pass
    except Exception:
        logger.debug("resolve_installed_version: importlib.metadata lookup failed", exc_info=True)

    try:
        import sys
        from pathlib import Path

        module = sys.modules.get(type(instance).__module__)
        module_file = getattr(module, "__file__", None)
        if not module_file:
            return None

        current = Path(module_file).resolve().parent
        for _ in range(_PYPROJECT_SEARCH_DEPTH):
            candidate = current / "pyproject.toml"
            if candidate.exists():
                import tomllib

                with candidate.open("rb") as f:
                    data = tomllib.load(f)
                return data.get("project", {}).get("version")
            if current.parent == current:  # reached filesystem root
                break
            current = current.parent
    except Exception:
        logger.debug("resolve_installed_version: pyproject.toml walk-up failed", exc_info=True)

    return None


def _parse_owner_repo(repo_url: str) -> tuple[str, str] | None:
    """Split a GitHub repository URL into ``(owner, repo)``, or ``None`` if unparseable."""
    try:
        path = repo_url.rstrip("/").split("github.com/", 1)[-1]
        parts = path.strip("/").split("/")
        if len(parts) < _OWNER_REPO_PART_COUNT:
            return None
        return parts[0], parts[1]
    except Exception:
        return None


def _parse_version(v_str: str) -> tuple[int, ...]:
    """Parse a dotted version string into a tuple of leading numeric components.

    Stops at the first non-numeric component (e.g. a pre-release suffix), and
    returns ``()`` for anything unparseable — callers treat that as "unknown",
    never as "older" or "newer".
    """
    if not v_str or not isinstance(v_str, str):
        return ()
    clean = v_str.split("-")[0].split("+")[0]
    parts: list[int] = []
    for p in clean.split("."):
        if p.isdigit():
            parts.append(int(p))
        else:
            break
    return tuple(parts)


def _compare_versions(a: str, b: str) -> int | None:
    """Compare two dotted version strings component-wise.

    Returns ``1`` if ``a > b``, ``-1`` if ``a < b``, ``0`` if equal, or
    ``None`` if either side is unparseable — callers must treat that as
    "unknown", never as "equal".
    """
    va = _parse_version(a)
    vb = _parse_version(b)
    if not va or not vb:
        return None
    # Compare component-wise with unequal lengths treated as zero-padded.
    length = max(len(va), len(vb))
    va = va + (0,) * (length - len(va))
    vb = vb + (0,) * (length - len(vb))
    if va > vb:
        return 1
    if va < vb:
        return -1
    return 0


def check_for_plugin_update(current_version: str, repo_url: str, timeout: float = 5.0) -> UpdateCheckResult:
    """Check whether a newer release of this plugin has been published.

    Fetches ``repo_url``'s ``pyproject.toml`` straight off its GitHub
    ``main`` branch and compares ``[project].version`` against
    ``current_version``. Best-effort: any network error, non-200 response, or
    unparseable version comes back as ``UpdateCheckResult(status="check_failed")``
    and is logged at debug level rather than raised — a broken connectivity
    check must never be allowed to break plugin startup.

    Parameters:
        current_version (str): The version currently installed, e.g. ``"0.8.6.5"``.
        repo_url (str): The plugin's GitHub repository URL (its manifest's ``homepage``).
        timeout (float): Network timeout in seconds.

    Returns:
        UpdateCheckResult: See its docstring for the three possible outcomes.
    """
    owner_repo = _parse_owner_repo(repo_url)
    if not owner_repo:
        logger.debug("check_for_plugin_update: cannot parse owner/repo from %s", repo_url)
        return UpdateCheckResult(status="check_failed")
    owner, repo = owner_repo
    manifest_url = _RAW_PYPROJECT_TEMPLATE.format(owner=owner, repo=repo)

    try:
        response = requests.get(manifest_url, timeout=timeout)
        response.raise_for_status()

        import tomllib

        data = tomllib.loads(response.text)
        remote_version = data.get("project", {}).get("version")
        if not remote_version:
            logger.debug("check_for_plugin_update: no [project].version in %s", manifest_url)
            return UpdateCheckResult(status="check_failed")

        comparison = _compare_versions(remote_version, current_version)
        if comparison is None:
            logger.debug(
                "check_for_plugin_update: unparseable version(s): remote=%s current=%s",
                remote_version,
                current_version,
            )
            return UpdateCheckResult(status="check_failed")

        if comparison > 0:
            return UpdateCheckResult(status="update_available", remote_version=remote_version)
        return UpdateCheckResult(status="up_to_date", remote_version=remote_version)
    except Exception:
        logger.debug("check_for_plugin_update: failed to check %s", manifest_url, exc_info=True)
        return UpdateCheckResult(status="check_failed")
