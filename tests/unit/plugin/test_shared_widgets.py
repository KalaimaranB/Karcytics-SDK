"""Tests for the small shared pieces promoted out of plugins: `style_axes`,
`ZoomControls`, `confirm_destructive`, and `BioComboBox(fit_contents=True)`.
Each replaces copies that had drifted apart in the flow-cytometry plugin.
"""

import matplotlib

matplotlib.use("Agg")

from matplotlib.colors import to_hex  # noqa: E402 - after selecting the headless backend
from matplotlib.figure import Figure  # noqa: E402 - after selecting the headless backend
from PySide6.QtWidgets import QMessageBox, QWidget  # noqa: E402 - grouped with the other GUI imports

from karcytics_sdk.plugin import (  # noqa: E402 - see above
    AxesColors,
    BioComboBox,
    BioListWidget,
    BioToggleButton,
    ZoomControls,
    confirm_destructive,
    style_axes,
)

COLORS = AxesColors(foreground="#aaaaaa", border="#333333", background="#101010")


def _axes():
    return Figure().add_subplot()


def test_style_axes_colours_spines_ticks_and_faces():
    ax = _axes()
    style_axes(ax, COLORS)
    assert {to_hex(s.get_edgecolor()) for s in ax.spines.values()} == {"#333333"}
    assert all(s.get_visible() for s in ax.spines.values())
    assert to_hex(ax.get_facecolor()) == "#101010"
    assert to_hex(ax.figure.patch.get_facecolor()) == "#101010"
    assert to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()) == "#aaaaaa"


def test_style_axes_open_frame_grid_and_untouched_background():
    ax = _axes()
    ax.set_facecolor("#ffffff")
    style_axes(ax, AxesColors("#aaaaaa", "#333333"), open_frame=True, grid="y")
    assert not ax.spines["top"].get_visible() and not ax.spines["right"].get_visible()
    assert ax.spines["left"].get_visible()
    assert to_hex(ax.get_facecolor()) == "#ffffff"
    assert ax.yaxis.get_gridlines()[0].get_visible()
    assert not ax.xaxis.get_gridlines()[0].get_visible()


def test_zoom_controls_call_back_and_overlay_the_host(qtbot):
    calls = []
    host = QWidget()
    qtbot.addWidget(host)
    pan = BioToggleButton("Pan")
    controls = ZoomControls(
        lambda: calls.append("in"), lambda: calls.append("out"), lambda: calls.append("fit"), extra=[pan]
    )
    controls.overlay_on(host)

    controls.zoom_in_button.click()
    controls.zoom_out_button.click()
    controls.fit_button.click()
    assert calls == ["in", "out", "fit"]
    assert host.layout().itemAt(0).widget() is controls
    assert controls.layout().itemAt(0).widget() is pan


def test_confirm_destructive_lists_the_items_and_defaults_to_no(monkeypatch):
    shown = {}

    def fake_exec(box):
        shown.update(text=box.text(), info=box.informativeText(), default=box.defaultButton().text())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    items = [f"Child {i}" for i in range(8)]
    assert confirm_destructive(None, "Delete", "Deleting 'T cells' also deletes 8 children:", items) is False
    assert shown["text"] == "Deleting 'T cells' also deletes 8 children:"
    assert shown["info"].splitlines() == [*(f"• Child {i}" for i in range(6)), "... and 2 more"]
    assert shown["default"].replace("&", "") == "No"

    monkeypatch.setattr(QMessageBox, "exec", lambda box: QMessageBox.StandardButton.Yes)
    assert confirm_destructive(None, "Delete", "Sure?") is True


def test_fit_contents_combo_opens_wide_enough_for_its_longest_item(qtbot):
    long_name = "CD45+ / CD3+ / CD4+ / CD25hi CD127lo regulatory T cells"
    plain, fitted = BioComboBox(), BioComboBox(fit_contents=True)
    for combo in (plain, fitted):
        qtbot.addWidget(combo)
        combo.addItems(["A", long_name])
        combo.resize(80, 24)

    needed = fitted.fontMetrics().horizontalAdvance(long_name)
    assert plain.popup_width() == 80
    assert fitted.popup_width() >= needed


def test_list_widget_themes_its_item_checkboxes(qtbot):
    from karcytics_sdk.plugin.theme_fallback import Colors

    lst = BioListWidget()
    qtbot.addWidget(lst)
    lst._apply_theme_styles()
    sheet = lst.styleSheet()
    assert "QListWidget::indicator:checked" in sheet
    assert Colors.ACCENT_PRIMARY in sheet.split("QListWidget::indicator:checked", 1)[1]
