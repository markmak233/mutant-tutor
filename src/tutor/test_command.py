"""Run one learner exercise in its course image, then record the attempt on the host.

This command needs neither a model nor the solution folder. The exercise path identifies
the workspace; credentials are never loaded and Docker failure never falls back to the host.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tomllib
import warnings
from pathlib import Path

from tutor import execution
from tutor.attempt_records import Tally, write_attempt
from tutor.attempt_transport import EVENT_FILE
from tutor.attempts import exercise_of
from tutor.config import WorkspaceError, load_workspace
from tutor.container import ContainerError, image_for
from tutor.testkit_c import C_FLAGS_ENV, DEFAULT_FLAGS, course_flags
from tutor.validation.safety import config_files

DEFAULT_TIMEOUT_S = 300.0
# These are generated outputs or human guidance, not input to the exercise's tests.
_EXCLUDED = {"question.md", "hints.md", "sample_output", "__pycache__",
             "program", "program.exe", "a.out"}


def _positive_seconds(raw: str) -> float:
    """Reject NaN/infinity as well as nonpositive values before starting any process."""
    try:
        seconds = float(raw)
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("timeout must be a positive finite number of seconds")


def _course(exercise: Path):
    """Find the nearest workspace and require the exact student-facing batch layout."""
    for folder in (exercise, *exercise.parents):
        marker = folder / "workspace.toml"
        if not marker.is_file():
            continue
        workspace = load_workspace({"WORKSPACE_DIR": str(folder)})
        identity = exercise_of(exercise / "test_student.py", workspace.root)
        if (identity is None or exercise != workspace.batches_dir / identity[0]
                / "exercises" / identity[1]):
            raise ValueError("choose one batches/<batch>/exercises/<exercise> folder")
        data = tomllib.loads(marker.read_text(encoding="utf-8"))
        if data.get("isolation", {}).get("runner") != "container":
            raise ValueError('tutor test needs [isolation] runner = "container"; '
                             "use pytest for a host course, or choose the runner first")
        return workspace, identity, data
    raise ValueError("no course workspace.toml above the exercise folder")


def _files(exercise: Path) -> tuple[dict[str, Path], str]:
    """Select only practice input; refuse config, links and credentials before copying."""
    students = [name for name in ("student.py", "student.c") if (exercise / name).is_file()]
    if len(students) != 1 or not (exercise / "test_student.py").is_file():
        raise ValueError("need test_student.py and exactly one student.py or student.c")
    files = {p.name: p for p in sorted(exercise.iterdir())
             if not p.name.startswith(".") and p.name not in _EXCLUDED
             and p.suffix not in (".pyc", ".o", ".exe")}
    if execution.links(files):
        raise ValueError("exercise input contains a symbolic link or directory junction")
    names = execution.copied_files(files)
    if config_files(names):
        raise ValueError("remove pytest configuration files from the exercise input")
    if any(Path(name).name.startswith(".env") or Path(name).name in
           (EVENT_FILE, execution.REPORT_FILE) for name in names):
        raise ValueError("exercise input contains credentials or a reserved report filename")
    return files, students[0]


def _record(workspace, identity, result, partitions, selection):
    """Bookkeeping failures warn, never replacing a student's test result or exit code."""
    data = result.learner
    if data is None:
        warnings.warn("attempt not recorded: no learner result", stacklevel=1)
        return None
    stopped = bool(result.stop_reason)
    if data.problem and not stopped:
        warnings.warn(f"attempt not recorded: {data.problem}", stacklevel=1)
        return None
    tally = data.events.tally if data.events else Tally()
    exitstatus = data.events.exitstatus if data.events else None
    if not stopped and not any((tally.passed, tally.failed, tally.errors, tally.skipped)):
        return None  # no selected tests is not an attempt that passed
    try:
        return write_attempt(workspace, *identity, tally, result.duration_s,
                             interrupted=stopped or exitstatus == 2, partitions=partitions,
                             source=data.source, suffix=data.suffix,
                             stop_reason=result.stop_reason, selection=selection)
    except Exception as e:
        warnings.warn(f"could not record the attempt: {e}", stacklevel=1)
        return None


def _output(chunk: str) -> None:
    """Flush each received chunk, including pytest dots without trailing newlines."""
    sys.stdout.write(chunk)
    sys.stdout.flush()


def main(argv: list[str] | None = None, *, run_tests=None) -> int:
    """Resolve dependencies at call time so tests can supply a runner without Docker."""
    parser = argparse.ArgumentParser(prog="tutor test", description=__doc__.split("\n")[0])
    parser.add_argument("exercise", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("-k", dest="selection", help="run tests matching this pytest expression")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-v", action="count", default=0, help="more verbose (repeatable)")
    verbosity.add_argument("-q", action="store_true", help="less verbose")
    parser.add_argument("--timeout", type=_positive_seconds, default=DEFAULT_TIMEOUT_S,
                        metavar="SECONDS", help="wall-clock limit (default: 300 seconds)")
    args = parser.parse_args(argv)
    try:
        exercise = args.exercise.expanduser().resolve()
        workspace, identity, data = _course(exercise)
        files, student = _files(exercise)
        partitions = int(os.environ.get("TUTOR_PARTITIONS", "2"))
        if partitions <= 0:
            raise ValueError("TUTOR_PARTITIONS must be a positive integer")
        env = {"TUTOR_PARTITIONS": str(partitions)}
        if student.endswith(".c"):
            if "c" not in data.get("languages", {}):
                raise ValueError("C practice requires [languages.c] in workspace.toml")
            flags = course_flags(exercise)
            env[C_FLAGS_ENV] = json.dumps(list(DEFAULT_FLAGS) if flags is None else flags)
        options = execution.LearnerOptions(student, args.selection, -1 if args.q else args.v)
        image = image_for(files.values())
        print(f"{workspace.course}: {identity[0]}/{identity[1]}; image {image}; "
              f"limit {args.timeout:g} s; temporary copy", flush=True)
        if args.selection:
            print(f"Selected tests: {args.selection}", flush=True)
        print("Checking the course image (Docker may need to wake)...", flush=True)
        result = (run_tests or execution.run_pytest)(
            files, args.timeout, env, learner=options, on_output=_output)
        line = _record(workspace, identity, result, partitions, args.selection)
        if line:
            print(f"\nAttempt: {line['result']}; {line['passed']} passed, {line['failed']} failed, "
                  f"{line['errors']} errors, {line['skipped']} skipped", flush=True)
            if "code" in line:
                print(f"Snapshot: attempts/{line['code']}", flush=True)
        else:
            print("\nNo attempt recorded.", flush=True)
        if result.stop_reason == "timeout":
            print(f"Time limit reached ({args.timeout:g} s); increase it with --timeout SECONDS.")
            return 124
        if result.stop_reason == "keyboard_interrupt":
            return 130
        return result.returncode if result.returncode is not None else 2
    except (OSError, ValueError, WorkspaceError, ContainerError) as e:
        print(f"tutor test: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130  # readiness/slot waiting was interrupted before tests started
