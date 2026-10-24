"""python -m tutor.validation <exercise_dir> <solution_dir>

Prints one line per rule; exit code 0 only if the exercise is valid.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tutor.config import load_env
from tutor.validation import ExerciseError, ValidationReport, load_exercise, validate


def format_report(report: ValidationReport) -> str:
    lines = [report.exercise_id]
    for r in report.results:
        lines.append(
            f"  {r.status.value:<5} {r.rule:<4} {r.name:<17} {r.duration_s:5.1f}s  {r.detail}"
        )
    lines.append("VALID" if report.valid else "INVALID")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Usage: python -m tutor.validation <exercise_dir> <solution_dir>")
        return 2
    load_env()  # JAVA_HOME etc. for the Spark runs
    try:
        exercise = load_exercise(Path(args[0]), Path(args[1]))
    except ExerciseError as e:
        print(f"exercise: {e}", file=sys.stderr)
        return 1
    report = validate(exercise)
    print(format_report(report))
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
