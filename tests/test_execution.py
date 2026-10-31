import os
import sys
import time
from pathlib import Path

from tutor.execution import TIMEOUT_FACTOR_ENV, Outcome, run_pytest, timeout_factor


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_passing_tests(tmp_path):
    """All tests pass -> PASSED, exit code 0."""
    test = write(tmp_path / "test_ok.py", "def test_ok():\n    assert 1 + 1 == 2\n")

    result = run_pytest({"test_ok.py": test})

    assert result.outcome is Outcome.PASSED
    assert result.returncode == 0


def test_failing_assertion(tmp_path):
    """Tests run but one fails -> FAILED. This is what a skeleton or mutant should give."""
    test = write(tmp_path / "test_bad.py", "def test_bad():\n    assert 1 + 1 == 3\n")

    result = run_pytest({"test_bad.py": test})

    assert result.outcome is Outcome.FAILED
    assert "assert" in result.output


def test_files_are_copied_under_the_given_names(tmp_path):
    """The mapping renames files: here a reference solution runs as student.py."""
    reference = write(tmp_path / "reference.py", "def add(a, b):\n    return a + b\n")
    tests = write(
        tmp_path / "tests_src.py",
        "from student import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    )

    result = run_pytest({"student.py": reference, "test_student.py": tests})

    assert result.outcome is Outcome.PASSED


def test_extra_env_reaches_the_tests(tmp_path):
    """`env` adds variables for one run only; this is how rule 5 sets TUTOR_PARTITIONS."""
    test = write(
        tmp_path / "test_env.py",
        "import os\n\ndef test_env():\n    assert os.environ['TUTOR_PARTITIONS'] == '4'\n",
    )

    result = run_pytest({"test_env.py": test}, env={"TUTOR_PARTITIONS": "4"})

    assert result.outcome is Outcome.PASSED
    assert "TUTOR_PARTITIONS" not in os.environ


def test_syntax_error_is_error_not_failed(tmp_path):
    """Code that cannot even be imported -> ERROR, kept apart from a genuine test failure."""
    broken = write(tmp_path / "broken.py", "def add(a, b)\n    return a + b\n")
    tests = write(tmp_path / "t.py", "from student import add\n\ndef test_add():\n    pass\n")

    result = run_pytest({"student.py": broken, "test_student.py": tests})

    assert result.outcome is Outcome.ERROR


def test_no_tests_collected_is_error(tmp_path):
    """A directory without tests (pytest exit code 5) is an ERROR, not a pass."""
    module = write(tmp_path / "helper.py", "X = 1\n")

    result = run_pytest({"helper.py": module})

    assert result.outcome is Outcome.ERROR
    assert result.returncode == 5


# A process that sleeps for a minute, started from inside a test the way PySpark starts a
# JVM. Python rather than `sleep`, which Windows does not have.
SLEEPER = "[sys.executable, '-c', 'import time; time.sleep(60)']"


def test_timeout_kills_the_whole_process_group(tmp_path, monkeypatch):
    """An endless test is stopped at the limit, and so is any process it started
    (the way PySpark starts a JVM): the child's pid must be gone afterwards."""
    monkeypatch.setenv(TIMEOUT_FACTOR_ENV, "1")  # the 2 s limit as given, on every platform
    pid_file = tmp_path / "child.pid"
    test = write(
        tmp_path / "test_hang.py",
        "import subprocess, sys, time\n"
        "def test_hang():\n"
        f"    child = subprocess.Popen({SLEEPER})\n"
        f"    open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "    time.sleep(60)\n",
    )

    result = run_pytest({"test_hang.py": test}, timeout_s=2)

    assert result.outcome is Outcome.TIMEOUT
    assert result.returncode is None
    assert result.duration_s < 10
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    time.sleep(0.2)
    assert not _alive(child_pid)


def test_a_process_left_behind_by_a_finished_run_is_killed(tmp_path):
    """A test that passes but leaves a process running (a JVM never stopped) gets it
    killed after the run, although its parent, pytest, has already exited; the process
    sat in the temp dir, so the run returning at all shows the dir could be deleted."""
    pid_file = tmp_path / "child.pid"
    test = write(
        tmp_path / "test_leaves.py",
        "import subprocess, sys\n"
        "def test_leaves():\n"
        # Not on pytest's output pipe, which the runner reads until it closes.
        f"    child = subprocess.Popen({SLEEPER}, stdout=subprocess.DEVNULL,\n"
        "                             stderr=subprocess.DEVNULL)\n"
        f"    open({str(pid_file)!r}, 'w').write(str(child.pid))\n",
    )

    result = run_pytest({"test_leaves.py": test})

    assert result.outcome is Outcome.PASSED
    assert not _alive(int(pid_file.read_text(encoding="utf-8")))


def test_the_time_limit_is_scaled_on_windows_only(monkeypatch):
    """Spark starts a Python process per task on Windows, so limits are 4x there."""
    monkeypatch.delenv(TIMEOUT_FACTOR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert timeout_factor() == 4.0
    monkeypatch.setattr(sys, "platform", "darwin")
    assert timeout_factor() == 1.0


def test_the_time_limit_factor_can_be_overridden(monkeypatch):
    """TUTOR_TIMEOUT_FACTOR wins; a value that is not a positive number is ignored."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv(TIMEOUT_FACTOR_ENV, "2.5")
    assert timeout_factor() == 2.5
    for bad in ("0", "-1", "fast"):
        monkeypatch.setenv(TIMEOUT_FACTOR_ENV, bad)
        assert timeout_factor() == 1.0


def test_a_scaled_limit_lets_a_slow_run_finish(tmp_path, monkeypatch):
    """The factor reaches the run itself: 2 s given, 4x applied, a 3 s test passes."""
    monkeypatch.setenv(TIMEOUT_FACTOR_ENV, "4")
    test = write(tmp_path / "test_slow.py",
                 "import time\n\ndef test_slow():\n    time.sleep(3)\n")

    result = run_pytest({"test_slow.py": test}, timeout_s=2)

    assert result.outcome is Outcome.PASSED


def test_temp_dir_is_removed_and_sources_untouched(tmp_path):
    """The run happens in a throwaway directory: files it writes vanish with it, and the
    source directory gains nothing."""
    test = write(
        tmp_path / "test_writes.py",
        "import os\n"
        "def test_writes():\n"
        "    open('leftover.txt', 'w').write('x')\n"
        "    print('CWD=' + os.getcwd())\n"
        "    assert False  # fail so pytest shows the printed line\n",
    )
    before = sorted(p.name for p in tmp_path.iterdir())

    result = run_pytest({"test_writes.py": test})

    run_dir = Path(result.output.split("CWD=")[1].split()[0])
    assert not run_dir.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) would terminate the process on Windows; ask for its exit code.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False  # no such process any more
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_counts_come_from_the_junit_report(tmp_path):
    """Passed / failed counts are read from pytest's report, not its text output."""
    test = write(
        tmp_path / "test_mix.py",
        "def test_a():\n    pass\n\ndef test_b():\n    pass\n\ndef test_c():\n    assert 0\n",
    )

    result = run_pytest({"test_mix.py": test})

    assert result.outcome is Outcome.FAILED
    assert (result.counts.passed, result.counts.failed) == (2, 1)


def test_all_skipped_is_an_error_not_a_pass(tmp_path):
    """pytest exits 0 when every test is skipped; nothing was verified, so ERROR."""
    test = write(
        tmp_path / "test_skip.py",
        "import pytest\n\n@pytest.mark.skip\ndef test_a():\n    assert False\n",
    )

    result = run_pytest({"test_skip.py": test})

    assert result.returncode == 0
    assert result.outcome is Outcome.ERROR
    assert result.counts.skipped == 1


def test_a_skipped_test_among_passing_ones_is_still_an_error(tmp_path):
    """A reference must pass every test; one skipped test means one check never ran."""
    test = write(
        tmp_path / "test_some.py",
        "import pytest\n\ndef test_a():\n    pass\n\n"
        "def test_b():\n    pytest.skip('no Hadoop here')\n",
    )

    assert run_pytest({"test_some.py": test}).outcome is Outcome.ERROR


def test_only_a_fixture_error_is_an_error_not_a_failure(tmp_path):
    """pytest exits 1 when a fixture breaks, but no assertion ran: a mutant that only
    breaks setup has not been caught by the tests."""
    test = write(
        tmp_path / "test_fixture.py",
        "import pytest\n\n@pytest.fixture\ndef env():\n    raise RuntimeError('no JVM')\n\n"
        "def test_b(env):\n    assert True\n",
    )

    result = run_pytest({"test_fixture.py": test})

    assert result.returncode == 1
    assert result.outcome is Outcome.ERROR
    assert (result.counts.failed, result.counts.errors) == (0, 1)


def test_a_run_that_ends_before_its_report_is_an_error_not_a_failure(tmp_path):
    """Exit code 1 with no JUnit report: no failed assertion shows the tests caught anything."""
    test = write(tmp_path / "test_exit.py", "import os\n\ndef test_a():\n    os._exit(1)\n")

    result = run_pytest({"test_exit.py": test})

    assert (result.returncode, result.counts) == (1, None)
    assert result.outcome is Outcome.ERROR


def test_copied_files_follow_links_as_the_run_does(tmp_path):
    from tutor.execution import copied_files, links

    target = tmp_path / "target"
    target.mkdir()
    (target / "x.py").write_text("", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("1\n", encoding="utf-8")
    (data / "more").symlink_to(target, target_is_directory=True)
    assert copied_files({"data": data}) == {"data/a.csv": data / "a.csv",
                                             "data/more/x.py": data / "more" / "x.py"}
    assert links({"data": data}) == ["data/more"]


def test_the_cap_on_runs_at_once_holds_across_threads(tmp_path, monkeypatch):
    """Validation runs checks on several threads; never more child runs than the cap."""
    import threading

    from tutor import execution

    script = write(tmp_path / "slow.py", "import time\ntime.sleep(0.4)\n")
    monkeypatch.setattr(execution, "_slots", {True: threading.BoundedSemaphore(2)})
    started = time.monotonic()
    threads = [threading.Thread(target=execution.run_script, args=({"slow.py": script},
                                                                   "slow.py"))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - started >= 0.8  # two at a time: at least two rounds of 0.4 s


def test_heavy_runs_get_a_third_of_the_cores_and_light_runs_half(monkeypatch):
    from tutor import execution

    monkeypatch.delenv(execution.MAX_RUNS_ENV, raising=False)
    monkeypatch.delenv(execution.MAX_HEAVY_RUNS_ENV, raising=False)
    monkeypatch.setattr(execution.os, "cpu_count", lambda: 12)
    assert (execution.max_runs(heavy=True), execution.max_runs(heavy=False)) == (4, 6)
    monkeypatch.setenv(execution.MAX_HEAVY_RUNS_ENV, "2")
    monkeypatch.setenv(execution.MAX_RUNS_ENV, "zero")  # not a number: the default
    assert (execution.max_runs(heavy=True), execution.max_runs(heavy=False)) == (2, 6)
