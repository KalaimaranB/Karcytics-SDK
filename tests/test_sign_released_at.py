"""`sign` stamps a release date into pyproject.toml, and the signature covers it.

The Hub compares this date with a license's update period, so it must be present on every signed
release and impossible to change without breaking verification.
"""

import datetime
import json
import subprocess
import tomllib
from argparse import Namespace

import pytest

from karcytics_sdk.cli.commands.security import sign_plugin as cli_sign
from karcytics_sdk.host.sign_plugin import PluginSigner, stamp_released_at
from karcytics_sdk.plugin.manifest_parser import ManifestParser
from karcytics_sdk.plugin.security_parser import ManifestHashMismatch, SecurityParser

MANIFEST = (
    '[project]\nname = "MyPlugin"\nversion = "1.0.0"\ndescription = "d"\n\n'
    '[tool.karcytics.plugin]\nid = "my_plugin"\nentry_point = "m:f"\n'
    'authors = [{name = "Test", role = "Developer"}]\n\n'
    '[tool.other]\nreleased_at = "keep-me"\n'
)


def _plugin_table(path):
    return tomllib.loads(path.read_text())["tool"]["karcytics"]["plugin"]


def test_stamp_inserts_into_the_plugin_table_only(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text(MANIFEST)
    stamp_released_at(m, "2026-12-01")
    data = tomllib.loads(m.read_text())
    assert data["tool"]["karcytics"]["plugin"]["released_at"] == "2026-12-01"
    assert data["tool"]["other"]["released_at"] == "keep-me"
    assert data["project"]["version"] == "1.0.0"


def test_stamp_replaces_an_existing_date(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text(MANIFEST)
    stamp_released_at(m, "2026-12-01")
    stamp_released_at(m, "2027-01-15")
    assert _plugin_table(m)["released_at"] == "2027-01-15"
    assert m.read_text().count('released_at = "2027-01-15"') == 1


def test_stamp_keeps_windows_line_endings(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_bytes(MANIFEST.replace("\n", "\r\n").encode())
    stamp_released_at(m, "2026-12-01")
    raw = m.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert _plugin_table(m)["released_at"] == "2026-12-01"


@pytest.mark.parametrize("bad", ["2026-13-01", "2026-02-30", "26-12-01", "today", ""])
def test_stamp_rejects_bad_dates(tmp_path, bad):
    m = tmp_path / "pyproject.toml"
    m.write_text(MANIFEST)
    with pytest.raises(ValueError):
        stamp_released_at(m, bad)
    assert m.read_text() == MANIFEST


def test_stamp_needs_a_plugin_table(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text('[project]\nname = "x"\n')
    with pytest.raises(ValueError, match="tool.karcytics.plugin"):
        stamp_released_at(m, "2026-12-01")


def _signed_plugin(tmp_path, released_at=None):
    signer = PluginSigner()
    signer.dev_dir = tmp_path / "dev"
    signer.private_key_path = signer.dev_dir / "private.key"
    signer.public_key_path = signer.dev_dir / "public.pub"
    signer.delegation_path = signer.dev_dir / "delegation.json"
    signer.init_identity()
    plugin = tmp_path / "my_plugin"
    plugin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=plugin, check=True)
    (plugin / "pyproject.toml").write_text(MANIFEST)
    (plugin / "main.py").write_text("print(1)\n")
    subprocess.run(["git", "add", "."], cwd=plugin, check=True)
    signer.sign_plugin(plugin, released_at=released_at)
    return plugin


def test_sign_stamps_today_by_default_and_parser_exposes_it(tmp_path):
    plugin = _signed_plugin(tmp_path)
    manifest = ManifestParser().parse_file(plugin / "pyproject.toml")
    assert manifest["released_at"] == datetime.date.today().isoformat()


def test_sign_uses_an_explicit_date(tmp_path):
    plugin = _signed_plugin(tmp_path, released_at="2026-12-24")
    assert ManifestParser().parse_file(plugin / "pyproject.toml")["released_at"] == "2026-12-24"


def test_signature_covers_the_release_date(tmp_path):
    plugin = _signed_plugin(tmp_path, released_at="2026-12-24")
    parser = SecurityParser()
    security = parser.parse(json.loads((plugin / "security.json").read_text()))
    parser.verify_manifest_binding(plugin / "pyproject.toml", security)  # intact: passes

    # Moving the date back to sneak a late release into an expired license must break the binding.
    stamp_released_at(plugin / "pyproject.toml", "2026-01-01")
    with pytest.raises(ManifestHashMismatch):
        parser.verify_manifest_binding(plugin / "pyproject.toml", security)


def test_cli_passes_released_at(tmp_path, monkeypatch):
    seen = {}

    def fake_sign(self, path, released_at=None):
        seen["released_at"] = released_at

    monkeypatch.setattr(PluginSigner, "sign_plugin", fake_sign)
    assert cli_sign(Namespace(plugin_dir=str(tmp_path), released_at="2027-02-02"))
    assert seen["released_at"] == "2027-02-02"
