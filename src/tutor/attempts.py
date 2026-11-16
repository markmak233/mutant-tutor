"""Record every test run of a workspace exercise in attempts/attempts.jsonl (docs/design.md).

A pytest plugin, registered through the `pytest11` entry point, so it loads in every pytest
run of this venv. It writes only for tests under <course>/batches/<batch>/exercises/<exercise>/,
the course being WORKSPACE_DIR or else the nearest folder above the test with a
workspace.toml (workspace_of): validator runs (temp dirs) and the repo's own tests are never
recorded. One line per exercise per run, e.g.

    {"time": "2026-10-25T14:03:11", "batch": "2026-10-25_p2-w2-mapreduce",
     "exercise": "word-count", "mode": "learn", "result": "failed", "passed": 2,
     "failed": 1, "errors": 0, "skipped": 0,
     "first_error": "test_example: AssertionError: got 0 lines...",
     "partitions": 2, "duration_s": 6.1}

"result" is "error", "failed", "interrupted" (Ctrl+C before the run finished),
"incomplete" (a test was skipped) or "passed": only a run where every test ran and passed.

A failed or errored run also keeps a copy of the student file it tested (student.py, or
student.c in a C exercise), under attempts/code/<batch>/<exercise>/<hash>.py (.c) (one copy
per distinct content), named in the line's "code" field: the owner's real mistakes,
replayable as wrong solutions that an exercise's tests must catch (the held-out set,
roadmap 3b).

The held-out checks, the hint command and the evaluation report read the file; it is also
kept for choosing review batches later. After a run is recorded the workspace's private report
is rebuilt (tutor.eval.report), so it follows the solving with no extra step. Recording never
changes a test result: any problem becomes a warning.
"""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path

from tutor import attempt_records
from tutor.attempt_records import Tally, rebuild_report, short_error, write_attempt
from tutor.attempt_transport import EXPORT_ENV, append_event
from tutor.config import WORKSPACE_FILE, WorkspaceError, load_env, load_workspace

# Public filename retained for existing hint, held-out and evaluation readers.
ATTEMPTS_FILE = attempt_records.ATTEMPTS_FILE
INTERRUPTED = 2  # pytest.ExitCode.INTERRUPTED, without importing pytest here


# pytest calls the hooks below as plain functions, so one run's state lives in the module.
_config = None
_started = 0.0
_tallies: dict[Path, Tally] = {}  # test file -> results
_export = False  # export mode never resolves a workspace or loads its credentials
_export_failed = False


def pytest_configure(config):
    """The run starts: keep the config, whose rootpath turns a test's id into its file."""
    global _config, _started, _export, _export_failed
    _config, _started = config, time.monotonic()
    _tallies.clear()
    _export, _export_failed = os.environ.get(EXPORT_ENV) == "1", False
    if _export:
        _emit("start")


def pytest_collectreport(report):
    """A test file that cannot even be imported (syntax error in student.py) is an error."""
    if report.failed:
        _record(report, "errors")


def pytest_runtest_logreport(report):
    """Each test reports up to three times, for setup, call and teardown; one outcome counts."""
    if report.skipped and report.when in ("setup", "call"):
        _record(report, "skipped")
    elif report.when == "call":
        if report.passed:
            _record(report, "passed")
        elif report.failed:
            _record(report, "failed")
    elif report.failed:  # setup or teardown broke: an error, not a failed assertion
        _record(report, "errors")


def pytest_sessionfinish(session, exitstatus):
    """The run ended: write what it did."""
    try:
        if _export:
            _emit("finish", exitstatus=int(exitstatus))
            return  # critically, do not enter _write(), which loads the host .env
        _write(time.monotonic() - _started, interrupted=exitstatus == INTERRUPTED)
    except Exception as e:  # never let bookkeeping break a test run
        warnings.warn(f"could not record the attempt: {e}", stacklevel=1)


