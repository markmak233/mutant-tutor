"""The shared course-venv boundary uses synthetic data and fixed-command stand-ins."""

import json
import subprocess

import pytest
import test_gui_environment as support

from tutor import course_requirements
from tutor.gui import environment

root = support.root
course = support.course


def test_gui_and_doctor_decode_same_course_interpreter_facts(course, monkeypatch):
    python = support._venv(course)
    calls = []
    monkeypatch.setenv("MODEL_TOKEN", "sk-test-not-a-real-key")
    monkeypatch.setenv("JAVA_HOME", "synthetic-jdk")
    row = {"name": "examplelib", "required": "1", "found": "1.4", "hint": "", "ok": True}

    def run(command, **kwargs):
        calls.append(command)
        assert command[0] == str(python)
        assert "MODEL_TOKEN" not in kwargs["env"]
        assert kwargs["env"]["JAVA_HOME"] == "synthetic-jdk"
        assert kwargs["env"]["WORKSPACE_DIR"] == str(course.workspace.root)
        return subprocess.CompletedProcess(command, 0, json.dumps({"results": [row]}), "")

    cli = course_requirements.check(course.workspace, run=run)
    gui = environment.check(course, run)
    assert cli.results == gui.results == [row] and gui.ok and cli.problem is None
    assert calls[0][-1] == course_requirements.PROBE
    assert calls[1][1:] == ["-m", "tutor.envcheck", "--json"]
    assert "load_env" not in course_requirements.PROBE


@pytest.mark.parametrize("raw", ["not json", '{"results":null}', '{"results":[{}]}'])
def test_malformed_probe_is_not_an_empty_success(course, raw):
    support._venv(course)

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, raw, "synthetic private stderr")

    found = course_requirements.check(course.workspace, run=run)
    assert found.problem and not found.results and "private" not in found.problem


def test_timed_out_course_probe_reports_unavailable_without_fallback(course):
    python = support._venv(course)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    found = course_requirements.check(course.workspace, run=run)
    assert found.problem and len(calls) == 1 and calls[0][0] == str(python)


def test_a_failing_requirement_is_an_answer_on_the_page_not_a_broken_check(course):
    # `python -m tutor.envcheck --json` exits 1 when any row fails, with its rows printed.
    # The page must keep them: the Python switch reads the current version from them.
    support._venv(course)
    row = {"name": "python", "required": "3.12", "found": "3.11.9",
           "hint": "recreate .venv with Python 3.12", "ok": False}

    def run(command, **kwargs):
        code = 1 if command[1:] == ["-m", "tutor.envcheck", "--json"] else 3
        return subprocess.CompletedProcess(command, code, json.dumps({"results": [row]}), "")

    gui = environment.check(course, run)
    assert gui.results == [row] and gui.problem is None and not gui.ok
    # Doctor's probe script exits 0 unless it crashed, so there a non-zero exit stays broken;
    # and the command's exit 1 without rows (workspace.toml did not load) is broken too.
    assert course_requirements.check(course.workspace, run=run).problem

    def failed(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "workspace: synthetic")

    assert environment.check(course, failed).problem
