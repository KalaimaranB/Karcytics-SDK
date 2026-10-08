from pathlib import Path

import pytest

from karcytics_sdk.evaluator import Mode, Severity, evaluate_repo

from .conftest import PACKAGE

QT = "from PySide6.QtWidgets import QPushButton, QComboBox, QMessageBox, QFileDialog, QInputDialog, QCheckBox\n"


def _findings(repo, path: str):
    report = evaluate_repo(repo.root, modes=[Mode.FUNCTIONAL])
    return [f for f in report.findings if f.path == path]


def _ui(repo, source: str):
    return _findings(repo, repo.ui(source))


def test_hub_import_is_an_error(repo):
    found = _ui(repo, "import os\nfrom karcytics.ui.theme import Colors\nimport karcytics.core\n")
    assert [(f.rule_id, f.line, f.severity) for f in found] == [
        ("KS201", 2, Severity.ERROR),
        ("KS201", 3, Severity.ERROR),
    ]


def test_sdk_and_plugin_namespace_imports_are_not_hub_imports(repo):
    source = "import karcytics_sdk\nfrom karcytics_plugins.demo import x\nfrom . import y\n"
    assert _ui(repo, source) == []


def test_qt_in_analysis_is_an_error_but_fine_in_ui(repo):
    source = "from PySide6.QtCore import QObject\n"
    assert [f.rule_id for f in _findings(repo, repo.analysis(source))] == ["KS202"]
    repo.analysis("")
    assert _ui(repo, source) == []


def test_other_qt_bindings_are_an_error_and_still_analysed(repo):
    found = _ui(repo, "from PyQt6.QtWidgets import QPushButton\nimport PyQt5.QtCore\nb = QPushButton()\n")
    assert [(f.rule_id, f.line) for f in found] == [("KS203", 1), ("KS203", 2), ("KS210", 3)]
    assert found[0].severity is Severity.ERROR


def test_dict_typed_signals_and_slots_are_an_error(repo):
    source = (
        "from PySide6.QtCore import QObject, Signal, Slot\n\n\n"
        "class Bus(QObject):\n"
        "    done = Signal(dict)\n"
        "    named = Signal(str, dict)\n"
        "    fine = Signal(str, object)\n\n"
        "    @Slot(dict)\n"
        "    def on_done(self, payload):\n"
        "        pass\n"
    )
    assert [(f.rule_id, f.line) for f in _ui(repo, source)] == [("KS204", 5), ("KS204", 6), ("KS204", 9)]


def test_the_sdk_itself_declares_no_dict_signals_or_foreign_bindings():
    sdk_root = Path(__file__).parents[3]
    report = evaluate_repo(sdk_root, modes=[Mode.FUNCTIONAL], select=["KS203", "KS204"])
    assert [(f.path, f.line, f.rule_id) for f in report.findings] == []


def test_raw_widget_reports_line_column_and_sdk_equivalent(repo):
    (finding,) = _ui(repo, QT + "\n\ndef build():\n    return QPushButton('Go')\n")
    assert (finding.rule_id, finding.line, finding.col) == ("KS210", 5, 12)
    assert "BioButton" in finding.remedy


@pytest.mark.parametrize(
    "source",
    [
        "from PySide6.QtWidgets import QComboBox as Combo\nw = Combo()\n",
        "from PySide6 import QtWidgets\nw = QtWidgets.QComboBox()\n",
        "import PySide6.QtWidgets as qw\nw = qw.QComboBox()\n",
        "import PySide6.QtWidgets\nw = PySide6.QtWidgets.QComboBox()\n",
    ],
)
def test_raw_widget_is_found_through_any_import_style(repo, source):
    assert [f.rule_id for f in _ui(repo, source)] == ["KS210"]


def test_widgets_without_an_sdk_equivalent_are_not_flagged(repo):
    assert _ui(repo, QT + "a = QCheckBox('x')\n") == []


