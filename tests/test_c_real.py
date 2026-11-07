"""C exercises in real containers, the only place they are validated: the examples are valid
there with a course's own flags, and what only Linux has (LeakSanitizer) works under the
container's limits.

Needs Docker running, so plain `pytest` leaves these out: `pytest -m docker`. The first run
builds an image with gcc (about a minute) and later runs reuse it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tutor.config import Workspace
from tutor.container import build, docker_problem
from tutor.execution import Outcome, run_pytest
from tutor.validation import load_exercise, validate
from tutor.validation.__main__ import format_report

pytestmark = pytest.mark.docker

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REQUIRES = {"python": "3.11", "gcc": "12"}


@pytest.fixture(scope="session")
def course(tmp_path_factory) -> Path:
    if problem := docker_problem():
        pytest.skip(problem)
    root = tmp_path_factory.mktemp("c-course")
    (root / "workspace.toml").write_text(
        'course = "dockertest-c"\n[requires]\npython = "3.11"\ngcc = "12"\n'
        '[isolation]\nrunner = "container"\n[languages.c]\nflags = ["-Wall", "-std=c99"]\n',
        encoding="utf-8")
    build(Workspace(root=root, course="dockertest-c", requires=REQUIRES, lecture_dir=root / "l"),
          say=lambda text: None)
    return root


def containers_left() -> str:
    return subprocess.run(["docker", "ps", "-aq", "--filter", "name=tutor-run-"],
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("name", ["station-above-average-c", "station-drop-low-c"])
def test_the_c_examples_are_valid_in_the_container(course, name):
    root = course / name
    shutil.copytree(EXAMPLES / name, root, ignore=shutil.ignore_patterns("validation.json"))

    report = validate(load_exercise(root / "exercise", root / "solution"))

    assert report.valid, format_report(report)
    assert report.result("3").detail == "3/3 killed"
    assert containers_left() == ""


def test_a_leak_fails_a_run_that_asks_for_the_check(course):
    """LeakSanitizer stops the world with ptrace, which the container's limits (no
    capabilities, no new privileges) leave working (measured 2026-11-07)."""
    folder = course / "leak"
    folder.mkdir()
    (folder / "student.c").write_text(
        "#include <stdlib.h>\n#include <stdio.h>\n"
        "int main(void) { int *p = malloc(64); p[0] = 7; printf(\"%d\\n\", p[0]); return 0; }\n",
        encoding="utf-8")
    (folder / "test_student.py").write_text(
        "from tutor.testkit_c import build\n\nprogram = build('student.c')\n\n\n"
        "def test_runs():\n    assert program.run().lines() == ['7']\n\n\n"
        "def test_frees_what_it_allocates():\n    program.run(leaks=True)\n", encoding="utf-8")

    result = run_pytest({name: folder / name for name in ("student.c", "test_student.py")},
                        timeout_s=60)

    assert result.outcome is Outcome.FAILED
    assert (result.counts.passed, result.counts.failed) == (1, 1)
    assert "LeakSanitizer: detected memory leaks" in result.output
