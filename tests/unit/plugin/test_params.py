"""Param declarations and the ParamsForm built from them."""

from __future__ import annotations

import pytest

from karcytics_sdk.plugin.params import Param, defaults
from karcytics_sdk.plugin.params_form import ParamsForm

PARAMS = [
    Param("target", "Target", "options", source="channel"),
    Param("use_seed", "Use seed", "bool", default=False),
    Param("seed", "Seed", "options", source="channel", enabled_by="use_seed"),
    Param("diameter", "Diameter", "float", 30.0, 0, 500, unit="length"),
    Param("min_area", "Min area", "float", 100.0, 0, 1e5, unit="area"),
    Param("niter", "Iterations", "int", 0, 0, 2000, advanced=True, special_value_text="Auto"),
    Param("mode", "Mode", "choice", "fast", choices=(("Fast", "fast"), ("Careful", "careful"))),
]


class _Store:
    """The smallest PreferenceManagerProtocol: a dict that counts saves."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.saves = 0

    def load(self) -> None: ...

    def save(self) -> None:
        self.saves += 1

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def has(self, key) -> bool:
        return key in self.data

    def clear(self) -> None:
        self.data.clear()


@pytest.fixture
def form(qtbot):
    f = ParamsForm()
    qtbot.addWidget(f)
    f.set_params(PARAMS)
    f.set_options("channel", ["GFP", "DAPI"])
    return f


class TestParam:
    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="kind must be one of"):
            Param("x", "X", "channel")

    def test_options_need_a_source(self):
        with pytest.raises(ValueError, match="needs a source"):
            Param("x", "X", "options")

    def test_choice_needs_choices(self):
        with pytest.raises(ValueError, match="needs choices"):
            Param("x", "X", "choice")

    def test_defaults_are_keyed_by_parameter(self):
        assert defaults(PARAMS[3:5]) == {"diameter": 30.0, "min_area": 100.0}


class TestParamsForm:
    def test_defaults_are_shown(self, form):
        assert form.display_values() == {
            "target": "GFP",
            "use_seed": False,
            "seed": "GFP",
            "diameter": 30.0,
            "min_area": 100.0,
            "niter": 0,
            "mode": "fast",
        }

    def test_units_are_shown_as_declared_by_default(self, form):
        assert form.widget("diameter").suffix() == " length"
        assert form.values()["diameter"] == 30.0

    def test_unit_display_relabels_and_converts_back(self, form):
        scale = 2.0  # two display units per declared unit
        form.set_unit_display(lambda unit: {"length": ("µm", scale), "area": ("µm²", scale**2)}[unit])
        assert form.widget("diameter").suffix() == " µm"
        assert form.widget("min_area").suffix() == " µm²"
        values = form.values()
        assert values["diameter"] == pytest.approx(15.0)
        assert values["min_area"] == pytest.approx(25.0)

    def test_int_values_are_rounded(self, form):
        form.set_display_values({"niter": 12})
        assert form.values()["niter"] == 12

    def test_enabled_by_follows_its_switch(self, form):
        assert not form.widget("seed").isEnabled()
        form.widget("use_seed").setChecked(True)
        assert form.widget("seed").isEnabled()

    def test_advanced_parameters_sit_behind_the_toggle(self, form):
        assert not form.widget("niter").isVisibleTo(form)
        form.btn_advanced.setChecked(True)
        assert form.widget("niter").isVisibleTo(form)

    def test_options_keep_a_choice_that_survives(self, form):
        form.set_display_values({"target": "DAPI"})
        form.set_options("channel", ["DAPI", "GFP", "mCherry"])
        assert form.display_values()["target"] == "DAPI"

    def test_shared_values_carry_over_when_params_change(self, form):
        form.set_display_values({"diameter": 42.0})
        form.set_params([Param("diameter", "Diameter", "float", 30.0, 0, 500)])
        assert form.display_values() == {"diameter": 42.0}

    def test_a_value_does_not_carry_over_to_a_parameter_with_another_default(self, form):
        form.widget("diameter").setValue(42.0)
        form.set_params([Param("diameter", "Diameter", "float", 80.0, 0, 500)])
        assert form.display_values() == {"diameter": 80.0}

    def test_changed_fires_on_edit(self, form, qtbot):
        with qtbot.waitSignal(form.changed):
            form.widget("diameter").setValue(31.0)

    def test_hidden_controls_still_hold_values(self, form):
        form.set_hidden({"target"})
        assert not form.widget("target").isVisibleTo(form)
        assert form.values()["target"] == "GFP"

    def test_reset_puts_back_the_declared_defaults(self, form):
        form.set_display_values({"diameter": 99.0, "mode": "careful"})
        form.reset_to_defaults()
        assert form.display_values()["diameter"] == 30.0
        assert form.display_values()["mode"] == "fast"

    def test_my_defaults_round_trip_through_a_store(self, form, qtbot):
        store = _Store()
        form.set_display_values({"diameter": 55.0})
        form.save_defaults(store, "segmenter.otsu")
        assert store.saves == 1

        other = ParamsForm()
        qtbot.addWidget(other)
        other.set_params(PARAMS)
        assert other.load_defaults(store, "segmenter.otsu") is True
        assert other.display_values()["diameter"] == 55.0

    def test_loading_defaults_from_an_empty_store_changes_nothing(self, form):
        assert form.load_defaults(_Store(), "nothing") is False
        assert form.display_values()["diameter"] == 30.0

    def test_a_form_without_params_hides_itself(self, qtbot):
        f = ParamsForm()
        qtbot.addWidget(f)
        f.set_params([])
        assert f.isHidden()
