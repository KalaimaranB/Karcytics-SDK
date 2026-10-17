"""read_plugin_version: metadata first, then the plugin's own pyproject.toml."""

from __future__ import annotations

from karcytics_sdk.plugin.version import read_plugin_version


def _plugin_tree(root, name="demo", version='"1.4.2"'):
    pkg = root / "src" / "karcytics_plugins" / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = {version}\n')
    return pkg / "__init__.py"


def test_reads_the_source_trees_pyproject(tmp_path):
    init = _plugin_tree(tmp_path)

    assert read_plugin_version(init, "demo") == "1.4.2"


def test_ignores_a_pyproject_for_another_project(tmp_path):
    init = _plugin_tree(tmp_path / "inner", name="other")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "9.9.9"\n')

    assert read_plugin_version(init, "demo") == "9.9.9"


def test_unknown_when_nothing_matches(tmp_path):
    init = _plugin_tree(tmp_path, name="other")

    assert read_plugin_version(init, "demo") == "0.0.0"


def test_installed_metadata_wins(tmp_path):
    init = _plugin_tree(tmp_path, name="karcytics-sdk", version='"0.0.1"')

    assert read_plugin_version(init, "karcytics-sdk") != "0.0.1"
