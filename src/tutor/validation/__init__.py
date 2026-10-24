"""Deterministic exercise validation: never judged by an LLM (docs/design.md).

So far rules 1-3 (reference passes, skeleton fails, mutants killed). Rules 4-5 and the
API checks follow in phase 1g. Run with `python -m tutor.validation <exercise> <solution>`.
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
