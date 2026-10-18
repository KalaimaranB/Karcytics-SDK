import textwrap

from karcytics_sdk.evaluator import Mode, design_rules, evaluate_repo

from .conftest import PACKAGE


def _design(repo, **kwargs):
    # Most fixtures are deliberately Qt-free ui/ modules; KS330 has its own test.
    kwargs.setdefault("ignore", ["KS330"])
    return evaluate_repo(repo.root, modes=[Mode.DESIGN], **kwargs).findings


def _ids(repo, source: str, zone: str = "ui"):
    path = repo.ui(textwrap.dedent(source)) if zone == "ui" else repo.analysis(textwrap.dedent(source))
    return [(f.rule_id, f.line) for f in _design(repo) if f.path == path]


def _module(repo, name: str, source: str) -> str:
    relative = f"{PACKAGE}/ui/{name}.py"
    repo.write(relative, textwrap.dedent(source))
    return relative


# ── Size ─────────────────────────────────────────────────────────────


def test_long_file_class_and_function_carry_their_measurement(repo, monkeypatch):
    monkeypatch.setattr(design_rules, "MAX_FILE_LINES", 5)
    monkeypatch.setattr(design_rules, "MAX_CLASS_METHODS", 2)
    monkeypatch.setattr(design_rules, "MAX_FUNCTION_LINES", 3)
    path = repo.ui(
        "class Panel:\n"
        "    def a(self):\n"
        "        x = 1\n"
        "        y = 2\n"
        "        return x + y\n"
        "    def b(self):\n"
        "        pass\n"
        "    def c(self):\n"
        "        pass\n"
    )
    found = {(f.rule_id, f.subject, f.metric) for f in _design(repo) if f.path == path}
    assert found == {("KS300", "", 9), ("KS301", "Panel", 3), ("KS302", "Panel.a", 4)}


# ── KISS ─────────────────────────────────────────────────────────────


def test_static_only_class(repo):
    source = """\
        class Helpers:
            @staticmethod
            def a():
                pass

            @staticmethod
            def b():
                pass


        class Proto(Base):
            @staticmethod
            def a():
                pass

            @staticmethod
            def b():
                pass
        """
    assert _ids(repo, source) == [("KS341", 1)]


def test_pass_through_class(repo):
    forwarding = "\n".join(
        f"    def m{i}(self, x):\n        return self._{'a' if i % 2 else 'b'}.m{i}(x)\n" for i in range(5)
    )
    source = f"class Facade:\n    def __init__(self, a, b):\n        self._a, self._b = a, b\n\n{forwarding}\n    def real(self):\n        x = 1\n        return x\n"
    assert _ids(repo, source) == [("KS340", 1)]
    few = "class Small:\n" + "\n".join(f"    def m{i}(self):\n        return self._a.m{i}()\n" for i in range(4))
    assert _ids(repo, few) == []


def test_private_access_on_other_objects(repo):
    source = """\
        import os


        class Node:
            def __init__(self):
                self._mask = None

            def merge(self, other, panel):
                own = self._mask
                same_class = other._mask
                reach = panel._sidebar
                chain = self._panel._sidebar
                probe = getattr(panel, "_sidebar", None)
                module = os._exit
                fine = self._replace
                return super()._merge()

            def __eq__(self, other):
                return self._mask == other._mask
        """
    assert _ids(repo, source) == [("KS342", 11), ("KS342", 12), ("KS342", 13)]


def test_getattr_on_self(repo):
    source = """\
        class Panel:
            def refresh(self):
                if hasattr(self, "_cache"):
                    pass
                value = getattr(self, "_mode", None)
                dynamic = getattr(self, name)
                setattr(self, "_mode", 1)
                return getattr(self, "__class__")
        """
    assert _ids(repo, source) == [("KS343", 3), ("KS343", 5)]


