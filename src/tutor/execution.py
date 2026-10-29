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
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DEFAULT_TIMEOUT_S = 30.0
REPORT_FILE = "tutor-pytest-report.xml"  # pytest's JUnit XML, written inside the temp dir


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
) -> RunResult:
    """Copy `files` into a fresh temp dir and run pytest there.

    `files` maps the name inside the temp dir to a source file or directory, so the
    same tests can run against different code, e.g.
    {"student.py": mutant_path, "test_student.py": tests_path}.
    `env` adds environment variables for this run only, e.g. {"TUTOR_PARTITIONS": "4"}.
    The temp dir, and anything the run leaves behind (spark-warehouse/, caches), is
    deleted afterwards; the source files are never modified.
    """
    with tempfile.TemporaryDirectory(prefix="tutor-run-") as tmp:
        workdir = Path(tmp)
        for name, source in files.items():
            target = workdir / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        return _run_in(workdir, timeout_s, env or {})


def _run_in(workdir: Path, timeout_s: float, extra_env: Mapping[str, str]) -> RunResult:
    # sys.executable: the project venv, so student code can import pyspark and mrjob.
    # no:cacheprovider: no .pytest_cache; the temp dir is discarded anyway.
    # --junitxml: the counts that tell "all passed" from "all skipped" (see _outcome).
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           f"--junitxml={REPORT_FILE}"]
    # PYSPARK_PYTHON: Spark workers otherwise start the `python3` on PATH, which may be a
    # different minor version than the driver (PYTHON_VERSION_MISMATCH).
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYSPARK_PYTHON": sys.executable,
        **extra_env,
    }
    start = time.monotonic()
    # New session = new process group, so a timeout can kill grandchildren too
    # (PySpark starts a JVM under pytest; killing only pytest would orphan it).
    proc = subprocess.Popen(
        cmd, cwd=workdir, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        output, _ = proc.communicate()
        return RunResult(Outcome.TIMEOUT, None, time.monotonic() - start, output)
    duration = time.monotonic() - start
    _kill_group(proc.pid)  # leftovers such as a JVM that was never stopped
    counts = _read_counts(workdir / REPORT_FILE)
    return RunResult(_outcome(proc.returncode, counts), proc.returncode, duration, output,
                     counts)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # the whole group has already exited


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
