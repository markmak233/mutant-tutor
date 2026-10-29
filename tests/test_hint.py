"""The hint command: rules first, then the exercise's known mistakes (its mutants)."""

import json

import pytest

from tutor.execution import run_pytest
from tutor.hint import mutants
from tutor.hint.__main__ import hint_for, log_hint
from tutor.hint.rules import first_rule

TESTS = (
    "from student import total\n\n\n"
    "def test_example():\n    assert total([1, 2, 3]) == 6\n\n\n"
    "def test_empty():\n    assert total([]) == 0\n\n\n"
    "def test_negative():\n    assert total([-1, 1]) == 0\n"
)
MUTANTS = {
    "m01_skip_first": '"""Mutant: starts at the second element, so the first value is lost."""\n'
                      "def total(xs):\n    return sum(xs[1:])\n",
    "m02_plus_one": '"""Mutant: starts the running total at 1, every total is one too big."""\n'
                    "def total(xs):\n    return sum(xs) + 1\n",
}
# The first mutant's mistake, written the way a student would.
SKIPS_FIRST = "def total(xs):\n    t = 0\n    for x in xs[1:]:\n        t += x\n    return t\n"


def batch(tmp_path, student, mode="learn", constraints=None):
    """A batch with one exercise, laid out as the pipeline saves it."""
    root = tmp_path / "batches" / "2026-10-29_p3-demo"
    exercise, solution = root / "exercises" / "sum-list", root / "solutions" / "sum-list"
    (solution / "mutants").mkdir(parents=True)
    exercise.mkdir(parents=True)
    (exercise / "test_student.py").write_text(TESTS)
    (exercise / "student.py").write_text(student)
    (exercise / "hints.md").write_text("## Hint 1\n")
    (root / "manifest.json").write_text(json.dumps({"mode": mode}))
    (solution / "metadata.json").write_text(json.dumps(
        {"id": "sum-list", "constraints": constraints or {}}))
    (solution / "reference.py").write_text("def total(xs):\n    return sum(xs)\n")
    (solution / "skeleton.py").write_text("def total(xs):\n    raise NotImplementedError\n")
    for name, code in MUTANTS.items():
        (solution / "mutants" / f"{name}.py").write_text(code)
    return exercise


class CountingRuns:
    def __init__(self):
        self.calls = 0

    def __call__(self, files, timeout, env):
        self.calls += 1
        return run_pytest(files, timeout, env)


def test_a_student_who_makes_a_known_mistake_is_told_that_mistake(tmp_path):
    runs = CountingRuns()
    hint = hint_for(batch(tmp_path, SKIPS_FIRST), run_tests=runs, say=lambda _: None)
    assert (hint.layer, hint.key) == ("mutant", "m01_skip_first")
    assert hint.text == ("your tests fail exactly as a known mistake: starts at the second "
                         "element, so the first value is lost")
    assert runs.calls == 3  # the student, then each mutant once


def test_mutant_signatures_are_computed_once(tmp_path):
    exercise = batch(tmp_path, SKIPS_FIRST)
    hint_for(exercise, say=lambda _: None)
    runs = CountingRuns()
    assert hint_for(exercise, run_tests=runs, say=lambda _: None).key == "m01_skip_first"
    assert runs.calls == 1  # only the student's run; the mutants come from the cache
    (exercise.parent.parent / "solutions" / "sum-list" / "mutants" / "m02_plus_one.py"
     ).write_text(MUTANTS["m02_plus_one"].replace("+ 1", "+ 2"))
    runs = CountingRuns()
    hint_for(exercise, run_tests=runs, say=lambda _: None)
    assert runs.calls == 2  # the changed mutant runs again


def test_a_review_batch_hears_only_what_goes_wrong(tmp_path):
    hint = hint_for(batch(tmp_path, SKIPS_FIRST, mode="review"), say=lambda _: None)
    assert hint.text == ("your tests fail exactly as a known mistake, where the first value "
                         "is lost; look for the cause")
    assert "second element" not in hint.text


def test_rules_come_before_known_mistakes(tmp_path):
    hint = hint_for(batch(tmp_path, SKIPS_FIRST.replace("t += x", "t += x，")),
                    say=lambda _: None)
    assert (hint.layer, hint.key) == ("rule", "full-width")
    assert "line 4: '，' should be ','" in hint.text
    todo = hint_for(batch(tmp_path / "b", "def total(xs):\n    raise NotImplementedError\n"),
                    say=lambda _: None)
    assert todo.key == "todo-left"


