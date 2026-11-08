"""Real C/Spark learner runs match the original pytest plugin in a course image.

TUTOR_TEST_HOST_PARITY=1 additionally checks an equipped host using isolated synthetic
baseline courses. No real course, solution, lecture or model is consulted.
"""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from tutor import execution
from tutor.config import Workspace
from tutor.container import build, docker_problem
from tutor.test_command import main

pytestmark = pytest.mark.docker

SOURCES = {
    "c": '#include <stdio.h>\nint main(void) { puts("VALUE"); return 0; }\n',
    "spark": "def total(sc):\n    return sc.parallelize([1, 2], 2).sum() + EXTRA\n",
}
TESTS = {
    "c": ('from tutor.testkit_c import build\nprogram = build("student.c")\n'
          'def test_total():\n    assert program.run().lines() == ["3"]\n'),
    "spark": (
        "import pytest\nfrom pyspark import SparkConf, SparkContext\n"
        "from student import total\n"
        "@pytest.fixture\ndef sc():\n"
        "    conf = SparkConf().setMaster('local[2]').setAppName('practice-parity')\n"
        "    conf.set('spark.ui.enabled', 'false')\n"
        "    context = SparkContext(conf=conf)\n"
        "    yield context\n    context.stop()\n"
        "def test_total(sc):\n    assert total(sc) == 3\n"),
}


@pytest.fixture(scope="module", params=("c", "spark"))
def course(request, tmp_path_factory):
    """Build explicit synthetic images; the command itself never auto-builds one."""
    if problem := docker_problem():
        pytest.skip(problem)
    kind = request.param
    root = tmp_path_factory.mktemp(f"learner-{kind}")
    requires = {"python": "3.11", **({"gcc": "12"} if kind == "c" else
                                    {"java": "17", "pyspark": "3.5"})}
    config = (f'course = "practice-{kind}"\n[requires]\n'
              + "".join(f'{name} = "{value}"\n' for name, value in requires.items())
              + '[isolation]\nrunner = "container"\n'
              + ('[languages.c]\nflags = ["-std=c99", "-Wall"]\n' if kind == "c" else ""))
    (root / "workspace.toml").write_text(config, encoding="utf-8")
    build(Workspace(root, f"practice-{kind}", requires, root / "lectures"), say=lambda text: None)
    return root, kind