def test_local_class_sharing_a_qt_name_is_not_flagged(repo):
    assert _ui(repo, "class QPushButton:\n    pass\n\n\nb = QPushButton()\n") == []


def test_sdk_components_are_not_flagged(repo):
    assert _ui(repo, "from karcytics_sdk.plugin import BioButton\nb = BioButton('x')\n") == []


def test_subclassing_a_wrapped_widget(repo):
    (finding,) = _ui(repo, QT + "\n\nclass Fancy(QComboBox):\n    pass\n")
    assert (finding.rule_id, finding.line) == ("KS211", 4)
    assert "BioComboBox" in finding.remedy


def test_dialog_calls_map_to_their_sdk_helper(repo):
    source = QT + (
        "QMessageBox.critical(None, 't', 'm')\n"
        "QFileDialog.getSaveFileName(None)\n"
        "QInputDialog.getText(None, 't', 'l')\n"
        "box = QMessageBox()\n"
        "ok = QMessageBox.StandardButton.Yes\n"
    )
    found = _ui(repo, source)
    assert [f.rule_id for f in found] == ["KS220"] * 4
    assert ["show_error" in found[0].remedy, "get_save_path" in found[1].remedy, "get_text" in found[2].remedy] == [
        True,
        True,
        True,
    ]


def test_open_file_dialog_is_only_advisory(repo):
    (finding,) = _ui(repo, QT + "QFileDialog.getOpenFileName(None)\n")
    assert (finding.rule_id, finding.severity) == ("KS220", Severity.INFO)


def test_qt_threading(repo):
    source = (
        "from PySide6.QtCore import QThread, QRunnable, QThreadPool\n\n\n"
        "class Worker(QThread):\n    pass\n\n\n"
        "class Job(QRunnable):\n    pass\n\n\n"
        "t = QThread()\n"
        "QThreadPool.globalInstance().start(Job())\n"
    )
    assert [(f.rule_id, f.line) for f in _ui(repo, source)] == [
        ("KS230", 4),
        ("KS230", 8),
        ("KS230", 12),
        ("KS230", 13),
    ]


def test_sdk_worker_thread_subclass_is_not_flagged(repo):
    source = "from karcytics_sdk.plugin import OneShotWorkerThread\n\n\nclass W(OneShotWorkerThread):\n    pass\n"
    assert _ui(repo, source) == []


PY_THREADS = (
    "import threading\nfrom concurrent.futures import ThreadPoolExecutor\n"
    "threading.Thread(target=print).start()\nThreadPoolExecutor()\n"
)


def test_python_threading_is_a_warning_in_ui(repo):
    assert [(f.rule_id, f.severity) for f in _ui(repo, PY_THREADS)] == [("KS231", Severity.WARNING)] * 2


def test_python_threading_is_only_info_in_analysis(repo):
    found = _findings(repo, repo.analysis(PY_THREADS))
    assert [(f.rule_id, f.severity) for f in found] == [("KS231", Severity.INFO)] * 2


def test_hard_coded_colours(repo):
    source = (
        '"""Module docstring mentioning #ffffff is prose."""\n'
        "from karcytics_sdk.plugin.theme_fallback import Colors\n"
        "A = '#1a2B3c'\n"
        "B = f'color: {Colors.FG_PRIMARY}; background: #00000080;'\n"
        "C = 'see issue #123456789 or item #12'\n"
        "D = '''\n  border: 1px;\n  color: #abcdef;\n'''\n"
        "E = '#111111 #222222'\n"
    )
    found = [f for f in _ui(repo, source) if f.rule_id == "KS240"]
    assert [(f.line, f.message) for f in found] == [
        (3, "hard-coded colour `#1a2B3c`"),
        (4, "hard-coded colour `#00000080`"),
        (8, "hard-coded colour `#abcdef`"),
        (10, "hard-coded colour `#111111`"),
    ]


def test_colours_are_not_checked_in_analysis(repo):
    assert _findings(repo, repo.analysis("PALETTE = ['#ff0000']\n")) == []


