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
