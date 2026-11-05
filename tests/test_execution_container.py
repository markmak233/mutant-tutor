"""A course's runs in a container, with a stand-in `docker` program on PATH: no Docker needed.

What these cover is the runner's side: which runs go to a container, what the command gives
away, and what happens when Docker fails. tests/test_container_real.py checks real containers.
"""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from tutor import container, execution
from tutor.container import ContainerError, check_course, image_for, image_name
from tutor.execution import Outcome, _read_counts, run_pytest, run_script

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in is a POSIX script")

REPORT = ('<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0"></testsuite>'
          "</testsuites>")
PLAIN = {"python": "3.11"}
CONTAINER_TOML = 'course = "demo"\n[requires]\npython = "3.11"\n[isolation]\nrunner = "container"\n'

# Each call is logged. `run` does what FAKE_DOCKER_MODE says, and writes the report in the
# folder mounted at /work, as pytest would there.
FAKE_DOCKER = """\
#!{python}
import json, os, pathlib, sys, time
args = sys.argv[1:]
mode = os.environ.get("FAKE_DOCKER_MODE", "pass")
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["image", "inspect"]:
    sys.exit(1 if mode in ("no-image", "down") else 0)
if args[0] == "info":
    print("" if mode == "down" else "2" if "NCPU" in args[-1] else "24.0.6")
    sys.exit(0)
if args[0] == "run":
    work = next(a.split(":/work")[0] for a in args if a.endswith(":/work"))
    report = pathlib.Path(work, "tutor-pytest-report.xml")
    if mode == "docker-failed":
        print("docker: Error response from daemon: mounts denied")
        sys.exit(125)
    if mode == "killed":
        sys.exit(137)
    if mode == "hang":
        time.sleep(60)
    if mode == "link":
        pathlib.Path(work, "real.xml").write_text({report!r})
        report.symlink_to("real.xml")
        sys.exit(0)
    report.write_text({report!r})
    print("ran in a container")
"""


