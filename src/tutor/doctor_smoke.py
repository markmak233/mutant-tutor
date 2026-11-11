"""Validate one shipped synthetic example on a disposable copy; never write a report.

Use load_exercise + validate + format_report directly: validation's CLI loads .env and
writes reports, neither of which doctor may do. Execution still uses its isolated runner.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

from tutor import project_source
from tutor.container import ContainerError
from tutor.doctor_rows import Row
from tutor.validation import load_exercise, validate
from tutor.validation.__main__ import REPORT_FILE, format_report


def examples_root() -> Path:
    """Source installs use public examples; wheels carry the same controlled examples."""
    return project_source.examples()


def smoke(course, part, *, validator=None, say=print) -> list[Row]:
    """Restore cwd before deleting the copy, including on validator errors or Ctrl+C."""
    if part.validation_needs_container and course.runner != "container":
        return [Row("SKIP", f"smoke: {part.key.upper()} is validated only in a container")]
    source = examples_root() / part.smoke_example
    if not source.is_dir():
        return [Row("SKIP", "smoke: no shipped examples in this installation")]
    validator = validate if validator is None else validator
    started = time.monotonic()
    # Ignore local test/cache artifacts; none belong to the example's validation inputs.
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    with tempfile.TemporaryDirectory(prefix="tutor-doctor-") as raw:
        root = Path(raw)
        shutil.copyfile(course.root / "workspace.toml", root / "workspace.toml")
        batch = root / "batches" / "doctor"
        exercise = batch / "exercises" / part.smoke_example
        solution = batch / "solutions" / part.smoke_example
        shutil.copytree(source / "exercise", exercise, ignore=ignored)
        shutil.copytree(source / "solution", solution, ignore=ignored)
        (solution / REPORT_FILE).unlink(missing_ok=True)
        previous = Path.cwd()
        try:
            os.chdir(root)
            try:
                report = validator(load_exercise(exercise, solution))
            except ContainerError as exc:
                return [Row("FAIL", f"smoke: {exc}",
                            "make the course image ready on the Environment page, "
                            "then run tutor doctor --smoke again")]
            say(format_report(report))
        finally:
            os.chdir(previous)
    if report.valid:
        return [Row("OK", f"smoke {part.smoke_example}: VALID in {time.monotonic()-started:.1f}s")]
    return [Row("FAIL", "smoke: validator exit code 1 (INVALID)",
                "read the validator's lines above; until this passes, the course cannot "
                "validate exercises")]