def test_type_name_comparisons(repo):
    source = """\
        if type(gate).__name__ == "QuadrantGate":
            pass
        if gate.__class__.__name__ in ("A", "B"):
            pass
        if isinstance(gate, Quadrant) or cls.__name__ == "X":
            pass
        """
    assert _ids(repo, source) == [("KS344", 1), ("KS344", 3)]


def test_import_inside_a_function(repo):
    source = """\
        import os


        def load():
            import json
            from . import sibling  # karcytics: allow KS360 - startup time
            return json, sibling
        """
    assert _ids(repo, source) == [("KS360", 5)]


def test_tagged_trace_logged_as_warning(repo):
    source = """\
        logger.warning("[phase1] building panel")
        logger.warning(f"[RENDER] {n} points")
        self._log.error("[x] y")
        logger.warning("Could not read [file]")
        logger.debug("[phase1] fine at debug")
        warnings.warn("[tag] not a logger")
        """
    assert _ids(repo, source) == [("KS361", 1), ("KS361", 2), ("KS361", 3)]


def test_plan_references_in_comments_are_info(repo):
    path = repo.ui("x = 1  # cached here (Priority 1 analysis #1)\ny = 2  # Phase 1 of startup\n")
    found = [(f.rule_id, f.line, f.severity.value) for f in _design(repo) if f.path == path]
    assert found == [("KS362", 1, "info")]


# ── Duplication (cross-file) ─────────────────────────────────────────


LABEL = """\
    def _section_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; color: grey;")
        label.setWordWrap(True)
        return label
"""


def test_same_helper_with_a_similar_body_in_several_modules(repo):
    a = _module(repo, "a", f"class A:\n{LABEL}")
    b = _module(repo, "b", f"class B:\n{LABEL.replace('grey', 'gray')}")
    _module(repo, "c", "class C:\n    def _section_label(self, text):\n        return None\n")
    found = sorted((f.path, f.rule_id) for f in _design(repo) if f.rule_id == "KS320")
    assert found == [(a, "KS320"), (b, "KS320")]


def test_same_helper_skips_overrides_and_conventional_hooks(repo):
    _module(repo, "base", f"class Base:\n{LABEL}")
    _module(repo, "a", f"from .base import Base\n\n\nclass A(Base):\n{LABEL}")
    _module(repo, "b", f"class B:\n{LABEL.replace('_section_label', '_apply_theme_styles')}")
    _module(repo, "c", f"class C:\n{LABEL.replace('_section_label', '_apply_theme_styles')}")
    assert [f for f in _design(repo) if f.rule_id == "KS320"] == []


CLONE = """\
def {name}(gate, sample):
    {var} = []
    for node in sample.nodes:
        if node.gate is gate:
            {var}.append(node)
    if not {var}:
        return None
    total = 0
    for node in {var}:
        total += node.count
    return total / len({var})
"""


def test_duplicated_function_bodies_match_despite_renamed_variables(repo):
    a = _module(repo, "a", CLONE.format(name="mean_count", var="found"))
    b = _module(repo, "b", CLONE.format(name="average", var="matches"))
    _module(repo, "c", "def short(x):\n    return x\n")
    found = sorted((f.path, f.rule_id) for f in _design(repo) if f.rule_id == "KS321")
    assert found == [(a, "KS321"), (b, "KS321")]


def test_unhandled_and_unused_event_topics(repo):
    repo.write(
        f"{PACKAGE}/analysis/events.py",
        'HANDLED = "x.handled"\nVIA_HELPER = "x.helper"\nDEAD = "x.dead"\nUNUSED = "x.unused"\n',
    )
    publisher = repo.analysis(
        "from . import events\n\n"
        "def emit(bus):\n"
        "    CentralEventBus.publish(events.HANDLED, {})\n"
        "    CentralEventBus.publish(events.VIA_HELPER, {})\n"
        "    CentralEventBus.publish(events.DEAD, {})\n"
        "    CentralEventBus.publish('literal.topic', {})\n"
    )
    _module(
        repo,
        "listener",
        "from ..analysis import events\n\n"
        "CentralEventBus.subscribe(events.HANDLED, print)\n"
        "TOPICS = [events.VIA_HELPER]\n",
    )
    found = sorted((f.rule_id, f.path, f.line) for f in _design(repo) if f.rule_id in ("KS350", "KS351"))
    assert found == [("KS350", publisher, 6), ("KS351", f"{PACKAGE}/analysis/events.py", 4)]


