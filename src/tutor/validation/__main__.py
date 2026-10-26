"""python -m tutor.validation <exercise_dir> <solution_dir>

Prints one line per rule and saves the same report as <solution_dir>/validation.json;
exit code 0 only if the exercise is valid.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tutor.config import load_env
from tutor.validation import ExerciseError, ValidationReport, load_exercise, validate

REPORT_FILE = "validation.json"


def format_report(report: ValidationReport) -> str:
    lines = [report.exercise_id]
    for r in report.results:
        lines.append(
            f"  {r.status.value:<5} {r.rule:<4} {r.name:<17} {r.duration_s:5.1f}s  {r.detail}"
        )
    lines.append("VALID" if report.valid else "INVALID")
    return "\n".join(lines)


def report_to_dict(report: ValidationReport, validated_at: str, tool_commit: str | None) -> dict:
    """The report as JSON-ready data. tool_commit records which version of the validator
    judged the exercise, as manifest.json does for the generator."""
    return {
        "exercise_id": report.exercise_id,
        "valid": report.valid,
        "validated_at": validated_at,
        "tool_commit": tool_commit,
        "results": [
            {
                "rule": r.rule,
                "name": r.name,
                "status": r.status.value,
                "detail": r.detail,
                "duration_s": round(r.duration_s, 1),
            }
            for r in report.results
        ],
    }


def _tool_commit() -> str | None:
    """e.g. "c5e0a46" or "c5e0a46-dirty" (uncommitted changes); None outside a git checkout."""
    repo = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty"], cwd=repo,
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


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
    # Only the command line writes the file; validate() stays free of side effects, so the
    # phase 3 graph can call it without leaving files behind.
    data = report_to_dict(report, datetime.now().isoformat(timespec="seconds"), _tool_commit())
    path = Path(args[1]) / REPORT_FILE
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"report saved to {path}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
