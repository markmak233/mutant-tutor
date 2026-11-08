"""Learner CLI behavior with synthetic courses and content-based runner stand-ins."""

import json

import pytest

from tutor.attempt_records import Tally
from tutor.attempt_transport import AttemptEvents, LearnerData
from tutor.container import ContainerError
from tutor.execution import Outcome, RunResult
from tutor.test_command import main


def course(tmp_path):
    """The learner path never requires a solutions directory or any model settings."""
    exercise = tmp_path / "batches" / "b" / "exercises" / "e"
    exercise.mkdir(parents=True)
    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[requires]\npython = "3.11"\n'
        '[isolation]\nrunner = "container"\n', encoding="utf-8")
    (exercise / "student.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    (exercise / "test_student.py").write_text("def test_it():\n    assert False\n",
                                              encoding="utf-8")
    return exercise


def lines(root):
    path = root / "attempts" / "attempts.jsonl"
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()]


def failed(files, timeout, env, *, learner, on_output):
    """Choose the result from the tested input, never a threaded caller's request order."""
    assert files[learner.student_file].read_bytes().endswith(b"return 0\n")
    assert timeout == 12 and env == {"TUTOR_PARTITIONS": "2"}
    assert learner.selection == "example" and learner.verbosity == 2
    on_output("live progress")
    return RunResult(Outcome.FAILED, 1, 0.2, "live progress", learner=LearnerData(
        files[learner.student_file].read_bytes(), ".py",
        AttemptEvents(Tally(passed=1, failed=1, first_error="test_it: wrong"), 1)))


def test_options_and_current_directory_record_one_host_attempt(tmp_path, monkeypatch, capsys):
    exercise = course(tmp_path)
    # Even a wrong exported workspace must not receive this exercise's attempt.
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "other"))
    monkeypatch.chdir(exercise)
    assert main(["-k", "example", "-vv", "--timeout", "12"], run_tests=failed) == 1
    [line] = lines(tmp_path)
    assert line["result"] == "failed" and line["selection"] == "example"
    snapshot = tmp_path / "attempts" / line["code"]
    assert snapshot.read_bytes() == (exercise / "student.py").read_bytes()
    assert "live progress" in capsys.readouterr().out


@pytest.mark.parametrize("reason, code", [("timeout", 124), ("keyboard_interrupt", 130)])
def test_interruption_records_partial_counts_without_a_passing_attempt(tmp_path, reason, code):
    exercise = course(tmp_path)

    def run(files, *args, **kwargs):
        return RunResult(Outcome.TIMEOUT, None, 0.1, "", stop_reason=reason,
                         learner=LearnerData(b"input", ".py", AttemptEvents(Tally(passed=1))))

    assert main([str(exercise)], run_tests=run) == code
    [line] = lines(tmp_path)
    assert line["result"] == "interrupted" and line["passed"] == 1
    assert line["stop_reason"] == reason and "code" not in line


def test_missing_docker_leaves_answers_and_attempts_untouched(tmp_path, capsys):
    exercise = course(tmp_path)

    def unavailable(*args, **kwargs):
        raise ContainerError("Docker is not running")

    before = (exercise / "student.py").read_bytes()
    assert main([str(exercise)], run_tests=unavailable) == 2
    assert "Docker is not running" in capsys.readouterr().err
    assert not (tmp_path / "attempts").exists()
    assert (exercise / "student.py").read_bytes() == before


@pytest.mark.parametrize("result, tally, exitstatus", [
    ("passed", Tally(passed=1), 0),
    ("incomplete", Tally(passed=1, skipped=1), 0),
    ("error", Tally(passed=1, errors=1, first_error="test_it: teardown"), 1),
])
def test_recording_preserves_host_hook_semantics(tmp_path, result, tally, exitstatus):
    exercise = course(tmp_path)

    def run(files, *args, **kwargs):
        return RunResult(Outcome.ERROR, exitstatus, 0.1, "", learner=LearnerData(
            b"input", ".py", AttemptEvents(tally, exitstatus)))

    assert main([str(exercise)], run_tests=run) == exitstatus
    [line] = lines(tmp_path)
    assert line["result"] == result
    assert line["passed"] == tally.passed and line["errors"] == tally.errors


def test_zero_selected_tests_is_not_recorded(tmp_path):
    exercise = course(tmp_path)

    def run(files, *args, **kwargs):
        return RunResult(Outcome.ERROR, 5, 0.1, "", learner=LearnerData(
            b"input", ".py", AttemptEvents(Tally(), 5)))

    assert main([str(exercise)], run_tests=run) == 5
    assert not (tmp_path / "attempts").exists()


def test_nested_configuration_and_links_are_refused_before_execution(tmp_path, symlink):
    exercise = course(tmp_path)
    data = exercise / "sample_input"
    data.mkdir()
    (data / "pytest.ini").write_text("[pytest]", encoding="utf-8")
    assert main([str(exercise)], run_tests=lambda *a, **k: pytest.fail("executed")) == 2
    (data / "pytest.ini").unlink()
    symlink(data / "linked.py", exercise / "student.py")
    assert main([str(exercise)], run_tests=lambda *a, **k: pytest.fail("executed")) == 2


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_deadlines_are_rejected(tmp_path, timeout):
    with pytest.raises(SystemExit) as error:
        main([str(tmp_path), "--timeout", timeout])
    assert error.value.code == 2
