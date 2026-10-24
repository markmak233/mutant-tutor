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
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DEFAULT_TIMEOUT_S = 30.0


class Outcome(Enum):
    """What happened to one pytest run. Validation needs FAILED and ERROR kept apart:
    a skeleton must fail its tests, not crash before any test runs."""

    PASSED = "passed"  # exit code 0: every test passed
    FAILED = "failed"  # exit code 1: tests ran and at least one failed
    ERROR = "error"  # exit codes 2-5: syntax/import error, usage error, no tests collected
    TIMEOUT = "timeout"  # killed after the time limit


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    returncode: int | None  # None when the run timed out
    duration_s: float
    output: str  # pytest's stdout and stderr, interleaved


def run_pytest(files: Mapping[str, Path], timeout_s: float = DEFAULT_TIMEOUT_S) -> RunResult:
    """Copy `files` into a fresh temp dir and run pytest there.

    `files` maps the name inside the temp dir to a source file or directory, so the
    same tests can run against different code, e.g.
    {"student.py": mutant_path, "test_student.py": tests_path}.
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
        return _run_in(workdir, timeout_s)


def _run_in(workdir: Path, timeout_s: float) -> RunResult:
    # sys.executable: the project venv, so student code can import pyspark and mrjob.
    # no:cacheprovider: no .pytest_cache; the temp dir is discarded anyway.
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    # PYSPARK_PYTHON: Spark workers otherwise start the `python3` on PATH, which may be a
    # different minor version than the driver (PYTHON_VERSION_MISMATCH).
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYSPARK_PYTHON": sys.executable}
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
    return RunResult(_outcome(proc.returncode), proc.returncode, duration, output)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # the whole group has already exited


def _outcome(returncode: int) -> Outcome:
    if returncode == 0:
        return Outcome.PASSED
    if returncode == 1:
        return Outcome.FAILED
    return Outcome.ERROR
