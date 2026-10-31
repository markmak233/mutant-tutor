"""Run an exercise's tests in a throwaway directory, in a child process, with a timeout.

This is isolation from the workspace, not a sandbox: the child can still read and write
outside its directory and use the network (docs/design.md, "Code execution safety").
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

if sys.platform == "win32":  # Job Objects stand in for process groups there
    from tutor import winjob

DEFAULT_TIMEOUT_S = 30.0
REPORT_FILE = "tutor-pytest-report.xml"  # pytest's JUnit XML, written inside the temp dir
# How many child runs may be alive at once in this process. Validation runs its checks in
# parallel and a batch validates several exercises at once, so one cap holds for all of
# them, and there are two: a heavy run starts a JVM that uses every core (Spark, Hadoop),
# a light one is plain Python in one process. Heavy default: a third of the cores (4 on 12):
# measured 2026-10-30 on a Spark exercise, 4 and 6 at once took the same 47 s, 8 and 12
# pushed its hypothesis run past the 30 s timeout, a false INVALID; 2 Hadoop and 13 Spark
# exercises validated together twice, all VALID. Light default: half the cores.
# TUTOR_MAX_HEAVY_RUNS and TUTOR_MAX_RUNS override them (read once, at the first run).
MAX_HEAVY_RUNS_ENV = "TUTOR_MAX_HEAVY_RUNS"
MAX_RUNS_ENV = "TUTOR_MAX_RUNS"
_slots: dict[bool, threading.BoundedSemaphore] = {}  # heavy? -> its cap
_slots_lock = threading.Lock()
# Every run's time limit is multiplied by this. Windows: 4, because Spark cannot fork a
# Python worker daemon there and starts a new Python process for every task, one after
# another, about 0.6 s each (measured 2026-10-31: a 4-task RDD job 2.4 s, a JVM-only job
# 0.09 s); an example's 25-case hypothesis check took 101 s against the 30 s it needs on
# macOS, and validated with 120 s. TUTOR_TIMEOUT_FACTOR overrides it (read at every run).
TIMEOUT_FACTOR_ENV = "TUTOR_TIMEOUT_FACTOR"
WINDOWS_TIMEOUT_FACTOR = 4.0


def timeout_factor() -> float:
    raw = os.environ.get(TIMEOUT_FACTOR_ENV, "")
    try:
        if float(raw) > 0:
            return float(raw)
    except ValueError:
        pass  # unset or not a number: the platform default
    return WINDOWS_TIMEOUT_FACTOR if sys.platform == "win32" else 1.0


def max_runs(heavy: bool) -> int:
    raw = os.environ.get(MAX_HEAVY_RUNS_ENV if heavy else MAX_RUNS_ENV, "")
    if raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, (os.cpu_count() or 3) // (3 if heavy else 2))


def _slot(heavy: bool) -> threading.BoundedSemaphore:
    """The process-wide cap for this kind of run; a run waits for a free slot before its
    timeout starts."""
    with _slots_lock:
        if heavy not in _slots:
            _slots[heavy] = threading.BoundedSemaphore(max_runs(heavy))
        return _slots[heavy]


class Outcome(Enum):
    """What happened to one pytest run. Validation needs FAILED and ERROR kept apart:
    a skeleton must fail its tests, not crash before any test runs. The exit code alone is
    not enough: pytest also exits 0 when every test was skipped, and 1 when only a
    fixture broke, so the counts from the JUnit report decide (see _outcome)."""

    PASSED = "passed"  # tests ran, every one passed, none skipped
    FAILED = "failed"  # at least one test ran and failed an assertion
    ERROR = "error"  # nothing was verified: import/syntax error, no tests, skipped tests,
    #                  or only setup/teardown errors
    TIMEOUT = "timeout"  # killed after the time limit


@dataclass(frozen=True)
class TestCounts:
    """From pytest's JUnit XML report. errors = setup/teardown/collection errors."""

    __test__ = False  # not a pytest test class, despite the name

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    returncode: int | None  # None when the run timed out
    duration_s: float
    output: str  # pytest's stdout and stderr, interleaved
    counts: TestCounts | None = None  # None when pytest wrote no report (timeout, crash)


