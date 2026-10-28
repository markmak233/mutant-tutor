"""Validation rules (docs/design.md, "Exercise validation").

0. metadata.json fits the batch format (schema.py); the other rules read their settings
   from it, so they are skipped if it does not.
1. The reference passes every test.
2. The skeleton fails them: tests ran and failed, not crashed before running.
3. Every mutant fails them, so the tests can catch each planted bug.
4. The reference agrees with oracle.py on random inputs (hypothesis, solution/test_oracle.py).
5. The reference passes with 1, 2 and 4 partitions (exercises with "partition_check").
api. The reference respects the exercise's static API constraints (see api.py).

Rules 1-3 and 5 run the exercise's own test file with a different candidate as student.py;
rule 4 runs the exercise's differential test instead. Neither needs to know whether the
exercise is function-level or file-level: that lives in the exercise's test files.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tutor.execution import DEFAULT_TIMEOUT_S, Outcome, RunResult, run_pytest
from tutor.schema import Metadata, problems
from tutor.testkit import COMBINER_ENV, DEFAULT_PARTITIONS, PARTITIONS_ENV
from tutor.validation.api import count_uses, find_violations
from tutor.validation.exercise import ExerciseFiles

# Spark tests read PARTITIONS_ENV as their number of partitions / input files (design.md).
PARTITION_COUNTS = (1, 2, 4)


class Status(Enum):
    OK = "ok"
    FAIL = "FAIL"
    SKIPPED = "skip"  # not run because rule 0 or 1 failed
    NOT_APPLICABLE = "n/a"  # does not apply to this exercise; does not affect validity


@dataclass(frozen=True)
class RuleResult:
    rule: str  # "0" to "5", or "api"
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
        return all(r.status in (Status.OK, Status.NOT_APPLICABLE) for r in self.results)

    def result(self, rule: str) -> RuleResult:
        return next(r for r in self.results if r.rule == rule)


def validate(ex: ExerciseFiles, timeout_s: float | None = None) -> ValidationReport:
    """timeout_s per pytest run: the argument, else metadata `timeout_s` (Hadoop exercises
    need minutes, since every job costs 7-9 s), else DEFAULT_TIMEOUT_S."""
    fmt = check_format(ex)
    if fmt.status is not Status.OK:
        # Every other rule reads metadata (timeout, partitions, constraints), so none of
        # them can be trusted to run as intended.
        reason = "skipped: metadata.json must fit the batch format first"
        return ValidationReport(ex.exercise_id, [
            fmt,
            RuleResult("1", "reference passes", Status.SKIPPED, reason, 0.0),
            *_skipped_test_rules(reason),
            RuleResult("api", "api constraints", Status.SKIPPED, reason, 0.0),
        ])
    if timeout_s is None:
        timeout_s = float(ex.metadata.get("timeout_s", DEFAULT_TIMEOUT_S))
    reference = check_reference_passes(ex, timeout_s)
    if reference.status is not Status.OK:
        # A failing reference means the tests or the exercise are wrong, so the rules that
        # run tests would prove nothing; skipping them also saves the runs. The static API
        # check does not depend on the tests, so it still runs.
        return ValidationReport(ex.exercise_id, [
            fmt,
            reference,
            *_skipped_test_rules("skipped: the reference must pass first"),
            check_api_constraints(ex),
        ])
    return ValidationReport(ex.exercise_id, [
        fmt,
        reference,
        check_skeleton_fails(ex, timeout_s),
        check_mutants_killed(ex, timeout_s),
        check_matches_oracle(ex, timeout_s),
        check_distribution(ex, timeout_s),
        check_api_constraints(ex),
    ])


def _skipped_test_rules(reason: str) -> list[RuleResult]:
    return [
        RuleResult("2", "skeleton fails", Status.SKIPPED, reason, 0.0),
        RuleResult("3", "mutants killed", Status.SKIPPED, reason, 0.0),
        RuleResult("4", "matches oracle", Status.SKIPPED, reason, 0.0),
        RuleResult("5", "distribution", Status.SKIPPED, reason, 0.0),
    ]


def check_format(ex: ExerciseFiles) -> RuleResult:
    found = problems(Metadata, ex.metadata)
    if found:
        return RuleResult("0", "format", Status.FAIL, "metadata.json: " + "; ".join(found), 0.0)
    return RuleResult("0", "format", Status.OK, "metadata.json fits the batch format", 0.0)


def check_reference_passes(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    r = _run(ex, ex.reference, timeout_s, DEFAULT_PARTITIONS)
    if r.outcome is Outcome.PASSED:
        return RuleResult("1", "reference passes", Status.OK, _summary(r), r.duration_s)
    detail = f"reference {r.outcome.value}: {_summary(r)}"
    return RuleResult("1", "reference passes", Status.FAIL, detail, r.duration_s)


def check_skeleton_fails(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    r = _run(ex, ex.skeleton, timeout_s, DEFAULT_PARTITIONS)
    if r.outcome is Outcome.FAILED:
        return RuleResult("2", "skeleton fails", Status.OK, _summary(r), r.duration_s)
    if r.outcome is Outcome.PASSED:
        detail = "skeleton passes: the tests do not check anything the student must write"
    elif r.outcome is Outcome.ERROR:
        detail = f"skeleton errors before any test runs (import/syntax?): {_summary(r)}"
    else:
        detail = "skeleton timed out"
    return RuleResult("2", "skeleton fails", Status.FAIL, detail, r.duration_s)


def check_mutants_killed(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    """A mutant is killed if it fails at any of the exercise's "mutant_partitions"
    (default [2]; a few exercises ask for [2, 4] to catch bugs that only show at 4)."""
    if not ex.mutants:
        return RuleResult(
            "3", "mutants killed", Status.FAIL,
            "no mutants: nothing shows the tests can catch a bug", 0.0,
        )
    counts = ex.metadata.get("mutant_partitions", [DEFAULT_PARTITIONS])
    survived: list[str] = []
    broken: list[str] = []
    duration = 0.0
    for mutant in ex.mutants:
        verdict = "survived"
        for partitions in counts:
            r = _run(ex, mutant, timeout_s, partitions)
            duration += r.duration_s
            # TIMEOUT counts as killed: a mutant that hangs is caught by the time limit.
            if r.outcome in (Outcome.FAILED, Outcome.TIMEOUT):
                verdict = "killed"
                break
            if r.outcome is Outcome.ERROR:
                verdict = "broken"
                break
        if verdict == "survived":
            survived.append(mutant.stem)
        elif verdict == "broken":
            broken.append(mutant.stem)

    killed = len(ex.mutants) - len(survived) - len(broken)
    parts = [f"{killed}/{len(ex.mutants)} killed"]
    if counts != [DEFAULT_PARTITIONS]:
        parts[0] += f" (partitions {', '.join(map(str, counts))})"
    if survived:
        parts.append(f"survived (tests miss this bug): {', '.join(survived)}")
    if broken:
        parts.append(f"cannot run (not a useful mutant): {', '.join(broken)}")
    status = Status.OK if not survived and not broken else Status.FAIL
    return RuleResult("3", "mutants killed", status, "; ".join(parts), duration)


def check_matches_oracle(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    """Rule 1 only tries the inputs written in the tests; random inputs find the cases the
    test author did not think of. The differential test picks partition counts itself."""
    if ex.oracle is None or ex.oracle_test is None:
        return RuleResult(
            "4", "matches oracle", Status.FAIL,
            "no oracle.py / test_oracle.py: nothing compares the reference with an "
            "independent specification", 0.0,
        )
    r = run_pytest(ex.differential_files(), timeout_s)
    if r.outcome is Outcome.PASSED:
        return RuleResult("4", "matches oracle", Status.OK, _summary(r), r.duration_s)
    if r.outcome is Outcome.FAILED:
        detail = f"reference disagrees with the oracle; {_falsifying_example(r)}"
    elif r.outcome is Outcome.ERROR:
        detail = f"differential test cannot run: {_summary(r)}"
    else:
        detail = "differential test timed out: lower max_examples in test_oracle.py"
    return RuleResult("4", "matches oracle", Status.FAIL, detail, r.duration_s)


def check_distribution(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    """The result must not depend on how the work is distributed. Two checks, each switched
    on in metadata:
    - partition_check: the reference passes at every partition count (rule 1 already ran
      the default, so only the others run here);
    - combiner_check (MRJob, inline runner): it passes with the combiner removed, since
      Hadoop may run a combiner zero, one or many times."""
    partition_check = ex.metadata.get("partition_check", False)
    combiner_check = ex.metadata.get("combiner_check", False)
    if not (partition_check or combiner_check):
        return RuleResult("5", "distribution", Status.NOT_APPLICABLE,
                          "no partition_check or combiner_check in metadata", 0.0)
    runs = []  # (description, partitions, extra env)
    if partition_check:
        runs += [(f"with {n} partition(s)", n, {}) for n in PARTITION_COUNTS
                 if n != DEFAULT_PARTITIONS]
    if combiner_check:
        runs.append(("with the combiner off", DEFAULT_PARTITIONS, {COMBINER_ENV: "off"}))
    duration = 0.0
    for description, partitions, env in runs:
        r = _run(ex, ex.reference, timeout_s, partitions, env)
        duration += r.duration_s
        if r.outcome is not Outcome.PASSED:
            detail = f"reference {r.outcome.value} {description}: {_summary(r)}"
            return RuleResult("5", "distribution", Status.FAIL, detail, duration)
    passed = []
    if partition_check:
        passed.append(f"{', '.join(map(str, PARTITION_COUNTS))} partitions")
    if combiner_check:
        passed.append("the combiner off")
    detail = f"reference passes with {' and with '.join(passed)}"
    return RuleResult("5", "distribution", Status.OK, detail, duration)


def check_api_constraints(ex: ExerciseFiles) -> RuleResult:
    constraints = ex.metadata.get("constraints", {})
    if not constraints:
        return RuleResult("api", "api constraints", Status.NOT_APPLICABLE,
                          "no constraints in metadata", 0.0)
    source = ex.reference.read_text()
    violations = find_violations(source, constraints)
    if violations:
        detail = "reference breaks its own constraints: " + "; ".join(violations)
        return RuleResult("api", "api constraints", Status.FAIL, detail, 0.0)
    rules = ", ".join(
        f"{key}={values}" for key, values in constraints.items() if key != "count_uses"
    )
    detail = f"reference respects {rules or 'all constraints'}"
    if constraints.get("count_uses"):
        # Information only (e.g. how many MRSteps); never a reason to fail.
        counts = count_uses(source, constraints["count_uses"])
        detail += "; uses: " + ", ".join(f"{name} x{n}" for name, n in counts.items())
    return RuleResult("api", "api constraints", Status.OK, detail, 0.0)


def _run(
    ex: ExerciseFiles, candidate: Path, timeout_s: float, partitions: int,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    env = {PARTITIONS_ENV: str(partitions), **(extra_env or {})}
    return run_pytest(ex.run_files(candidate), timeout_s, env)


def _summary(r: RunResult) -> str:
    """pytest's last line, e.g. "4 passed in 8.7s"."""
    lines = [line.strip("= ") for line in r.output.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"


def _falsifying_example(r: RunResult, limit: int = 300) -> str:
    """The input hypothesis reports for the disagreement, e.g.
    "Failing test case: test_matches(a=100, b=0)". Older hypothesis versions say
    "Falsifying example"; pytest prefixes each line with "E"."""
    lines = [line.lstrip("E").strip() for line in r.output.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith(("Failing test case", "Falsifying example")):
            block = []
            for follow in lines[i:]:
                if not follow:
                    break
                block.append(follow)
                if follow == ")":
                    break
            return " ".join(block)[:limit]
    return _summary(r)
