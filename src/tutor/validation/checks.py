"""Validation rules 1-3 (docs/design.md, "Exercise validation").

1. The reference passes every test.
2. The skeleton fails them: tests ran and failed, not crashed before running.
3. Every mutant fails them, so the tests can catch each planted bug.

Each rule runs the same test file with a different candidate as student.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tutor.execution import DEFAULT_TIMEOUT_S, Outcome, RunResult, run_pytest
from tutor.validation.exercise import ExerciseFiles


class Status(Enum):
    OK = "ok"
    FAIL = "FAIL"
    SKIPPED = "skip"


@dataclass(frozen=True)
class RuleResult:
    rule: int
    name: str
    status: Status
    detail: str
    duration_s: float


@dataclass(frozen=True)
class ValidationReport:
    exercise_id: str
    results: list[RuleResult]

    @property
    def valid(self) -> bool:
        return all(r.status is Status.OK for r in self.results)


def validate(ex: ExerciseFiles, timeout_s: float = DEFAULT_TIMEOUT_S) -> ValidationReport:
    reference = check_reference_passes(ex, timeout_s)
    if reference.status is not Status.OK:
        # A failing reference means the tests or the exercise are wrong, so "the skeleton
        # fails" and "every mutant fails" would prove nothing. Skipping also saves the runs.
        reason = "skipped: the reference must pass first"
        return ValidationReport(ex.exercise_id, [
            reference,
            RuleResult(2, "skeleton fails", Status.SKIPPED, reason, 0.0),
            RuleResult(3, "mutants killed", Status.SKIPPED, reason, 0.0),
        ])
    return ValidationReport(ex.exercise_id, [
        reference,
        check_skeleton_fails(ex, timeout_s),
        check_mutants_killed(ex, timeout_s),
    ])


def check_reference_passes(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    r = _run(ex, ex.reference, timeout_s)
    if r.outcome is Outcome.PASSED:
        return RuleResult(1, "reference passes", Status.OK, _summary(r), r.duration_s)
    detail = f"reference {r.outcome.value}: {_summary(r)}"
    return RuleResult(1, "reference passes", Status.FAIL, detail, r.duration_s)


def check_skeleton_fails(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    r = _run(ex, ex.skeleton, timeout_s)
    if r.outcome is Outcome.FAILED:
        return RuleResult(2, "skeleton fails", Status.OK, _summary(r), r.duration_s)
    if r.outcome is Outcome.PASSED:
        detail = "skeleton passes: the tests do not check anything the student must write"
    elif r.outcome is Outcome.ERROR:
        detail = f"skeleton errors before any test runs (import/syntax?): {_summary(r)}"
    else:
        detail = "skeleton timed out"
    return RuleResult(2, "skeleton fails", Status.FAIL, detail, r.duration_s)


def check_mutants_killed(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    if not ex.mutants:
        return RuleResult(
            3, "mutants killed", Status.FAIL,
            "no mutants: nothing shows the tests can catch a bug", 0.0,
        )
    survived: list[str] = []
    broken: list[str] = []
    duration = 0.0
    for mutant in ex.mutants:
        r = _run(ex, mutant, timeout_s)
        duration += r.duration_s
        # TIMEOUT counts as killed: a mutant that hangs is caught by the time limit.
        if r.outcome is Outcome.PASSED:
            survived.append(mutant.stem)
        elif r.outcome is Outcome.ERROR:
            broken.append(mutant.stem)

    killed = len(ex.mutants) - len(survived) - len(broken)
    parts = [f"{killed}/{len(ex.mutants)} killed"]
    if survived:
        parts.append(f"survived (tests miss this bug): {', '.join(survived)}")
    if broken:
        parts.append(f"cannot run (not a useful mutant): {', '.join(broken)}")
    status = Status.OK if not survived and not broken else Status.FAIL
    return RuleResult(3, "mutants killed", status, "; ".join(parts), duration)


def _run(ex: ExerciseFiles, candidate: Path, timeout_s: float) -> RunResult:
    return run_pytest(ex.run_files(candidate), timeout_s)


def _summary(r: RunResult) -> str:
    """pytest's last line, e.g. "4 passed in 8.7s"."""
    lines = [line.strip("= ") for line in r.output.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"