def run_pytest(
    files: Mapping[str, Path],
    timeout_s: float = DEFAULT_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    heavy: bool = True,
) -> RunResult:
    """Copy `files` into a fresh temp dir and run pytest there.

    `files` maps the name inside the temp dir to a source file or directory, so the
    same tests can run against different code, e.g.
    {"student.py": mutant_path, "test_student.py": tests_path}.
    `env` adds environment variables for this run only, e.g. {"TUTOR_PARTITIONS": "4"}.
    The temp dir, and anything the run leaves behind (spark-warehouse/, caches), is
    deleted afterwards; the source files are never modified.
    `heavy`: the run starts a JVM (see ExerciseFiles.heavy); unknown code counts as heavy.
    """
    with _slot(heavy), tempfile.TemporaryDirectory(prefix="tutor-run-") as tmp:
        workdir = _copy_in(files, Path(tmp))
        return _run_in(workdir, timeout_s, env or {})


def run_script(
    files: Mapping[str, Path],
    script: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    heavy: bool = True,
) -> RunResult:
    """`python <script>` with no arguments, in a fresh temp dir holding `files`, the way a
    student runs `python student.py` in the exercise folder; isolated like run_pytest.
    PASSED: exit code 0; anything else is ERROR (or TIMEOUT)."""
    with _slot(heavy), tempfile.TemporaryDirectory(prefix="tutor-run-") as tmp:
        workdir = _copy_in(files, Path(tmp))
        return _run_in(workdir, timeout_s, env or {}, [sys.executable, script])


def _copy_in(files: Mapping[str, Path], workdir: Path) -> Path:
    """Copy each source to its name inside `workdir`; directories whole (links followed)."""
    for name, source in files.items():
        target = workdir / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return workdir


def copied_files(files: Mapping[str, Path]) -> dict[str, Path]:
    """Every file run_pytest copies in for `files`, by its path in the run directory:
    directories whole, with links followed as copytree follows them."""
    found = {}
    for name, source in files.items():
        if not source.is_dir():
            found[name] = source
            continue
        for root, dirs, names in os.walk(source, followlinks=True):
            dirs.sort()
            for file in sorted(names):
                path = Path(root) / file
                found[f"{name}/{path.relative_to(source).as_posix()}"] = path
    return found


def links(files: Mapping[str, Path]) -> list[str]:
    """The symbolic links among `files` and inside their directories, by path in the run
    directory; run_pytest would copy what each one points to."""
    found = []
    for name, source in files.items():
        if source.is_symlink():
            found.append(name)
        elif source.is_dir():
            for root, dirs, names in os.walk(source):  # not into linked directories
                found += [f"{name}/{(Path(root) / n).relative_to(source).as_posix()}"
                          for n in sorted(dirs + names) if (Path(root) / n).is_symlink()]
    return found