def exercise_of(test_file: Path, workspace_root: Path) -> tuple[str, str] | None:
    """(batch, exercise) if `test_file` is batches/<batch>/exercises/<exercise>/..., else None."""
    try:
        parts = test_file.resolve().relative_to(workspace_root.resolve() / "batches").parts
    except ValueError:
        return None
    if len(parts) >= 4 and parts[1] == "exercises":
        return parts[0], parts[2]
    return None


def _record(report, kind: str) -> None:
    """Count one outcome against its test file, and keep the file's first error."""
    if _config is None:
        return
    test_file = _config.rootpath / report.nodeid.split("::")[0]
    tally = _tallies.setdefault(test_file, Tally())
    setattr(tally, kind, getattr(tally, kind) + 1)
    if kind not in ("passed", "skipped") and tally.first_error is None:
        tally.first_error = short_error(report)
    if _export:
        error = short_error(report) if kind in ("failed", "errors") else None
        _emit("report", kind=kind, error=error)


def _emit(event: str, **fields: object) -> None:
    """Export failures warn once; bookkeeping must never change pytest's own result."""
    global _export_failed
    if _export_failed:
        return
    try:
        append_event(event, **fields)
    except Exception as e:
        _export_failed = True
        warnings.warn(f"could not export the attempt: {e}", stacklevel=1)


def workspace_of(test_file: Path, named: Path | None) -> Path | None:
    """The course a test file belongs to: WORKSPACE_DIR's when the file is one of its
    exercises, else the nearest folder above the file with a workspace.toml, when the file
    is one of that course's exercises. The second covers every terminal that has no
    WORKSPACE_DIR, or another course's: a course's VS Code terminal sets it on macOS only
    (workspace.SETTINGS), and the repo's `.env` names one course. Without it, such runs
    went unrecorded without a word (found 2026-11-16). A validator's or tutor's own temp
    copy has no workspace.toml above it, so it still leaves no trace."""
    if named is not None and exercise_of(test_file, named) is not None:
        return named
    for folder in test_file.resolve().parents:
        if (folder / WORKSPACE_FILE).is_file():
            return folder if exercise_of(test_file, folder) is not None else None
    return None


def _write(duration_s: float, interrupted: bool = False) -> None:
    """Append one line per exercise this run touched, then rebuild the report of each course
    written to. A run of no workspace exercise (the validator's, the repo's own tests) leaves
    no trace."""
    if not _tallies:
        return
    # Tests run from a workspace folder, so the repo's .env is loaded by path. An installed
    # tool has no such file; workspace_of finds its courses from the test files instead.
    load_env(Path(__file__).resolve().parents[2] / ".env")
    raw = os.environ.get("WORKSPACE_DIR")
    named = Path(raw).expanduser().resolve() if raw else None
    # Add test files up per course and exercise: pytest started higher up may run several
    # files of one exercise, and (rarely) exercises of more than one course.
    by_course: dict[Path, dict[tuple[str, str], Tally]] = {}
    for test_file, tally in _tallies.items():
        root = workspace_of(test_file, named)
        if root is None:
            continue  # not a workspace exercise: validator or repo tests
        key = exercise_of(test_file, root)
        total = by_course.setdefault(root, {}).setdefault(key, Tally())
        total.passed += tally.passed
        total.failed += tally.failed
        total.errors += tally.errors
        total.skipped += tally.skipped
        total.first_error = total.first_error or tally.first_error

    partitions = int(os.environ.get("TUTOR_PARTITIONS", "2"))
    for root, by_exercise in by_course.items():
        # WORKSPACE_DIR's course loads with the rest of the environment (its LECTURE_DIR);
        # a course found from its files loads alone, as `tutor test` loads one.
        environ = None if root == named else {"WORKSPACE_DIR": str(root)}
        try:
            workspace = load_workspace(environ)
        except WorkspaceError as e:
            warnings.warn(f"attempt not recorded: {e}", stacklevel=1)
            continue
        for (batch, exercise), tally in by_exercise.items():
            write_attempt(workspace, batch, exercise, tally, duration_s,
                          interrupted=interrupted, partitions=partitions, rebuild=False)
        rebuild_report(workspace)  # all exercise lines are durable before rebuilding once
