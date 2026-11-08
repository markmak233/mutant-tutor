"""Characterize host pytest recording so the shared writer preserves existing attempts."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tutor.attempts import exercise_of
from tutor.testkit_c import host_problem

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

TESTS = (
    "def test_ok():\n    assert True\n\n"
    "def test_wrong():\n    assert 1 + 1 == 3, 'adds wrong'\n"
)


def make_workspace(tmp_path: Path, mode: str | None = "review") -> Path:
    """A minimal workspace with one batch and one exercise; returns the exercise dir."""
    ws = tmp_path / "ws"
    exercise = ws / "batches" / "2026-10-25_p2-w2" / "exercises" / "word-count"
    exercise.mkdir(parents=True)
    (ws / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    if mode:
        manifest = ws / "batches" / "2026-10-25_p2-w2" / "manifest.json"
        manifest.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    (exercise / "test_student.py").write_text(TESTS, encoding="utf-8")
    return exercise


def run_pytest_in(folder: Path, workspace: Path) -> subprocess.CompletedProcess:
    """Run pytest the way the owner does, from the exercise folder, with the plugin."""
    env = {**os.environ, "WORKSPACE_DIR": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tutor.attempts", "-p", "no:cacheprovider"],
        cwd=folder, env=env, capture_output=True, text=True, timeout=60,
    )


def attempts(workspace: Path) -> list[dict]:
    path = workspace / "attempts" / "attempts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_exercise_of_recognises_only_batch_exercises(tmp_path):
    ws = tmp_path / "ws"
    assert exercise_of(ws / "batches/b1/exercises/e1/test_student.py", ws) == ("b1", "e1")
    assert exercise_of(ws / "batches/b1/solutions/e1/test_oracle.py", ws) is None
    assert exercise_of(tmp_path / "elsewhere/test_student.py", ws) is None


def test_a_run_in_an_exercise_folder_is_recorded(tmp_path):
    """One line with the batch, exercise, mode from the manifest and the first error."""
    exercise = make_workspace(tmp_path)
    run_pytest_in(exercise, tmp_path / "ws")

    [line] = attempts(tmp_path / "ws")
    assert line["batch"] == "2026-10-25_p2-w2"
    assert line["exercise"] == "word-count"
    assert line["mode"] == "review"
    assert (line["result"], line["passed"], line["failed"], line["errors"]) == ("failed", 1, 1, 0)
    assert line["first_error"].startswith("test_wrong: AssertionError: adds wrong")


def test_the_first_error_is_stored_without_terminal_colours():
    from types import SimpleNamespace

    from tutor.attempt_records import short_error
    coloured = "AssertionError: assert ['\x1b[33m(age + 10)\x1b[39;49;00m'] == ['age10']"
    report = SimpleNamespace(nodeid="test_student.py::test_columns", longrepr=SimpleNamespace(
        reprcrash=SimpleNamespace(message=coloured)))
    assert short_error(report) == (
        "test_columns: AssertionError: assert ['(age + 10)'] == ['age10']")


def test_a_failing_run_keeps_its_student_py_once_and_a_passing_run_none(tmp_path):
    exercise = make_workspace(tmp_path)
    (exercise / "student.py").write_text("def add(a, b):\n    return a * b\n")
    run_pytest_in(exercise, tmp_path / "ws")
    run_pytest_in(exercise, tmp_path / "ws")  # the same mistake again
    (exercise / "test_student.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    run_pytest_in(exercise, tmp_path / "ws")

    first, again, passing = attempts(tmp_path / "ws")
    assert first["code"] == again["code"]
    assert first["code"].startswith("code/2026-10-25_p2-w2/word-count/")
    kept = tmp_path / "ws" / "attempts" / first["code"]
    assert kept.read_text(encoding="utf-8") == "def add(a, b):\n    return a * b\n"
    assert len(list(kept.parent.iterdir())) == 1
    assert "code" not in passing


def test_a_c_exercise_keeps_its_failing_student_c_as_c(tmp_path):
    """The snapshot is the file the learner writes in, with its suffix, so the held-out
    check can run it as C again."""
    exercise = make_workspace(tmp_path)
    (exercise / "student.c").write_text("int add(int a, int b) { return a * b; }\n")
    run_pytest_in(exercise, tmp_path / "ws")

    [line] = attempts(tmp_path / "ws")
    assert line["code"].endswith(".c")
    kept = tmp_path / "ws" / "attempts" / line["code"]
    assert kept.read_text(encoding="utf-8") == "int add(int a, int b) { return a * b; }\n"


@pytest.mark.skipif(host_problem() is not None, reason=str(host_problem()))
def test_a_learners_own_c_run_is_recorded(tmp_path):
    """The learner's own run of a C exercise, on their machine: the C example handed out as
    its skeleton compiles, fails its tests, and is logged with its student.c kept."""
    exercise = make_workspace(tmp_path)
    example = EXAMPLES / "station-drop-low-c"
    shutil.copytree(example / "exercise", exercise, dirs_exist_ok=True)
    shutil.copy(example / "solution" / "skeleton.c", exercise / "student.c")
    run_pytest_in(exercise, tmp_path / "ws")

    [line] = attempts(tmp_path / "ws")
    assert (line["result"], line["errors"]) == ("failed", 0) and line["failed"] > 0
    kept = tmp_path / "ws" / "attempts" / line["code"]
    assert kept.suffix == ".c"
    assert kept.read_bytes() == (example / "solution" / "skeleton.c").read_bytes()


def test_every_run_appends_a_line(tmp_path):
    exercise = make_workspace(tmp_path, mode=None)
    run_pytest_in(exercise, tmp_path / "ws")
    run_pytest_in(exercise, tmp_path / "ws")

    lines = attempts(tmp_path / "ws")
    assert len(lines) == 2
    assert lines[0]["mode"] is None  # no manifest yet


def test_a_broken_student_file_is_an_error(tmp_path):
    """A syntax error stops the test file from importing: result "error", not "failed"."""
    exercise = make_workspace(tmp_path)
    (exercise / "student.py").write_text("def f(:\n", encoding="utf-8")
    (exercise / "test_student.py").write_text("from student import f\n\ndef test_f():\n    f()\n",
                                              encoding="utf-8")
    run_pytest_in(exercise, tmp_path / "ws")

    [line] = attempts(tmp_path / "ws")
    assert line["result"] == "error"
    assert "SyntaxError" in line["first_error"] or "invalid syntax" in line["first_error"]


def test_runs_outside_the_workspace_are_not_recorded(tmp_path):
    """Validator runs happen in temp dirs and must leave no trace."""
    make_workspace(tmp_path)
    elsewhere = tmp_path / "validator-temp"
    elsewhere.mkdir()
    (elsewhere / "test_student.py").write_text(TESTS, encoding="utf-8")
    run_pytest_in(elsewhere, tmp_path / "ws")

    assert not (tmp_path / "ws" / "attempts").exists()
    assert not (tmp_path / "ws" / "reports").exists()


def test_each_recorded_run_brings_the_report_up_to_date(tmp_path):
    """Failing, then passing: the report follows, and holds no first error, which on a
    hand-calculation exercise can be the answer."""
    exercise = make_workspace(tmp_path)
    manifest = tmp_path / "ws" / "batches" / "2026-10-25_p2-w2" / "manifest.json"
    manifest.write_text(json.dumps({"exercises": ["word-count"]}), encoding="utf-8")
    report = tmp_path / "ws" / "reports" / "report.md"

    run_pytest_in(exercise, tmp_path / "ws")
    # Exercises, Skip, Passed, Started, then the other columns.
    assert "| 2026-10-25_p2-w2 | 1 | 0 | 0 | 1 |" in report.read_text(encoding="utf-8")
    assert "adds wrong" not in report.read_text(encoding="utf-8")

    (exercise / "test_student.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    run_pytest_in(exercise, tmp_path / "ws")
    assert "| 2026-10-25_p2-w2 | 1 | 0 | 1 | 0 |" in report.read_text(encoding="utf-8")


def test_a_report_that_cannot_be_built_costs_only_a_warning(tmp_path):
    """A file where the report's folder should be: the run keeps its result and its log line."""
    exercise = make_workspace(tmp_path)
    (exercise / "test_student.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "ws" / "reports").write_text("in the way", encoding="utf-8")

    run = run_pytest_in(exercise, tmp_path / "ws")

    assert run.returncode == 0
    assert "report not updated" in run.stdout
    [line] = attempts(tmp_path / "ws")
    assert line["result"] == "passed"


def test_a_run_with_a_skipped_test_is_incomplete_not_passed(tmp_path):
    exercise = make_workspace(tmp_path)
    (exercise / "test_student.py").write_text(
        "import pytest\n\ndef test_ok():\n    assert True\n\n"
        "@pytest.mark.skip\ndef test_later():\n    pass\n", encoding="utf-8")
    run_pytest_in(exercise, tmp_path / "ws")
    [line] = attempts(tmp_path / "ws")
    assert (line["result"], line["passed"], line["skipped"]) == ("incomplete", 1, 1)


def test_a_run_where_every_test_is_skipped_is_still_recorded(tmp_path):
    exercise = make_workspace(tmp_path)
    (exercise / "test_student.py").write_text(
        "import pytest\n\ndef test_a():\n    pytest.skip('no Hadoop here')\n", encoding="utf-8")
    run_pytest_in(exercise, tmp_path / "ws")
    [line] = attempts(tmp_path / "ws")
    assert (line["result"], line["skipped"]) == ("incomplete", 1)


def test_an_interrupted_run_is_not_recorded_as_passed(tmp_path):
    """Ctrl+C after some tests passed: pytest ends with exit status 2 (interrupted)."""
    exercise = make_workspace(tmp_path)
    (exercise / "test_student.py").write_text(
        "def test_ok():\n    assert True\n\n"
        "def test_stopped():\n    raise KeyboardInterrupt\n")
    run_pytest_in(exercise, tmp_path / "ws")
    [line] = attempts(tmp_path / "ws")
    assert (line["result"], line["passed"]) == ("interrupted", 1)
