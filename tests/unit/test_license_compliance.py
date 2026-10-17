"""The license gate must accept a license *choice* that includes an allowed option."""

import pytest

from karcytics_sdk.cli.commands import compliance as check


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mit", None),
        ("lgpl-3.0-only", None),
        # PySide6 / shiboken6: usable under the LGPL alternative.
        ("lgpl-3.0-only or gpl-2.0-only or gpl-3.0-only", None),
        ("(mit or agpl-3.0)", None),
        # PyQt6: GPL with no acceptable alternative.
        ("gpl-3.0-only", "gpl"),
        ("gpl-2.0-only or gpl-3.0-only", "gpl"),
        ("agpl-3.0 or sspl-1.0", "agpl"),
    ],
)
def test_license_choice(text, expected):
    assert check._disallowed_marker(text) == expected


def test_or_inside_long_prose_is_not_a_license_choice():
    prose = "this package bundles a library under the gpl, " + "or other notices apply. " * 20
    assert check._disallowed_marker(prose) == "gpl"


def test_pyqt_is_no_longer_allowlisted():
    assert not [name for name in check.NAME_ALLOWLIST if "pyqt" in name]
