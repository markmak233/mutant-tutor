"""Deterministic exercise validation: never judged by an LLM (docs/design.md).

Rules 0-5 plus static API constraints (see checks.py).
Run with `python -m tutor.validation <exercise_dir> <solution_dir>`.
"""

from tutor.validation.checks import RuleResult, Status, ValidationReport, validate
from tutor.validation.exercise import ExerciseError, ExerciseFiles, load_exercise

__all__ = [
    "ExerciseError",
    "ExerciseFiles",
    "RuleResult",
    "Status",
    "ValidationReport",
    "load_exercise",
    "validate",
]
