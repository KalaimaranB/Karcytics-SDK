import importlib
import sys

import pytest


def test_image_utils_warns_that_it_is_deprecated():
    sys.modules.pop("karcytics_sdk.contrib.image_utils", None)
    with pytest.warns(DeprecationWarning, match="removed in karcytics-sdk 4.0"):
        module = importlib.import_module("karcytics_sdk.contrib.image_utils")
    assert callable(module.load_and_convert)  # still importable until 4.0