def _run_in(workdir: Path, timeout_s: float, extra_env: Mapping[str, str],
            script: list[str] | None = None) -> RunResult:
    """Run pytest in `workdir`, or the command `script` (a Python file run directly).
    `timeout_s` is scaled by timeout_factor() here, so every caller (validation, hints,
    held-out checks) keeps stating the limit an exercise needs on macOS or Linux."""
    timeout_s *= timeout_factor()
    # sys.executable: the project venv, so student code can import pyspark and mrjob.
    # no:cacheprovider: no .pytest_cache; the temp dir is discarded anyway.
    # --junitxml: the counts that tell "all passed" from "all skipped" (see _outcome).
    cmd = script or [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                     f"--junitxml={REPORT_FILE}"]
    # PYSPARK_PYTHON: Spark workers otherwise start the `python3` on PATH, which may be a
    # different minor version than the driver (PYTHON_VERSION_MISMATCH).
    # PYTHONIOENCODING: the child's stdout is a pipe, and on Windows Python would write it
    # in the ANSI code page (cp1252), failing on any non-Latin character in test output.
    # Only stdio: what the code under test opens keeps the platform default, as in the
    # learner's own runs.
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYSPARK_PYTHON": sys.executable,
        "PYTHONIOENCODING": "utf-8",
        **extra_env,
    }
    start = time.monotonic()
    with _ProcessTree(cmd, workdir, env) as tree:
        proc = tree.proc
        try:
            output, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            tree.kill()
            output, _ = proc.communicate()  # what it printed before the kill
            return RunResult(Outcome.TIMEOUT, None, time.monotonic() - start, output)
        duration = time.monotonic() - start
        tree.kill()  # leftovers such as a JVM that was never stopped
    if script:  # no test report: the exit code is the whole answer
        outcome = Outcome.PASSED if proc.returncode == 0 else Outcome.ERROR
        return RunResult(outcome, proc.returncode, duration, output)
    counts = _read_counts(workdir / REPORT_FILE)
    return RunResult(_outcome(proc.returncode, counts), proc.returncode, duration, output,
                     counts)


class _ProcessTree:
    """The child and every process it starts, killed as one. PySpark starts a JVM under
    pytest, and killing only pytest would orphan it, still running in the temp dir.

    POSIX: a new session is a new process group, which the child's descendants join, so
    one killpg reaches them all, even after their parent has exited.
    Windows has no process groups: a Job Object does the same (tutor/winjob.py). The child
    is assigned to the job right after it starts; a process it started in that moment
    would stay outside, but Python takes far longer to start up and import pytest than
    that, and only then do tests start anything."""

    def __init__(self, cmd: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        self._job = winjob.Job() if sys.platform == "win32" else None
        self.proc = subprocess.Popen(
            # The output is decoded as UTF-8 (what PYTHONIOENCODING makes the child write);
            # bytes a program wrote some other way are replaced, not a crash.
            cmd, cwd=cwd, env=env, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=self._job is None,  # POSIX only; the job replaces it
        )
        if self._job is not None:
            try:
                self._job.add(self.proc.pid)
            except OSError:  # not contained, so do not let it run: end it and report
                self.proc.kill()
                self.proc.communicate()
                self._job.close()
                raise

    def kill(self) -> None:
        if self._job is not None:
            self._job.kill()  # returns once every process in the job is gone
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # the whole group has already exited

    def __enter__(self) -> _ProcessTree:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._job is not None:
            self._job.close()  # KILL_ON_JOB_CLOSE: also ends what an exception left running


def _read_counts(report: Path) -> TestCounts | None:
    """Totals over every <testsuite> in pytest's JUnit XML; None if it is missing or broken."""
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    tests = failed = errors = skipped = 0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
    passed = tests - failed - errors - skipped
    return TestCounts(passed=passed, failed=failed, errors=errors, skipped=skipped)


def _outcome(returncode: int, counts: TestCounts | None) -> Outcome:
    """Exit code 0 counts as PASSED only if tests really ran and none was skipped: an
    all-skipped run (exit 0) proves nothing, so a reference or oracle test must not pass
    that way. Exit code 1 counts as FAILED only if the report shows a failed test: a run
    where only a fixture broke, or one that ended before pytest wrote its report (the
    process exited from inside a test), never checked the code, so it must not count as
    killing a mutant."""
    if returncode == 0:
        if counts is None or counts.passed == 0 or counts.skipped > 0:
            return Outcome.ERROR
        return Outcome.PASSED
    if returncode == 1:
        if counts is None or counts.failed == 0:
            return Outcome.ERROR
        return Outcome.FAILED
    return Outcome.ERROR