def test_logging_print_json_and_stylesheet(repo):
    source = (
        "import json\nimport logging\n"
        "log = logging.getLogger(__name__)\n"
        "print('hi')\n"
        "data = json.load(open('f'))\n"
        "text = json.dumps({})\n"
        "widget.setStyleSheet('x')\n"
    )
    found = _ui(repo, source)
    assert [(f.rule_id, f.line, f.severity) for f in found] == [
        ("KS250", 3, Severity.WARNING),
        ("KS252", 4, Severity.WARNING),
        ("KS251", 5, Severity.INFO),
        ("KS241", 7, Severity.INFO),
    ]


def test_sdk_logger_is_not_flagged(repo):
    assert _ui(repo, "from karcytics_sdk.plugin import get_logger\nlog = get_logger(__name__)\n") == []


def test_syntax_error_is_reported_not_raised(repo):
    (finding,) = _ui(repo, "def broken(:\n")
    assert (finding.rule_id, finding.line) == ("KS200", 1)


def test_missing_sdk_base_classes(repo):
    repo.write(f"{PACKAGE}/ui/panel.py", "")
    repo.write(f"{PACKAGE}/analysis/engine.py", "")
    found = _findings(repo, PACKAGE)
    assert [(f.rule_id, f.severity) for f in found] == [("KS260", Severity.WARNING), ("KS261", Severity.INFO)]


def test_tests_and_virtualenvs_are_not_analysed(repo):
    raw = QT + "b = QPushButton()\n"
    repo.write(f"{PACKAGE}/tests/test_x.py", raw)
    repo.write(f"{PACKAGE}/.venv/lib/x.py", raw)
    assert evaluate_repo(repo.root, modes=[Mode.FUNCTIONAL]).findings == []


def test_non_plugin_repo_still_gets_functional_checks(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/main.py").write_text(QT + "b = QPushButton()\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_main.py").write_text(QT + "b = QPushButton()\n", encoding="utf-8")
    report = evaluate_repo(tmp_path, modes=[Mode.FUNCTIONAL])
    assert [(f.rule_id, f.path) for f in report.findings] == [("KS210", "app/main.py")]
    assert report.plugin_id is None


TEST_FILE = "tests/unit/test_panel.py"
PANEL_IMPORT = "from karcytics_plugins.demo.ui.panel import Panel\n\n\n"


def _test_file(repo, source: str):
    repo.write(TEST_FILE, source)
    return [(f.rule_id, f.line) for f in _findings(repo, TEST_FILE)]


def test_test_that_drops_the_panel_is_flagged(repo):
    source = (
        PANEL_IMPORT + "def test_direct():\n    panel = Panel()\n    assert panel\n\n\n"
        "def test_factory(plugin):\n    assert plugin.create_panel()\n"
    )
    assert _test_file(repo, source) == [("KS270", 5), ("KS270", 10)]


def test_test_that_destroys_or_hands_over_the_panel_is_not_flagged(repo):
    source = (
        PANEL_IMPORT + "def test_qtbot(qtbot):\n    panel = Panel()\n    qtbot.addWidget(panel)\n\n\n"
        "def test_handed_over(destroy_after, plugin):\n    panel = destroy_after(plugin.create_panel())\n\n\n"
        "def panel_fixture():\n    panel = Panel()\n    yield panel\n    panel.deleteLater()\n\n\n"
        "def test_shiboken():\n    import shiboken6\n\n    panel = Panel()\n    shiboken6.delete(panel)\n\n\n"
        "def test_other_widget():\n    assert Helper()\n"
    )
    assert _test_file(repo, source) == []


def test_nested_function_is_judged_on_its_own(repo):
    source = PANEL_IMPORT + "def test_outer():\n    def build():\n        return Panel()\n\n    build().deleteLater()\n"
    assert _test_file(repo, source) == [("KS270", 6)]
