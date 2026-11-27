"""academy-export reads courses statically: splices across files, variables, f-strings, run-time text."""

import json
import textwrap

from karcytics_sdk.academy.export import PLACEHOLDER, export, main

STEPS = textwrap.dedent("""
    from karcytics_sdk.plugin.tutorial_models import InfoStep, QuestionStep, AnswerChoice, BaseStep

    EXTRA_STEPS: list[BaseStep] = [
        # ── Spliced part ──────────────────────────────
        InfoStep(id="x1", text="From " "another file.", next_step_id="x2"),
        QuestionStep(
            id="x2",
            text="Pick one",
            choices=[AnswerChoice("A", True, "Right!"), AnswerChoice(text="B", feedback="Nope")],
            question_id="q1",
        ),
    ]
    MORE_STEPS = [*EXTRA_STEPS, InfoStep(id="x3", text="Third")]
""")
COURSE = textwrap.dedent("""
    from karcytics_sdk.plugin.tutorial_models import Course, InfoStep, VerificationStep
    from .parts import MORE_STEPS

    NAME = "Ada"
    _held = InfoStep(id="held", text="Held in a variable")

    def _later(panel):
        _held.text = "Changed when it runs: " + NAME

    def _helper(result):
        return f"You found {result} clusters, which is a fine number of clusters."

    def _wire(step):
        setattr(_dyn, "text", _helper(3))

    _dyn = InfoStep(id="dyn", text=f"Hello {NAME}, nice to meet you")

    second = Course(id="c2", title="Second", steps=[InfoStep(id="s1", text="Only")])
    first = Course(
        id="c1",
        title="First course",
        description="Learn things",
        estimated_minutes=30,
        prerequisite_course_ids=["c0"],
        steps=[
            # ── Intro ──────────────────────────────
            InfoStep(id="a", text="Welcome", failure_hint="Try again"),
            _held,
            # ── Middle ─────────────────────────────
            *MORE_STEPS,
            VerificationStep(id="v", text="Check", on_success_step_id="a"),
            _dyn,
        ],
    )
    COURSES = (first, second)
""")


def _repo(tmp_path):
    pkg = tmp_path / "src" / "my_plugin" / "tutorials"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg.parent / "__init__.py").write_text("")
    (pkg / "parts.py").write_text(STEPS)
    (pkg / "courses.py").write_text(COURSE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "from x import Course\nfake = Course(id='nope', title='t', steps=[])\n"
    )
    return tmp_path


def _run(tmp_path):
    root = _repo(tmp_path)
    return export(root / "src", "my_plugin", repo_root=root)


def test_courses_in_courses_order_and_tests_skipped(tmp_path):
    d = _run(tmp_path)
    assert [c["id"] for c in d["courses"]] == ["c1", "c2"]
    c = d["courses"][0]
    assert (c["title"], c["description"], c["estimated_minutes"], c["prerequisites"]) == (
        "First course",
        "Learn things",
        30,
        ["c0"],
    )


def test_steps_follow_splices_across_files(tmp_path):
    c = _run(tmp_path)["courses"][0]
    assert [s["id"] for s in c["steps"]] == ["a", "held", "x1", "x2", "x3", "v", "dyn"]
    by = {s["id"]: s for s in c["steps"]}
    assert by["x1"]["text"] == "From another file." and by["x1"]["file"].endswith("parts.py")
    assert c["files"] == sorted({by["a"]["file"], by["x1"]["file"]})
    assert by["a"]["failure_hint"] == "Try again"
    assert by["v"]["links"] == {"on_success_step_id": "a"}


def test_sections_come_from_where_steps_are_listed(tmp_path):
    by = {s["id"]: s for s in _run(tmp_path)["courses"][0]["steps"]}
    assert by["a"]["section"] == "Intro" and by["held"]["section"] == "Intro"
    assert by["x1"]["section"] == "Spliced part"  # its own file's heading wins
    assert by["x3"]["section"] == "Middle"  # no heading in its list: where it's spliced


def test_questions_choices_and_feedback(tmp_path):
    q = {s["id"]: s for s in _run(tmp_path)["courses"][0]["steps"]}["x2"]
    assert q["choices"] == [{"text": "A", "correct": True, "feedback": "Right!"}, {"text": "B", "feedback": "Nope"}]
    assert q["question_id"] == "q1"


def test_dynamic_text_is_marked_not_guessed(tmp_path):
    by = {s["id"]: s for s in _run(tmp_path)["courses"][0]["steps"]}
    assert by["dyn"]["text"] == f"Hello {PLACEHOLDER}, nice to meet you" and by["dyn"]["dynamic"] == ["text"]
    held = by["held"]["variants"][0]
    assert held["text"] == "Changed when it runs: Ada" and held["where"].endswith("courses.py:9")
    assert "clusters, which is a fine number" in by["dyn"]["variants"][0]["text"]


def test_hash_changes_only_with_text(tmp_path):
    d1 = _run(tmp_path)
    by1 = {s["id"]: s["hash"] for s in d1["courses"][0]["steps"]}
    f = tmp_path / "src" / "my_plugin" / "tutorials" / "parts.py"
    f.write_text(
        f.read_text().replace('"Third"', '"Third, reworded"').replace('next_step_id="x2"', 'next_step_id="x3"')
    )
    by2 = {s["id"]: s["hash"] for s in export(tmp_path / "src", "m", repo_root=tmp_path)["courses"][0]["steps"]}
    assert by1["x3"] != by2["x3"] and by1["x1"] == by2["x1"]


def test_unreadable_entries_are_warnings(tmp_path):
    root = _repo(tmp_path)
    f = root / "src" / "my_plugin" / "tutorials" / "courses.py"
    f.write_text(f.read_text().replace("_dyn,\n", "_dyn,\n            *make_steps(),\n            MISSING,\n"))
    d = export(root / "src", "m", repo_root=root)
    assert any("can't read" in w for w in d["warnings"]) and any("MISSING" in w for w in d["warnings"])


def test_cli_writes_json(tmp_path, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "academy.json"
    assert main([str(root), "--module", "my_plugin", "--out", str(out), "--commit", "abc"]) == 0
    d = json.loads(out.read_text())
    assert d["format"] == 1 and d["module"] == "my_plugin" and d["commit"] == "abc" and len(d["courses"]) == 2
    assert "2 courses, 8 steps" in capsys.readouterr().err