class Docker:
    """Puts the stand-in first on PATH; `commands` are the calls it logged, in order."""

    def __init__(self, tmp_path, monkeypatch):
        folder = tmp_path / "fake-bin"
        folder.mkdir()
        program = folder / "docker"
        program.write_text(FAKE_DOCKER.format(python=sys.executable, report=REPORT),
                           encoding="utf-8")
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        self.log = tmp_path / "docker.log"
        self.log.write_text("", encoding="utf-8")
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(self.log))
        monkeypatch.delenv("FAKE_DOCKER_MODE", raising=False)
        monkeypatch.setattr(container, "_warned_host", False)
        container._cpu_limit.cache_clear()

    def mode(self, mode: str) -> None:
        self._monkeypatch.setenv("FAKE_DOCKER_MODE", mode)

    @property
    def commands(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def calls(self, verb: str) -> list[list[str]]:
        return [c for c in self.commands if c[0] == verb]


@pytest.fixture
def docker(tmp_path, monkeypatch):
    yield Docker(tmp_path, monkeypatch)
    container._cpu_limit.cache_clear()


def course_files(root: Path, toml: str | None = CONTAINER_TOML) -> dict[str, Path]:
    """A course folder with one exercise; the mapping run_pytest takes."""
    exercise = root / "course" / "batches" / "b" / "exercises" / "e"
    exercise.mkdir(parents=True)
    if toml is not None:
        (root / "course" / "workspace.toml").write_text(toml, encoding="utf-8")
    (exercise / "student.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (exercise / "test_student.py").write_text(textwrap.dedent("""\
        from student import f
        def test_f():
            assert f() == 1
        """), encoding="utf-8")
    return {"student.py": exercise / "student.py", "test_student.py": exercise / "test_student.py"}


def test_a_course_on_the_container_runs_there_and_gets_the_same_result(tmp_path, docker):
    result = run_pytest(course_files(tmp_path), timeout_s=30)
    assert result.outcome is Outcome.PASSED
    assert result.counts.passed == 2 and "ran in a container" in result.output
    run = docker.calls("run")[0]
    assert image_name("demo", PLAIN) in run
    joined = " ".join(run)
    for flag in ("--rm", "--pull never", "--network none", "--read-only", "--cap-drop ALL",
                 "--hostname localhost", "--memory 2g", "--init"):
        assert flag in joined
    assert " timeout -s KILL 35 python -m pytest -q " in joined  # ends even with no one to kill it


def test_the_run_gives_the_container_none_of_this_machines_environment(tmp_path, docker,
                                                                        monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("WORKSPACE_DIR", "/a/private/place")
    run_pytest(course_files(tmp_path), env={"TUTOR_PARTITIONS": "4"})
    run = docker.calls("run")[0]
    joined = " ".join(run)
    assert "sk-secret-value" not in joined and "/a/private/place" not in joined
    # No `-e NAME` alone either, which would pass the host's value on.
    values = [run[i + 1] for i, flag in enumerate(run) if flag == "-e"]
    assert values == ["TUTOR_PARTITIONS=4"]
    assert not any(flag in run for flag in ("--env-file", "--env"))


def test_only_the_run_folder_can_be_written_from_inside(tmp_path, docker):
    files = course_files(tmp_path)
    run_pytest(files)
    run = docker.calls("run")[0]
    mounts = [run[i + 1] for i, flag in enumerate(run) if flag == "-v"]
    writable = [m for m in mounts if not m.endswith(":ro")]
    assert len(mounts) == 2 and len(writable) == 1 and writable[0].endswith(":/work")
    # The course's own folder is not mounted: only the throwaway copy is.
    assert str(tmp_path / "course") not in " ".join(mounts)


def test_cpus_never_pass_what_the_vm_has(tmp_path, docker):
    run_pytest(course_files(tmp_path))  # the stand-in's VM has 2
    run = docker.calls("run")[0]
    assert run[run.index("--cpus") + 1] == "2"


def test_a_script_runs_there_too(tmp_path, docker):
    result = run_script(course_files(tmp_path), "student.py")
    assert result.outcome is Outcome.PASSED
    assert docker.calls("run")[0][-2:] == ["python", "student.py"]


def test_no_workspace_stays_on_the_host_without_a_word(tmp_path, docker, capsys):
    result = run_pytest(course_files(tmp_path, toml=None))
    assert result.outcome is Outcome.PASSED
    assert docker.commands == [] and "warning" not in capsys.readouterr().err


def test_a_course_that_never_chose_stays_on_the_host_and_is_warned_once(tmp_path, docker, capsys):
    files = course_files(tmp_path, toml='course = "demo"\n')
    assert run_pytest(files).outcome is Outcome.PASSED
    assert run_pytest(files).outcome is Outcome.PASSED
    assert docker.commands == []
    assert capsys.readouterr().err.count("your rights") == 1


def test_a_course_that_chose_the_host_is_not_warned_again(tmp_path, docker, capsys):
    files = course_files(tmp_path, toml='course = "demo"\n[isolation]\nrunner = "host"\n')
    assert run_pytest(files).outcome is Outcome.PASSED
    assert docker.commands == [] and "warning" not in capsys.readouterr().err


def test_a_mistyped_runner_stops_rather_than_means_the_host(tmp_path, docker):
    files = course_files(tmp_path, toml='course = "demo"\n[isolation]\nrunner = "conatiner"\n')
    with pytest.raises(ContainerError, match="conatiner"):
        run_pytest(files)
    assert docker.commands == []


def test_a_broken_workspace_toml_stops_rather_than_means_the_host(tmp_path, docker):
    with pytest.raises(ContainerError, match="workspace.toml"):
        run_pytest(course_files(tmp_path, toml="course = \n"))


def test_a_folder_inside_the_course_cannot_switch_the_container_off(tmp_path):
    files = course_files(tmp_path)
    exercise = files["student.py"].parent
    (exercise / "workspace.toml").write_text('course = "x"\n[isolation]\nrunner = "host"\n',
                                             encoding="utf-8")
    assert image_for(files.values()) == image_name("demo", PLAIN)


def test_a_student_file_kept_elsewhere_still_runs_in_the_courses_container(tmp_path):
    files = course_files(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "student.py"
    elsewhere.parent.mkdir()
    elsewhere.write_text("", encoding="utf-8")
    assert image_for({**files, "student.py": elsewhere}.values()) == image_name("demo", PLAIN)


def test_docker_stopped_stops_the_run_with_the_reason_and_no_result(tmp_path, docker):
    docker.mode("down")
    with pytest.raises(ContainerError, match="not running"):
        run_pytest(course_files(tmp_path))
    assert docker.calls("run") == []


def test_a_missing_image_says_how_to_build_it(tmp_path, docker):
    docker.mode("no-image")
    with pytest.raises(ContainerError, match="tutor.container build"):
        run_pytest(course_files(tmp_path))
    assert docker.calls("run") == []


def test_dockers_own_failure_is_not_reported_as_a_failed_run(tmp_path, docker):
    docker.mode("docker-failed")
    with pytest.raises(ContainerError, match="mounts denied"):
        run_pytest(course_files(tmp_path))


def test_a_killed_run_is_an_error_that_says_why(tmp_path, docker):
    docker.mode("killed")
    result = run_pytest(course_files(tmp_path))
    assert result.outcome is Outcome.ERROR and result.returncode == 137
    assert "memory limit" in result.output


def test_a_timeout_kills_the_container_by_name(tmp_path, docker):
    docker.mode("hang")
    result = run_pytest(course_files(tmp_path), timeout_s=1)
    assert result.outcome is Outcome.TIMEOUT and result.returncode is None
    run = docker.calls("run")[0]
    assert docker.calls("kill") == [["kill", run[run.index("--name") + 1]]]


def test_ctrl_c_while_running_kills_the_container(tmp_path, docker, monkeypatch):
    docker.mode("hang")
    communicate = execution.subprocess.Popen.communicate

    def interrupted(self, *args, **kwargs):
        if self.args[1] == "run":  # only while waiting for the run: the other calls must work
            raise KeyboardInterrupt
        return communicate(self, *args, **kwargs)

    monkeypatch.setattr(execution.subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_pytest(course_files(tmp_path), timeout_s=30)
    run = docker.calls("run")[0]
    assert docker.calls("kill") == [["kill", run[run.index("--name") + 1]]]


def test_a_finished_run_needs_no_kill(tmp_path, docker):
    run_pytest(course_files(tmp_path))
    assert docker.calls("kill") == []


def test_a_report_left_as_a_link_does_not_count(tmp_path, docker):
    docker.mode("link")
    result = run_pytest(course_files(tmp_path))
    # Exit 0 and a readable file behind the link: the host must not follow it.
    assert result.outcome is Outcome.ERROR and result.counts is None


def test_only_an_ordinary_report_of_a_sane_size_is_read(tmp_path, monkeypatch):
    good = tmp_path / "good.xml"
    good.write_text(REPORT, encoding="utf-8")
    assert _read_counts(good).passed == 2
    link = tmp_path / "link.xml"
    link.symlink_to(good)
    assert _read_counts(link) is None
    fifo = tmp_path / "fifo.xml"
    os.mkfifo(fifo)
    assert _read_counts(fifo) is None  # returning at all is the point: reading one would hang
    monkeypatch.setattr(execution, "_MAX_REPORT_BYTES", 10)
    assert _read_counts(good) is None


def test_a_command_about_to_spend_is_stopped_first(tmp_path, docker):
    course_files(tmp_path)
    root = tmp_path / "course"
    check_course(root)  # Docker up, image there
    docker.mode("down")
    with pytest.raises(ContainerError, match="not running"):
        check_course(root)


def test_generation_stops_before_any_llm_call(tmp_path, docker, monkeypatch, capsys):
    pytest.importorskip("langgraph")
    from tutor.graph.__main__ import main

    course_files(tmp_path)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "course"))
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setattr("tutor.graph.__main__._item",
                        lambda *a: pytest.fail("reached generation with no container"))
    docker.mode("down")
    item = tmp_path / "item.json"
    item.write_text("{}", encoding="utf-8")
    assert main(["item", "--batch", "b", str(item)]) == 1
    assert "not running" in capsys.readouterr().err


def test_the_validator_gives_no_verdict_when_no_container_can_run(tmp_path, docker, monkeypatch,
                                                                  capsys):
    from tutor.validation.__main__ import REPORT_FILE, main

    course_files(tmp_path)
    example = Path(__file__).resolve().parents[1] / "examples" / "station-longest-rise"
    import shutil

    course = tmp_path / "course" / "batches" / "b"
    shutil.copytree(example / "exercise", course / "exercises" / "copy")
    shutil.copytree(example / "solution", course / "solutions" / "copy")
    (course / "solutions" / "copy" / REPORT_FILE).unlink(missing_ok=True)
    monkeypatch.chdir(tmp_path)
    docker.mode("down")
    assert main([str(course / "exercises" / "copy"), str(course / "solutions" / "copy")]) == 2
    assert "not running" in capsys.readouterr().err
    assert not (course / "solutions" / "copy" / REPORT_FILE).exists()
