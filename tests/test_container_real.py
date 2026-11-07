"""Real containers: the limits hold, and a run gives the same result as on the host.

Needs Docker running, so plain `pytest` leaves these out: `pytest -m docker`. The first run
builds a small image (Python and the test packages, about a minute) and later runs reuse it.
Each test is code trying something from inside: PASSED means the limit held.
"""

import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from tutor.config import Workspace
from tutor.container import build, docker_problem
from tutor.execution import REPORT_FILE, Outcome, run_pytest, run_script

pytestmark = pytest.mark.docker


@pytest.fixture(scope="session")
def course(tmp_path_factory) -> Path:
    if problem := docker_problem():
        pytest.skip(problem)
    root = tmp_path_factory.mktemp("course")
    requires = {"python": "3.11"}
    (root / "workspace.toml").write_text(
        'course = "dockertest"\n[requires]\npython = "3.11"\n[isolation]\nrunner = "container"\n',
        encoding="utf-8")
    build(Workspace(root=root, course="dockertest", requires=requires, lecture_dir=root / "l"),
          say=lambda text: None)
    return root


def exercise(course: Path, name: str, test_body: str) -> dict[str, Path]:
    """One test file under the course, which is what sends the run to the container."""
    folder = course / name
    folder.mkdir()
    (folder / "student.py").write_text("VALUE = 1\n", encoding="utf-8")
    (folder / "test_student.py").write_text(
        "import os, socket, subprocess, sys, time\nimport pytest\n\n\n"
        "def test_it():\n" + textwrap.indent(textwrap.dedent(test_body), "    "),
        encoding="utf-8")
    return {"student.py": folder / "student.py", "test_student.py": folder / "test_student.py"}


def containers_left() -> str:
    return subprocess.run(["docker", "ps", "-aq", "--filter", "name=tutor-run-"],
                          capture_output=True, text=True).stdout.strip()


def test_results_come_back_as_on_the_host(course):
    passed = run_pytest(exercise(course, "passes", "from student import VALUE\nassert VALUE == 1"))
    assert passed.outcome is Outcome.PASSED and passed.counts.passed == 1
    failed = run_pytest(exercise(course, "fails", "assert 1 == 2"))
    assert failed.outcome is Outcome.FAILED and failed.counts.failed == 1
    assert "assert 1 == 2" in failed.output
    broken = run_pytest(exercise(course, "broken", "import no_such_module"))
    assert broken.outcome is Outcome.FAILED
    assert containers_left() == ""


def test_exercise_tests_can_import_the_testkit(course):
    files = exercise(course, "kit", "from tutor.testkit import assert_output\nassert assert_output")
    assert run_pytest(files).outcome is Outcome.PASSED


def test_a_script_runs_and_its_exit_code_decides(course):
    files = exercise(course, "script", "pass")
    assert run_script(files, "student.py").outcome is Outcome.PASSED
    (course / "script" / "student.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    assert run_script(files, "student.py").outcome is Outcome.ERROR


def test_no_network_and_no_names(course):
    files = exercise(course, "network", """\
        with pytest.raises(OSError):
            socket.create_connection(("1.1.1.1", 53), timeout=3)
        with pytest.raises(OSError):
            socket.gethostbyname("example.com")
    """)
    assert run_pytest(files).outcome is Outcome.PASSED


def test_the_only_writable_places_are_the_run_folder_and_tmp(course):
    files = exercise(course, "writes", """\
        for path in ("/etc/x", "/home/runner/x", "/opt/tutor/tutor/x", "/usr/local/x"):
            with pytest.raises(OSError):
                open(path, "w")
        open("here.txt", "w").write("x")
        open("/tmp/x", "w").write("x")
        assert os.getuid() != 0
    """)
    assert run_pytest(files).outcome is Outcome.PASSED


def test_this_machines_home_and_environment_are_out_of_reach(course, monkeypatch):
    monkeypatch.setenv("TUTOR_TEST_SECRET", "not-for-the-container")
    # A file that is surely on this machine: this test file, under the repository (in the
    # home folder on the owner's Mac and on GitHub's runner).
    this_file = Path(__file__).resolve().as_posix()
    home = Path.home().as_posix()
    # The image's own user is `runner`, with an empty /home/runner of its own; on a machine
    # whose user is also `runner` (GitHub's), the same name is no sign of the host's home.
    home_check = "" if home == "/home/runner" else f"assert not os.path.exists({home!r})"
    files = exercise(course, "secrets", f"""\
        assert not os.path.exists({this_file!r})
        {home_check}
        assert not os.path.exists("/opt/tutor/.env")
        assert not os.path.exists("/opt/.env")
        assert "TUTOR_TEST_SECRET" not in os.environ
        assert os.environ["ONLY_THIS"] == "yes"
    """)
    assert run_pytest(files, env={"ONLY_THIS": "yes"}).outcome is Outcome.PASSED


def test_runs_share_nothing(course):
    first = exercise(course, "leaves", "open('/tmp/left-behind', 'w').write('x')")
    assert run_pytest(first).outcome is Outcome.PASSED
    second = exercise(course, "looks", "assert not os.path.exists('/tmp/left-behind')")
    assert run_pytest(second).outcome is Outcome.PASSED


def test_too_many_processes_stop_at_the_limit(course):
    files = exercise(course, "bomb", """\
        kids = []
        with pytest.raises(OSError):
            while len(kids) < 3000:
                kids.append(subprocess.Popen(["sleep", "30"]))
        assert len(kids) < 1100
        for kid in kids:
            kid.kill()
    """)
    assert run_pytest(files, timeout_s=60).outcome is Outcome.PASSED


def test_too_much_memory_kills_the_run_and_says_so(course):
    files = exercise(course, "hog", """\
        keep = []
        while True:
            keep.append(bytearray(50_000_000))
    """)
    result = run_pytest(files, timeout_s=60)
    assert result.outcome is Outcome.ERROR and result.returncode == 137
    assert "memory limit" in result.output
    assert containers_left() == ""


def test_a_timeout_leaves_nothing_running(course):
    files = exercise(course, "sleeps", "time.sleep(120)")
    start = time.monotonic()
    result = run_pytest(files, timeout_s=3)
    assert result.outcome is Outcome.TIMEOUT
    assert time.monotonic() - start < 15
    assert containers_left() == ""


def test_a_process_left_behind_ends_with_the_run(course):
    files = exercise(course, "leftover", """\
        subprocess.Popen(["sleep", "120"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    """)
    start = time.monotonic()
    assert run_pytest(files, timeout_s=30).outcome is Outcome.PASSED
    assert time.monotonic() - start < 20
    assert containers_left() == ""


@pytest.mark.parametrize("leave", [
    f'os.symlink("/etc/passwd", "{REPORT_FILE}")',
    f'os.mkfifo("{REPORT_FILE}")',
], ids=["link", "fifo"])
def test_a_report_left_by_hostile_code_does_not_count_or_hang(course, leave):
    # os._exit: pytest never writes the real report, and the exit code says all passed.
    files = exercise(course, f"report-{leave[3:7]}", f"{leave}\nos._exit(0)")
    start = time.monotonic()
    result = run_pytest(files, timeout_s=30)
    assert result.outcome is Outcome.ERROR and result.counts is None
    assert time.monotonic() - start < 20


def test_four_runs_at_once_do_not_disturb_each_other(course, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    files = [exercise(course, f"parallel-{i}", "time.sleep(2)") for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda f: run_pytest(f, timeout_s=60, heavy=False), files))
    assert [r.outcome for r in results] == [Outcome.PASSED] * 4