def test_cross_file_rules_are_skipped_for_a_files_run(repo):
    a = _module(repo, "a", CLONE.format(name="mean_count", var="found"))
    _module(repo, "b", CLONE.format(name="average", var="matches"))
    assert [f for f in _design(repo, files=[repo.root / a]) if f.rule_id == "KS321"] == []


# ── Layering ─────────────────────────────────────────────────────────


def test_ui_module_without_ui_code_is_info(repo):
    logic = repo.ui("import numpy as np\n\n\ndef stats(x):\n    return np.mean(x)\n")
    widget = _module(repo, "widget", "from karcytics_sdk.plugin import BioButton\n")
    themed = _module(repo, "themed", "from karcytics_sdk.plugin.theme_fallback import Colors\n")
    plot = _module(repo, "plot", "import matplotlib.pyplot as plt\n")
    found = [(f.rule_id, f.path, f.severity.value) for f in _design(repo, ignore=[]) if f.rule_id == "KS330"]
    assert found == [("KS330", logic, "info")]
    assert {widget, themed, plot}.isdisjoint(path for _, path, _ in found)


def test_calls_reserved_to_analysis(repo):
    repo.write(
        "pyproject.toml",
        (repo.root / "pyproject.toml").read_text()
        + '\n[tool.karcytics.evaluator]\nanalysis_only = ["apply_hierarchy", "fcs_data.events"]\n',
    )
    source = """\
        def refresh(sample, node):
            df = sample.fcs_data.events
            gated = node.apply_hierarchy(df)
            n = len(sample.fcs_data.meta)
            return apply_hierarchy(gated)
        """
    assert _ids(repo, source) == [("KS331", 2), ("KS331", 3), ("KS331", 5)]
    assert _ids(repo, source, zone="analysis") == []


def test_ui_objects_on_state(repo):
    source = """\
        from dataclasses import dataclass
        from typing import Any

        from karcytics_sdk.plugin import PluginState


        @dataclass
        class ViewState:
            zoom: float = 1.0
            _viewer: Any | None = None
            panel: "SpectralPanel | None" = None


        class FlowState(PluginState):
            canvas: QWidget | None = None
            note: str = ""


        @dataclass
        class Settings:
            _viewer: Any = None
        """
    assert _ids(repo, source, zone="analysis") == [("KS332", 10), ("KS332", 11), ("KS332", 15)]


def test_qt_backed_sdk_exports_in_analysis(repo):
    path = repo.analysis("from karcytics_sdk.plugin import CentralEventBus, Debouncer, AnalysisBase\n")
    report = evaluate_repo(repo.root, modes=[Mode.FUNCTIONAL])
    assert [(f.rule_id, f.message) for f in report.findings if f.path == path] == [
        ("KS205", "imports `Debouncer`, a Qt object, inside analysis/")
    ]
    repo.analysis("")
    ui = repo.ui("from karcytics_sdk.plugin import Debouncer\n")
    assert [f for f in evaluate_repo(repo.root, modes=[Mode.FUNCTIONAL]).findings if f.path == ui] == []


def test_qt_backed_catalog_matches_the_sdk():
    import inspect

    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    from karcytics_sdk import plugin
    from karcytics_sdk.evaluator import catalog

    def qt_backed(obj) -> bool:
        if inspect.isclass(obj):
            return issubclass(obj, QObject) and not issubclass(obj, QWidget)
        return isinstance(obj, QObject) and not isinstance(obj, QWidget)

    exported = {name for name in dir(plugin) if not name.startswith("_") and qt_backed(getattr(plugin, name))}
    assert exported == catalog.QT_BACKED_SDK_EXPORTS
