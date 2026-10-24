"""Validation rules 1-3.

Unit tests build tiny pure-Python exercises (an `add` function) in a temp dir, so they need
no Spark. The `spark`-marked tests validate the real examples end to end (about 1.5 min):
run them with `pytest -m spark`.
"""

import json
from pathlib import Path

import pytest

from tutor.config import load_env
from tutor.validation import ExerciseError, Status, load_exercise, validate
from tutor.validation.__main__ import format_report, main

TESTS = (
    "from student import add\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n"
    "def test_negative():\n    assert add(-1, 1) == 0\n"
)
REFERENCE = "def add(a, b):\n    return a + b\n"
SKELETON = "def add(a, b):\n    raise NotImplementedError\n"
MUTANT_SUB = "def add(a, b):\n    return a - b\n"


def make_exercise(tmp_path, *, tests=TESTS, reference=REFERENCE, skeleton=SKELETON,
                  mutants=None, extra_files=None):
    """Write an exercise/ + solution/ pair like the examples, return both paths."""
    mutants = {"m01_sub": MUTANT_SUB} if mutants is None else mutants
    exercise, solution = tmp_path / "exercise", tmp_path / "solution"
    (solution / "mutants").mkdir(parents=True)
    exercise.mkdir()
    (exercise / "test_student.py").write_text(tests)
    (exercise / "student.py").write_text(skeleton)
    (exercise / "hints.md").write_text("# Hints\n")
    for name, text in (extra_files or {}).items():
        (exercise / name).write_text(text)
    (solution / "metadata.json").write_text(json.dumps({"id": "add"}))
    (solution / "reference.py").write_text(reference)
    (solution / "skeleton.py").write_text(skeleton)
    for name, text in mutants.items():
        (solution / "mutants" / f"{name}.py").write_text(text)
    return exercise, solution


def statuses(report):
    return [r.status for r in report.results]


def test_valid_exercise(tmp_path):
    """Reference passes, skeleton fails its tests, the one mutant is killed."""
    report = validate(load_exercise(*make_exercise(tmp_path)))

    assert report.valid
    assert statuses(report) == [Status.OK, Status.OK, Status.OK]
    assert "1/1 killed" in report.results[2].detail


def test_broken_reference_skips_rules_2_and_3(tmp_path):
    """If the reference fails, the other rules would prove nothing, so they are skipped."""
    wrong = "def add(a, b):\n    return a * b\n"
    report = validate(load_exercise(*make_exercise(tmp_path, reference=wrong)))

    assert statuses(report) == [Status.FAIL, Status.SKIPPED, Status.SKIPPED]
    assert not report.valid


def test_skeleton_that_passes_breaks_rule_2(tmp_path):
    """Tests that an unfinished skeleton can pass do not check the student's work."""
    report = validate(load_exercise(*make_exercise(tmp_path, skeleton=REFERENCE)))

    assert report.results[1].status is Status.FAIL
    assert "skeleton passes" in report.results[1].detail


def test_skeleton_that_cannot_import_breaks_rule_2(tmp_path):
    """A skeleton with a syntax error crashes before any test runs: ERROR, not a real failure."""
    broken = "def add(a, b)\n    raise NotImplementedError\n"
    report = validate(load_exercise(*make_exercise(tmp_path, skeleton=broken)))

    assert report.results[1].status is Status.FAIL
    assert "errors before any test runs" in report.results[1].detail


def test_surviving_mutant_is_named(tmp_path):
    """b + a behaves exactly like a + b, so no test can kill it; the report names it."""
    mutants = {"m01_sub": MUTANT_SUB, "m02_swapped": "def add(a, b):\n    return b + a\n"}
    report = validate(load_exercise(*make_exercise(tmp_path, mutants=mutants)))

    rule3 = report.results[2]
    assert rule3.status is Status.FAIL
    assert "1/2 killed" in rule3.detail
    assert "m02_swapped" in rule3.detail


def test_mutant_that_cannot_import_is_reported(tmp_path):
    """A mutant that does not even import tests nothing about the tests."""
    mutants = {"m01_sub": MUTANT_SUB, "m02_typo": "def add(a, b)\n    return a\n"}
    report = validate(load_exercise(*make_exercise(tmp_path, mutants=mutants)))

    assert report.results[2].status is Status.FAIL
    assert "cannot run" in report.results[2].detail
    assert "m02_typo" in report.results[2].detail


def test_no_mutants_breaks_rule_3(tmp_path):
    report = validate(load_exercise(*make_exercise(tmp_path, mutants={})))

    assert report.results[2].status is Status.FAIL
    assert "no mutants" in report.results[2].detail


def test_data_files_are_copied_but_student_and_hints_are_not(tmp_path):
    """Files the tests read (sample data) come along; student.py is always the candidate."""
    tests = (
        "from pathlib import Path\nfrom student import add\n\n"
        "def test_from_file():\n"
        "    a, b, want = map(int, Path('data.txt').read_text().split())\n"
        "    assert add(a, b) == want\n\n"
        "def test_no_hints_copied():\n    assert not Path('hints.md').exists()\n"
    )
    exercise, solution = make_exercise(tmp_path, tests=tests, extra_files={"data.txt": "2 3 5"})
    files = load_exercise(exercise, solution).run_files(solution / "reference.py")

    assert files["student.py"] == solution / "reference.py"
    assert "hints.md" not in files
    assert validate(load_exercise(exercise, solution)).valid


def test_missing_reference_is_a_clear_error(tmp_path):
    exercise, solution = make_exercise(tmp_path)
    (solution / "reference.py").unlink()

    with pytest.raises(ExerciseError, match="reference.py"):
        load_exercise(exercise, solution)


def test_cli_exit_codes(tmp_path, capsys):
    """0 for a valid exercise, 1 for an invalid one, 2 for wrong usage."""
    good = make_exercise(tmp_path / "good")
    bad = make_exercise(tmp_path / "bad", mutants={})

    assert main([str(p) for p in good]) == 0
    assert "VALID" in capsys.readouterr().out
    assert main([str(p) for p in bad]) == 1
    assert main([]) == 2


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.spark
@pytest.mark.parametrize("name", ["station-average-rdd", "station-max-file"])
def test_examples_are_valid(name):
    """End to end on the real examples, with Spark."""
    load_env()
    exercise = load_exercise(EXAMPLES / name / "exercise", EXAMPLES / name / "solution")

    report = validate(exercise)

    assert report.valid, format_report(report)
