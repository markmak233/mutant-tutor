import json
import os
import subprocess
import sys
from pathlib import Path

from tutor.attempts import exercise_of

TESTS = (
    "def test_ok():\n    assert True\n\n"
    "def test_wrong():\n    assert 1 + 1 == 3, 'adds wrong'\n"
)


def make_workspace(tmp_path: Path, mode: str | None = "review") -> Path:
    """A minimal workspace with one batch and one exercise; returns the exercise dir."""
    ws = tmp_path / "ws"
    exercise = ws / "batches" / "2026-10-25_p2-w2" / "exercises" / "word-count"
    exercise.mkdir(parents=True)
    (ws / "workspace.toml").write_text('course = "demo"\n')
    if mode:
        manifest = ws / "batches" / "2026-10-25_p2-w2" / "manifest.json"
        manifest.write_text(json.dumps({"mode": mode}))
    (exercise / "test_student.py").write_text(TESTS)
    return exercise


def run_pytest_in(folder: Path, workspace: Path) -> None:
    """Run pytest the way the owner does, from the exercise folder, with the plugin."""
    env = {**os.environ, "WORKSPACE_DIR": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tutor.attempts", "-p", "no:cacheprovider"],
        cwd=folder, env=env, capture_output=True, timeout=60,
    )


def attempts(workspace: Path) -> list[dict]:
    path = workspace / "attempts" / "attempts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


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
    (exercise / "student.py").write_text("def f(:\n")
    (exercise / "test_student.py").write_text("from student import f\n\ndef test_f():\n    f()\n")
    run_pytest_in(exercise, tmp_path / "ws")

    [line] = attempts(tmp_path / "ws")
    assert line["result"] == "error"
    assert "SyntaxError" in line["first_error"] or "invalid syntax" in line["first_error"]


def test_runs_outside_the_workspace_are_not_recorded(tmp_path):
    """Validator runs happen in temp dirs and must leave no trace."""
    make_workspace(tmp_path)
    elsewhere = tmp_path / "validator-temp"
    elsewhere.mkdir()
    (elsewhere / "test_student.py").write_text(TESTS)
    run_pytest_in(elsewhere, tmp_path / "ws")

    assert not (tmp_path / "ws" / "attempts").exists()