def test_full_width_punctuation_in_comments_and_strings_is_not_a_mistake(tmp_path):
    commented = SKIPS_FIRST.replace("t = 0", "t = 0  # 总和，从零开始")
    commented = '"""求和：返回总数。"""\n' + commented
    assert hint_for(batch(tmp_path, commented), say=lambda _: None).key == "m01_skip_first"


def test_a_forbidden_api_is_named(tmp_path):
    hint = hint_for(batch(tmp_path, "def total(xs):\n    return sum(xs) - 1\n",
                          constraints={"forbidden_calls": ["sum"]}), say=lambda _: None)
    assert hint.key == "forbidden-api" and "uses sum" in hint.text


def test_passing_code_gets_no_hint_and_unknown_failures_show_the_first_error(tmp_path):
    assert hint_for(batch(tmp_path, "def total(xs):\n    return sum(xs)\n")) is None
    only_example = "def total(xs):\n    return 0 if len(xs) == 3 else sum(xs)\n"
    hint = hint_for(batch(tmp_path / "b", only_example), say=lambda _: None)
    assert hint.layer == "none" and "test_example" in hint.text and "hints.md" in hint.text
    # Failing the same tests as a mutant, with other values, is offered as a guess.
    guess = hint_for(batch(tmp_path / "c", "def total(xs):\n    return len(xs)\n"),
                     say=lambda _: None)
    assert guess.key == "m01_skip_first" and "it may be that one" in guess.text


@pytest.mark.parametrize(("output", "key"), [
    ("E   py4j.protocol.Py4JJavaError: ... PYTHON_VERSION_MISMATCH ...", "python-version"),
    ("E   TypeError: 'PipelinedRDD' object is not iterable", "rdd-on-driver"),
    ("E   ValueError: Cannot run multiple SparkContexts at once", "spark-context-left"),
    ("E     unexpected: [\"('a', 1)\"]", "tuple-output"),
    ("E   AssertionError: no output was saved to outputPath itself: ...", "not-saved"),
])
def test_rules_recognise_spark_and_output_mistakes(output, key):
    hint = first_rule("x = 1\n", output, {}, "learn")
    assert hint.key == key
    assert first_rule("x = 1\n", output, {}, "review").text != hint.text  # shorter


def test_mutants_the_tests_cannot_tell_apart_are_reported():
    same = {"test_a": ["assert 1 == 2"]}
    assert mutants.indistinguishable({"m01": same, "m02": dict(same), "m03": {}}) == [
        ("m01", "m02")]


def test_signatures_ignore_addresses_and_timings():
    one = "___ test_a ___\nE   AssertionError: <obj at 0x10a2b3> took 1.25s\n"
    two = "___ test_a ___\nE   AssertionError: <obj at 0x99ffee> took 3.50s\n"
    assert mutants.signature(one) == mutants.signature(two) == {
        "test_a": ["AssertionError: <obj at <addr>> took <s>"]}


def test_a_hint_in_a_workspace_batch_is_logged(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n')
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    exercise = batch(tmp_path, SKIPS_FIRST)
    log_hint(exercise, hint_for(exercise, say=lambda _: None))
    line = json.loads((tmp_path / "attempts" / "attempts.jsonl").read_text())
    assert (line["event"], line["layer"], line["key"], line["exercise"]) == (
        "hint", "mutant", "m01_skip_first", "sum-list")


def test_mistakes_the_tests_cannot_tell_apart_are_all_named(tmp_path):
    exercise = batch(tmp_path, SKIPS_FIRST)
    twin = exercise.parent.parent / "solutions" / "sum-list" / "mutants" / "m03_drop_head.py"
    twin.write_text('"""Mutant: drops the head of the list, so the first value is lost."""\n'
                    "def total(xs):\n    return sum(xs[1:])\n")
    hint = hint_for(exercise, say=lambda _: None)
    assert hint.key == "m01_skip_first|m03_drop_head"
    assert "one of: starts at the second element; drops the head of the list" in hint.text


def test_a_guess_prefers_the_mutant_whose_failures_read_most_alike():
    student = {"test_a": ["assert ['b', 'a'] == ['a', 'b']"]}
    known = {"m01_far": {"test_a": ["KeyError: 'x'"]},
             "m02_near": {"test_a": ["assert ['a', 'b', 'c'] == ['a', 'b']"]}}
    assert [m.mutant for m in mutants.matches(student, known)] == ["m02_near", "m01_far"]
