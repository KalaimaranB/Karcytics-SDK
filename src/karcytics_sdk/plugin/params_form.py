"""Controls generated from a list of :class:`~karcytics_sdk.plugin.params.Param`.

Basic parameters are shown; advanced ones sit behind an "Advanced settings"
toggle. Values the user typed are remembered by key, so swapping one
algorithm's parameters for another's keeps the shared ones. A form can
show units differently from how the algorithm takes them (µm on screen,
pixels to the algorithm) through ``set_unit_display``, and can keep a
user's preferred values as "my defaults" in any preference store.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from .components import (
    BioComboBox,
    BioDoubleSpinBox,
    BioHelpButton,
    BioToggleButton,
    _connect_theme_signal,
    _get_theme_tokens,
)
from .params import Param
from .preferences import PreferenceManagerProtocol

_ADVANCED_CLOSED = "Advanced settings ▸"
_ADVANCED_OPEN = "Advanced settings ▾"

#: Maps a parameter's declared unit to (suffix shown, display units per declared unit).
UnitDisplay = Callable[[str], tuple[str, float]]


def _unit_as_declared(unit: str) -> tuple[str, float]:
    return unit, 1.0


class ParamsForm(QWidget):
    """A form for a list of parameters; ``changed`` fires on every edit."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._params: list[Param] = []
        self._widgets: dict[str, QWidget] = {}
        self._labels: dict[str, QLabel] = {}
        self._helps: dict[str, BioHelpButton] = {}
        self._memory: dict[str, Any] = {}
        # The default of the parameter each remembered value was set on: a value only
        # carries over to a parameter with the same default (another algorithm's "Max
        # Area" of 1e6 is not this one's 5000). Values set explicitly carry over anywhere.
        self._memory_defaults: dict[str, Any] = {}
        self._hidden: set[str] = set()
        self._options: dict[str, list[str]] = {}
        self._unit_display: UnitDisplay = _unit_as_declared

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._basic = QWidget()
        self._basic_grid = _grid(self._basic)
        layout.addWidget(self._basic)

        self.btn_advanced = BioToggleButton(_ADVANCED_CLOSED)
        self.btn_advanced.toggled.connect(self._on_advanced_toggled)
        layout.addWidget(self.btn_advanced)
        self._advanced = QWidget()
        self._advanced_grid = _grid(self._advanced)
        self._advanced.setVisible(False)
        layout.addWidget(self._advanced)
        self.btn_advanced.setVisible(False)

        _connect_theme_signal(self.apply_theme)

    # ── Building ─────────────────────────────────────────────────────────────

    def set_params(self, params: list[Param]) -> None:
        """Rebuild the controls for `params`, keeping values the user already set."""
        self._memory.update(self.display_values())
        self._memory_defaults.update({p.key: p.default for p in self._params})
        for w in [*self._widgets.values(), *self._labels.values(), *self._helps.values()]:
            w.setParent(None)  # type: ignore[call-overload]  # PySide6 stub omits Optional
            w.deleteLater()
        self._widgets.clear()
        self._labels.clear()
        self._helps.clear()
        self._params = list(params)

        rows = {False: 0, True: 0}
        self.setVisible(bool(self._params))
        for p in self._params:
            grid = self._advanced_grid if p.advanced else self._basic_grid
            widget = self._make_widget(p)
            self._widgets[p.key] = widget
            r = rows[p.advanced]
            if p.kind == "bool":
                grid.addWidget(widget, r, 1)
            else:
                label = QLabel(f"{p.label}:")
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                label.setToolTip(p.help)
                self._labels[p.key] = label
                grid.addWidget(label, r, 0)
                grid.addWidget(widget, r, 1)
            if p.help:
                button = BioHelpButton()
                button.setHelpText(p.help, p.label)
                self._helps[p.key] = button
                grid.addWidget(button, r, 2)
            rows[p.advanced] = r + 1

        self._apply_hidden()
        has_advanced = rows[True] > 0
        self.btn_advanced.setVisible(has_advanced)
        self._advanced.setVisible(has_advanced and self.btn_advanced.isChecked())
        self._apply_units()
        self._apply_dependencies()
        self.apply_theme()

    def set_options(self, source: str, names: list[str]) -> None:
        """Offer `names` in every ``options`` picker fed by `source`, keeping each choice that survives."""
        self._options[source] = list(names)
        for p in self._params:
            if p.kind == "options" and p.source == source:
                combo = self._widgets[p.key]
                assert isinstance(combo, BioComboBox)
                current = combo.currentText() or self._memory.get(p.key, "")
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(names)
                if current in names:
                    combo.setCurrentText(current)
                combo.blockSignals(False)

    def set_unit_display(self, unit_display: UnitDisplay) -> None:
        """Show units through `unit_display`. The numbers the user typed are kept."""
        self._unit_display = unit_display
        self._apply_units()

    def set_hidden(self, keys: set[str]) -> None:
        """Hide the controls of `keys`; their values are still read and written."""
        self._hidden = set(keys)
        self._apply_hidden()

    # ── Values ───────────────────────────────────────────────────────────────

    def display_values(self) -> dict[str, Any]:
        """Every control's value as shown (in the displayed unit)."""
        return {p.key: self._read(p) for p in self._params}

    def set_display_values(self, values: dict[str, Any]) -> None:
        """Set controls from values in the displayed unit; unknown keys are remembered for later."""
        self._memory.update(values)
        for key in values:
            self._memory_defaults.pop(key, None)
        for p in self._params:
            if p.key in values:
                self._write(p, values[p.key])
        self._apply_dependencies()

    def values(self) -> dict[str, Any]:
        """The algorithm's input: every value converted back to its declared unit."""
        out: dict[str, Any] = {}
        for p in self._params:
            value = self._read(p)
            if p.kind in ("float", "int") and p.unit:
                value = value / self._unit_display(p.unit)[1]
            if p.kind == "int":
                value = int(round(value))
            out[p.key] = value
        return out

    def reset_to_defaults(self) -> None:
        """Put every control back to its parameter's declared default."""
        self.set_display_values({p.key: p.default for p in self._params})
        self.changed.emit()

    def save_defaults(self, store: PreferenceManagerProtocol, namespace: str) -> None:
        """Keep the current values as this user's defaults for `namespace` in `store`."""
        saved = dict(store.get(namespace, {}) or {})
        saved.update(self.display_values())
        store.set(namespace, saved)
        store.save()

    def load_defaults(self, store: PreferenceManagerProtocol, namespace: str) -> bool:
        """Apply this user's saved defaults for `namespace`, if any. Returns whether there were any."""
        saved = store.get(namespace, {}) or {}
        if not saved:
            return False
        self.set_display_values(dict(saved))
        return True

    # ── Access for tests and hosts ───────────────────────────────────────────

    def widget(self, key: str) -> QWidget | None:
        return self._widgets.get(key)

    def label(self, key: str) -> QLabel | None:
        return self._labels.get(key)

    def apply_theme(self) -> None:
        """Re-colour the plain Qt widgets (the Bio* ones restyle themselves)."""
        colors, _, _ = _get_theme_tokens()
        for p in self._params:
            if p.kind == "bool":
                self._widgets[p.key].setStyleSheet(f"color: {colors.FG_PRIMARY};")
        for label in self._labels.values():
            label.setStyleSheet(f"color: {colors.FG_PRIMARY};")

    # ── Internals ────────────────────────────────────────────────────────────

    def _remembered(self, p: Param) -> Any:
        if p.key in self._memory and self._memory_defaults.get(p.key, p.default) == p.default:
            return self._memory[p.key]
        return p.default

    def _make_widget(self, p: Param) -> QWidget:
        remembered = self._remembered(p)
        if p.kind == "bool":
            box = QCheckBox(p.label)
            box.setChecked(bool(remembered))
            box.setToolTip(p.help)
            box.toggled.connect(self._on_changed)
            return box
        if p.kind in ("options", "choice"):
            combo = BioComboBox()
            combo.setMinimumWidth(180)
            combo.view().setMinimumWidth(300)
            combo.setToolTip(p.help)
            if p.kind == "options":
                names = self._options.get(p.source, [])
                combo.addItems(names)
                if remembered in names:
                    combo.setCurrentText(remembered)
            else:
                for text, data in p.choices:
                    combo.addItem(text, data)
                combo.setCurrentIndex(max(combo.findData(remembered), 0))
            combo.currentIndexChanged.connect(self._on_changed)
            return combo
        spin = BioDoubleSpinBox()
        spin.setDecimals(0 if p.kind == "int" else p.decimals)
        spin.setRange(
            p.minimum if p.minimum is not None else -1e9,
            p.maximum if p.maximum is not None else 1e9,
        )
        if p.step is not None:
            spin.setSingleStep(p.step)
        if p.special_value_text:
            spin.setSpecialValueText(p.special_value_text)
        spin.setValue(float(remembered or 0))
        spin.setToolTip(p.help)
        spin.valueChanged.connect(self._on_changed)
        return spin

    def _read(self, p: Param) -> Any:
        w = self._widgets[p.key]
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, BioComboBox):
            return w.currentText() if p.kind == "options" else w.currentData()
        assert isinstance(w, BioDoubleSpinBox)
        return int(w.value()) if p.kind == "int" else w.value()

    def _write(self, p: Param, value: Any) -> None:
        w = self._widgets[p.key]
        w.blockSignals(True)
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, BioComboBox):
            if p.kind == "options":
                w.setCurrentText(str(value))
            else:
                w.setCurrentIndex(max(w.findData(value), 0))
        elif isinstance(w, BioDoubleSpinBox):
            w.setValue(float(value or 0))
        w.blockSignals(False)

    def _apply_hidden(self) -> None:
        for key, widget in self._widgets.items():
            widget.setVisible(key not in self._hidden)
            for extra in (self._labels, self._helps):
                if key in extra:
                    extra[key].setVisible(key not in self._hidden)

    def _apply_units(self) -> None:
        for p in self._params:
            w = self._widgets[p.key]
            if isinstance(w, BioDoubleSpinBox) and p.unit:
                w.setSuffix(f" {self._unit_display(p.unit)[0]}")

    def _apply_dependencies(self) -> None:
        for p in self._params:
            if not p.enabled_by or p.enabled_by not in self._widgets:
                continue
            switch = self._widgets[p.enabled_by]
            on = isinstance(switch, QCheckBox) and switch.isChecked()
            self._widgets[p.key].setEnabled(on)
            if p.key in self._labels:
                self._labels[p.key].setEnabled(on)

    def _on_changed(self, *_args: object) -> None:
        self._apply_dependencies()
        self.changed.emit()

    def _on_advanced_toggled(self, checked: bool) -> None:
        self.btn_advanced.setText(_ADVANCED_OPEN if checked else _ADVANCED_CLOSED)
        self._advanced.setVisible(checked)


def _grid(parent: QWidget) -> QGridLayout:
    grid = QGridLayout(parent)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(8)
    grid.setColumnStretch(1, 1)
    return grid
