"""Unit tests for karcytics_sdk.plugin.update_check.

Pure logic, no Qt — mocks requests.get so these never touch the network.
"""

from __future__ import annotations

import sys
import types
from importlib.metadata import PackageNotFoundError
from unittest.mock import MagicMock, patch

from karcytics_sdk.plugin.update_check import (
    UpdateCheckResult,
    _compare_versions,
    _parse_owner_repo,
    _parse_version,
    check_for_plugin_update,
    resolve_installed_version,
)

REPO_URL = "https://github.com/KalaimaranB/Karcytics-flow-cytometry"


def _fake_module_instance(module_name: str, module_file):
    """Build an object whose defining class reports `module_name` as its
    `__module__` and is registered in `sys.modules` pointing at `module_file`
    — the same shape `resolve_installed_version` inspects on a real plugin
    instance (`type(instance).__module__` -> `sys.modules[...].__file__`).
    """
    fake_module = types.ModuleType(module_name)
    fake_module.__file__ = str(module_file)

    instance_cls = type("FakeInstance", (), {})
    instance_cls.__module__ = module_name
    return instance_cls(), fake_module


def _mock_response(pyproject_text: str, status_ok: bool = True) -> MagicMock:
    response = MagicMock()
    response.text = pyproject_text
    if status_ok:
        response.raise_for_status.return_value = None
    else:
        response.raise_for_status.side_effect = Exception("HTTP error")
    return response


def _pyproject(version: str) -> str:
    return f'[project]\nname = "flow_cytometry"\nversion = "{version}"\n'


class TestParseOwnerRepo:
    def test_parses_a_standard_github_url(self):
        assert _parse_owner_repo(REPO_URL) == ("KalaimaranB", "Karcytics-flow-cytometry")

    def test_tolerates_a_trailing_slash(self):
        assert _parse_owner_repo(REPO_URL + "/") == ("KalaimaranB", "Karcytics-flow-cytometry")

    def test_returns_none_for_an_unparseable_url(self):
        assert _parse_owner_repo("not a url") is None
        assert _parse_owner_repo("") is None


class TestParseVersion:
    def test_parses_a_standard_semver(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_stops_at_a_prerelease_suffix(self):
        assert _parse_version("1.2.3-beta") == (1, 2, 3)

    def test_handles_more_than_three_components(self):
        assert _parse_version("0.8.6.5") == (0, 8, 6, 5)

    def test_returns_empty_tuple_for_unparseable_input(self):
        assert _parse_version("not-a-version") == ()
        assert _parse_version("") == ()
        assert _parse_version(None) == ()  # type: ignore[arg-type]


class TestCompareVersions:
    def test_positive_when_a_is_newer(self):
        assert _compare_versions("1.1.0", "1.0.0") == 1

    def test_zero_when_versions_are_equal(self):
        assert _compare_versions("1.0.0", "1.0.0") == 0

    def test_negative_when_a_is_older(self):
        assert _compare_versions("0.9.0", "1.0.0") == -1

    def test_handles_unequal_component_counts(self):
        assert _compare_versions("0.8.7", "0.8.6.5") == 1
        assert _compare_versions("0.8.6.5", "0.8.7") == -1

    def test_none_when_either_version_is_unparseable(self):
        assert _compare_versions("garbage", "1.0.0") is None
        assert _compare_versions("1.0.0", "garbage") is None


class TestCheckForPluginUpdate:
    def test_returns_update_available_with_remote_version_when_newer(self):
        with patch("requests.get", return_value=_mock_response(_pyproject("0.9.0"))):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="update_available", remote_version="0.9.0")

    def test_returns_up_to_date_when_versions_match(self):
        with patch("requests.get", return_value=_mock_response(_pyproject("0.8.6.5"))):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="up_to_date", remote_version="0.8.6.5")

    def test_returns_up_to_date_when_installed_version_is_newer_than_remote(self):
        # A user ahead of the published release (e.g. testing a pre-release
        # build) is still "up to date" — there is nothing newer to offer them.
        with patch("requests.get", return_value=_mock_response(_pyproject("0.1.0"))):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="up_to_date", remote_version="0.1.0")

    def test_returns_check_failed_on_network_error(self):
        with patch("requests.get", side_effect=ConnectionError("no network")):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="check_failed")

    def test_returns_check_failed_on_non_200_response(self):
        with patch("requests.get", return_value=_mock_response("", status_ok=False)):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="check_failed")

    def test_returns_check_failed_when_pyproject_has_no_version(self):
        with patch("requests.get", return_value=_mock_response('[project]\nname = "x"\n')):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="check_failed")

    def test_returns_check_failed_when_pyproject_is_malformed_toml(self):
        with patch("requests.get", return_value=_mock_response("not valid toml {{{")):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="check_failed")

    def test_returns_check_failed_for_an_unparseable_repo_url(self):
        result = check_for_plugin_update("0.8.6.5", "not a url")
        assert result == UpdateCheckResult(status="check_failed")

    def test_returns_check_failed_when_remote_version_is_unparseable(self):
        with patch("requests.get", return_value=_mock_response(_pyproject("not-a-version"))):
            result = check_for_plugin_update("0.8.6.5", REPO_URL)
        assert result == UpdateCheckResult(status="check_failed")


class TestResolveInstalledVersion:
    def test_prefers_importlib_metadata_when_the_distribution_is_actually_installed(self):
        instance = object()
        with patch("importlib.metadata.version", return_value="2.0.0"):
            result = resolve_installed_version("some_plugin", instance)
        assert result == "2.0.0"

    def test_falls_back_to_walking_up_to_pyproject_toml_when_not_pip_installed(self, tmp_path):
        """The exact real-world case this exists for: Flow Cytometry (and
        every isolated plugin in this codebase) is loaded via a sys.path
        insert into its dev checkout, never a real `pip install` — so
        importlib.metadata has no entry for it and this fallback is what
        actually resolves the version.
        """
        repo_root = tmp_path / "some_plugin_repo"
        module_dir = repo_root / "src" / "some_plugin" / "ui"
        module_dir.mkdir(parents=True)
        (repo_root / "pyproject.toml").write_text('[project]\nname = "some_plugin"\nversion = "3.1.4"\n')
        module_file = module_dir / "panel.py"
        module_file.write_text("")

        instance, fake_module = _fake_module_instance("some_plugin.ui.panel", module_file)

        with patch("importlib.metadata.version", side_effect=PackageNotFoundError()):
            with patch.dict(sys.modules, {"some_plugin.ui.panel": fake_module}):
                result = resolve_installed_version("some_plugin", instance)

        assert result == "3.1.4"

    def test_returns_none_when_neither_approach_resolves_anything(self, tmp_path):
        module_dir = tmp_path / "orphan"
        module_dir.mkdir()
        module_file = module_dir / "panel.py"
        module_file.write_text("")

        instance, fake_module = _fake_module_instance("orphan.panel", module_file)

        with patch("importlib.metadata.version", side_effect=PackageNotFoundError()):
            with patch.dict(sys.modules, {"orphan.panel": fake_module}):
                result = resolve_installed_version("orphan_plugin", instance)

        assert result is None

    def test_returns_none_when_the_modules_file_cannot_be_determined(self):
        instance, fake_module = _fake_module_instance("no_file_module", "/does/not/matter")
        del fake_module.__file__

        with patch("importlib.metadata.version", side_effect=PackageNotFoundError()):
            with patch.dict(sys.modules, {"no_file_module": fake_module}):
                result = resolve_installed_version("no_file_plugin", instance)

        assert result is None
