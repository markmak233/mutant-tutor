"""Record every test run of a workspace exercise in attempts/attempts.jsonl (docs/design.md).

A pytest plugin, registered through the `pytest11` entry point, so it loads in every pytest
run of this venv. It writes only for tests under
$WORKSPACE_DIR/batches/<batch>/exercises/<exercise>/: validator runs (temp dirs) and the
repo's own tests are never recorded. One line per exercise per run, e.g.

    {"time": "2026-10-25T14:03:11", "batch": "2026-10-25_p2-w2-mapreduce",
     "exercise": "word-count", "mode": "learn", "result": "failed", "passed": 2,
     "failed": 1, "errors": 0, "first_error": "test_example: AssertionError: got 0 lines...",
     "partitions": 2, "duration_s": 6.1}

Nothing reads the file in phase 2; it is kept for choosing review batches later and as
material for hint rules and mutants. Recording never changes a test result: any problem
becomes a warning.
"""

from __future__ import annotations

import json
import os
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tutor.config import WorkspaceError, load_env, load_workspace

ATTEMPTS_FILE = "attempts.jsonl"
_FIRST_ERROR_CHARS = 300


@dataclass
class _Tally:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    first_error: str | None = None


_config = None
_started = 0.0
_tallies: dict[Path, _Tally] = {}  # test file -> results


def pytest_configure(config):
    global _config, _started
    _config, _started = config, time.monotonic()
    _tallies.clear()


def pytest_collectreport(report):
    """A test file that cannot even be imported (syntax error in student.py) is an error."""
    if report.failed:
        _record(report, "errors")


def pytest_runtest_logreport(report):
    if report.when == "call":
        if report.passed:
            _record(report, "passed")
        elif report.failed:
            _record(report, "failed")
    elif report.failed:  # setup or teardown broke: an error, not a failed assertion
        _record(report, "errors")


def pytest_sessionfinish(session, exitstatus):
    try:
        _write(time.monotonic() - _started)
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
    if _config is None:
        return
    test_file = _config.rootpath / report.nodeid.split("::")[0]
    tally = _tallies.setdefault(test_file, _Tally())
    setattr(tally, kind, getattr(tally, kind) + 1)
    if kind != "passed" and tally.first_error is None:
        tally.first_error = _short_error(report)


def _short_error(report) -> str:
    """'test_name: AssertionError: got 0 lines, expected 5' from a failed report."""
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    message = crash.message if crash is not None else ""
    if not message:
        lines = [line for line in str(report.longrepr).splitlines() if line.strip()]
        message = lines[-1] if lines else "error"
    name = report.nodeid.split("::")[-1]
    return f"{name}: {' '.join(message.split())}"[:_FIRST_ERROR_CHARS]


def _write(duration_s: float) -> None:
    if not _tallies:
        return
    # Tests run from a workspace folder, so the repo's .env is loaded by path.
    load_env(Path(__file__).resolve().parents[2] / ".env")
    root = os.environ.get("WORKSPACE_DIR")
    if not root:
        return
    by_exercise: dict[tuple[str, str], _Tally] = {}
    for test_file, tally in _tallies.items():
        key = exercise_of(test_file, Path(root))
        if key is None:
            continue
        total = by_exercise.setdefault(key, _Tally())
        total.passed += tally.passed
        total.failed += tally.failed
        total.errors += tally.errors
        total.first_error = total.first_error or tally.first_error
    if not by_exercise:
        return  # not a workspace exercise: validator or repo tests

    try:
        workspace = load_workspace()
    except WorkspaceError as e:
        warnings.warn(f"attempt not recorded: {e}", stacklevel=1)
        return
    workspace.attempts_dir.mkdir(exist_ok=True)
    with open(workspace.attempts_dir / ATTEMPTS_FILE, "a", encoding="utf-8") as f:
        for (batch, exercise), tally in by_exercise.items():
            line = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "batch": batch,
                "exercise": exercise,
                "mode": _mode(workspace.batches_dir / batch),
                "result": _result(tally),
                "passed": tally.passed,
                "failed": tally.failed,
                "errors": tally.errors,
                "first_error": tally.first_error,
                "partitions": int(os.environ.get("TUTOR_PARTITIONS", "2")),
                "duration_s": round(duration_s, 1),
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _mode(batch_dir: Path) -> str | None:
    """`mode` from the batch's manifest.json ("learn" / "review"), None if not given."""
    try:
        return json.loads((batch_dir / "manifest.json").read_text()).get("mode")
    except (OSError, ValueError, AttributeError):
        return None


def _result(tally: _Tally) -> str:
    if tally.errors:
        return "error"
    if tally.failed:
        return "failed"
    return "passed"
