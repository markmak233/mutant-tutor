"""Shared attempt records for host pytest and the container learner command.

Only the host writes attempts and code snapshots. The pytest hooks supply a Tally;
container practice additionally supplies the exact source bytes staged before execution.
Recording is bookkeeping: callers warn on errors without changing the test result.
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tutor.config import Workspace
from tutor.languages import student_path

ATTEMPTS_FILE = "attempts.jsonl"
CODE_DIR = "code"
_FIRST_ERROR_CHARS = 300
# Remove terminal colouring before storing a concise, stable first-error message.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class Tally:
    """What the tests of one file, or of one exercise, did in this run."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    first_error: str | None = None


def short_error(report) -> str:
    """'test_name: AssertionError: got 0 lines, expected 5' from a failed report."""
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    message = crash.message if crash is not None else ""
    if not message:
        lines = [line for line in str(report.longrepr).splitlines() if line.strip()]
        message = lines[-1] if lines else "error"
    name = report.nodeid.split("::")[-1]
    message = _ANSI.sub("", message)
    return f"{name}: {' '.join(message.split())}"[:_FIRST_ERROR_CHARS]


def write_attempt(workspace: Workspace, batch: str, exercise: str, tally: Tally,
                  duration_s: float, *, interrupted: bool = False, partitions: int = 2,
                  source: bytes | None = None, suffix: str = ".py",
                  stop_reason: str | None = None, selection: str | None = None,
                  rebuild: bool = True) -> dict:
    """Append one host-owned record; optional frozen bytes identify the code actually run."""
    workspace.attempts_dir.mkdir(exist_ok=True)
    result = _result(tally, interrupted)
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "batch": batch, "exercise": exercise,
        "mode": _mode(workspace.batches_dir / batch), "result": result,
        "passed": tally.passed, "failed": tally.failed, "errors": tally.errors,
        "skipped": tally.skipped, "first_error": tally.first_error,
        "partitions": partitions, "duration_s": round(duration_s, 1),
    }
    # Preserve the old vocabulary; additional context never masquerades as a test pass.
    if stop_reason:
        line["stop_reason"] = stop_reason
    if selection:
        line["selection"] = selection
    if result in ("failed", "error"):
        student = student_path(workspace.batches_dir / batch / "exercises" / exercise)
        if code := _snapshot(student, workspace.attempts_dir, batch, exercise, source, suffix):
            line["code"] = code
    with (workspace.attempts_dir / ATTEMPTS_FILE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, ensure_ascii=False) + "\n")
    if rebuild:
        rebuild_report(workspace)  # the log is closed before its report is read
    return line


def rebuild_report(workspace: Workspace) -> None:
    """Bring the private report up to date. Imported here as this plugin loads in every pytest
    run of the venv and only a recorded run needs it. The attempt is already safe on disk, so a
    failure here costs a stale report and a warning, and never the test result."""
    try:
        from tutor.eval.report import write_report

        write_report(workspace.root, workspace.course)
    except Exception as e:
        warnings.warn(f"report not updated: {e}", stacklevel=1)


def _snapshot(student: Path, attempts_dir: Path, batch: str, exercise: str,
              source: bytes | None = None, suffix: str = ".py") -> str | None:
    """Copy the failing student file into attempts/code/ once per content, keeping its
    suffix (.py, .c) so the held-out check can run it as that language; its path relative
    to attempts/, or None (with a warning) when it cannot be copied."""
    try:
        data = student.read_bytes() if source is None else source
        ending = student.suffix if source is None else suffix
        name = hashlib.sha256(data).hexdigest()[:12] + ending
        relative = Path(CODE_DIR, batch, exercise, name)
        target = attempts_dir / relative
        if not target.exists():  # the same mistake run again is kept once
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return relative.as_posix()
    except OSError as e:
        warnings.warn(f"failing code not kept: {e}", stacklevel=1)
        return None


def _mode(batch_dir: Path) -> str | None:
    """`mode` from the batch's manifest.json ("learn" / "review"), None if not given."""
    try:
        return json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, AttributeError):
        return None


def _result(tally: Tally, interrupted: bool) -> str:
    """A failure the run found comes first; "passed" only if every test ran and passed."""
    if tally.errors:
        return "error"
    if tally.failed:
        return "failed"
    if interrupted:
        return "interrupted"
    if tally.skipped:
        return "incomplete"
    return "passed"