def baseline(root, kind, exercise, monkeypatch, *, host=False):
    """Stage the original plugin's path layout, then inspect its output before cleanup."""
    holder = root.parent / f"{root.name}-host-baseline" if host else root / "image-baseline"
    config = (root / "workspace.toml").read_text(encoding="utf-8")
    if host:
        config = config.replace('runner = "container"', 'runner = "host"')
        # This is a separate synthetic host course, never a change to the image course.
        holder.mkdir(exist_ok=True)
        (holder / "workspace.toml").write_text(config, encoding="utf-8")
    staged = holder / "copy"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "lectures").mkdir(exist_ok=True)
    (staged / "workspace.toml").write_text(config, encoding="utf-8")
    destination = staged / "batches" / "b" / "exercises" / "e"
    shutil.copytree(exercise, destination, dirs_exist_ok=True)
    (destination.parents[1] / "manifest.json").write_text('{"mode":"learn"}', encoding="utf-8")
    observations = []
    original = execution._run_with_env

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        folder = args[1] / "baseline" / "attempts"
        [line] = [json.loads(row) for row in (folder / "attempts.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        code = (folder / line["code"]).read_bytes() if "code" in line else None
        observations.append((line, code))
        return result

    # The relocated workspace name is the only destination passed to the baseline plugin.
    # On the host the run's temp path is unknown until _run_in creates it.
    variables = {"PYTEST_ADDOPTS": "-p tutor.attempts", "TUTOR_PARTITIONS": "2"}
    if not host:
        variables["WORKSPACE_DIR"] = "/work/baseline"
        variables["LECTURE_DIR"] = "/work/baseline/lectures"
    with monkeypatch.context() as patch:
        patch.setattr(execution, "_run_with_env", capture)
        if host:
            run_in = execution._run_in

            def host_run(workdir, timeout, env, *args, **kwargs):
                return run_in(workdir, timeout, {**env, "WORKSPACE_DIR": str(workdir / "baseline"),
                                                "LECTURE_DIR": str(workdir / "baseline/lectures")},
                              *args, **kwargs)

            patch.setattr(execution, "_run_in", host_run)
        result = execution.run_pytest({"baseline": staged}, 300, variables)
    return result, observations[0]


@pytest.mark.parametrize("passing", (True, False))
def test_learner_matches_original_plugin_and_keeps_answers_unchanged(course, passing, monkeypatch):
    root, kind = course
    exercise = root / "batches" / "b" / "exercises" / "e"
    exercise.mkdir(parents=True, exist_ok=True)
    suffix = ".c" if kind == "c" else ".py"
    source = SOURCES[kind].replace("VALUE", "3" if passing else "4")
    source = source.replace("EXTRA", "0" if passing else "1")
    (exercise / f"student{suffix}").write_text(source, encoding="utf-8")
    (exercise / "test_student.py").write_text(TESTS[kind], encoding="utf-8")
    (exercise.parents[1] / "manifest.json").write_text('{"mode":"learn"}', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in exercise.iterdir()}
    expected, (old, old_code) = baseline(root, kind, exercise, monkeypatch)
    assert expected.returncode == (0 if passing else 1)
    assert main([str(exercise)]) == expected.returncode
    log = root / "attempts" / "attempts.jsonl"
    new = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    compared = set(old) - {"time", "duration_s"}
    assert {k: new[k] for k in compared} == {k: old[k] for k in compared}
    if not passing:
        assert (root / "attempts" / new["code"]).read_bytes() == old_code == source.encode("utf-8")
    assert before == {p.name: p.read_bytes() for p in exercise.iterdir()}
    if os.environ.get("TUTOR_TEST_HOST_PARITY") == "1":
        actual, (host_line, host_code) = baseline(root, kind, exercise, monkeypatch, host=True)
        assert actual.returncode == expected.returncode
        assert {k: host_line[k] for k in compared} == {k: new[k] for k in compared}
        assert host_code == old_code


@pytest.mark.parametrize("entry", ("module", "installed"))
def test_entrypoints_run_the_scratch_exercise_in_its_image(course, entry):
    """Launch trusted CLI code; that CLI alone sends the synthetic answer to the runner."""
    root, kind = course
    exercise = root / "batches" / "b" / "exercises" / f"entry-{entry}"
    exercise.mkdir(parents=True)
    suffix = ".c" if kind == "c" else ".py"
    source = SOURCES[kind].replace("VALUE", "3").replace("EXTRA", "0")
    (exercise / f"student{suffix}").write_text(source, encoding="utf-8")
    (exercise / "test_student.py").write_text(TESTS[kind], encoding="utf-8")
    if entry == "module":
        command = [sys.executable, "-m", "tutor", "test"]
    else:
        script = Path(sysconfig.get_path("scripts")) / ("tutor.exe" if os.name == "nt" else "tutor")
        command = [str(script), "test"]
    result = subprocess.run(command, cwd=exercise, capture_output=True, encoding="utf-8",
                            timeout=360)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Attempt: passed" in result.stdout
    assert (exercise / f"student{suffix}").read_text(encoding="utf-8") == source


def test_learner_timeout_keeps_partial_progress_and_removes_its_container(course, monkeypatch):
    """A real silent container must not outlive the learner command's deadline."""
    root, kind = course
    exercise = root / "batches" / "b" / "exercises" / "slow"
    exercise.mkdir(parents=True)
    suffix = ".c" if kind == "c" else ".py"
    (exercise / f"student{suffix}").write_text("/* unused */" if kind == "c" else "# unused",
                                              encoding="utf-8")
    (exercise / "test_student.py").write_text(
        "import time\ndef test_first():\n    assert True\n"
        "def test_wait():\n    time.sleep(60)\n", encoding="utf-8")
    names = []
    original = execution.run_command

    def command(*args, **kwargs):
        names.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, "run_command", command)
    assert main([str(exercise), "--timeout", "5"]) == 124
    log = root / "attempts" / "attempts.jsonl"
    last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert last["result"] == "interrupted" and last["passed"] == 1
    assert last["stop_reason"] == "timeout" and "code" not in last
    # Look only for this test's container; another owner session may have its own runs.
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^{names[0]}$"],
                          capture_output=True, encoding="utf-8", timeout=30)
    assert left.returncode == 0 and not left.stdout.strip()
