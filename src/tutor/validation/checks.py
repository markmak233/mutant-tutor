"""Validation rules (docs/design.md, "Exercise validation").

0. metadata.json fits the batch format (schema.py); the other rules read their settings
   from it, so they are skipped if it does not.
safe. Every .py file that validation would run passes the static safety check (safety.py);
   if one does not, nothing is run.
1. The reference passes every test.
2. The skeleton fails them: tests ran and failed, not crashed before running.
3. Every mutant fails them, so the tests can catch each planted bug.
4. The reference agrees with oracle.py on random inputs (hypothesis, solution/test_oracle.py).
5. The reference passes with 1, 2 and 4 partitions (exercises with "partition_check").
api. The reference respects the exercise's static API constraints (see api.py).
main. `python student.py` runs on its own (metadata `standalone`): both student.py and the
   reference have an `if __name__ == "__main__":` block, and for a function exercise the
   reference run that way exits cleanly (it runs the question's example).

Rules 1-3 and 5 run the exercise's own test file with a different candidate as student.py;
rule 4 runs the exercise's differential test instead. Neither needs to know whether the
exercise is function-level or file-level: that lives in the exercise's test files.
"""

from __future__ import annotations

import ast
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tutor.execution import Outcome, RunResult, copied_files, links, run_pytest, run_script
from tutor.schema import Metadata, problems
from tutor.testkit import COMBINER_ENV, DEFAULT_PARTITIONS, PARTITIONS_ENV
from tutor.validation.api import count_uses, find_violations, is_main_block
from tutor.validation.exercise import STUDENT_FILE, ExerciseFiles
from tutor.validation.safety import (
    allowed_imports,
    config_files,
    find_hazards,
    workspace_allow_imports,
)

# Spark tests read PARTITIONS_ENV as their number of partitions / input files (design.md).
PARTITION_COUNTS = (1, 2, 4)


class Status(Enum):
    OK = "ok"
    FAIL = "FAIL"
    SKIPPED = "skip"  # not run because rule 0, safe or 1 failed
    NOT_APPLICABLE = "n/a"  # does not apply to this exercise; does not affect validity


@dataclass(frozen=True)
class RuleResult:
    rule: str  # "0", "safe", "1" to "5", or "api"
    name: str
    status: Status
    detail: str
    duration_s: float
    # What failed, from pytest's output (failure_excerpt), for whoever must fix it: the
    # generation pipeline's repair round. The CLI report shows `detail` only.
    excerpt: str = ""


@dataclass(frozen=True)
class ValidationReport:
    exercise_id: str
    results: list[RuleResult]

    @property
    def valid(self) -> bool:
        return all(r.status in (Status.OK, Status.NOT_APPLICABLE) for r in self.results)

    def result(self, rule: str) -> RuleResult:
        return next(r for r in self.results if r.rule == rule)


def validate(ex: ExerciseFiles, timeout_s: float | None = None,
             oracle_on_failure: bool = False) -> ValidationReport:
    """timeout_s per pytest run: the argument, else metadata `timeout_s` (Hadoop exercises
    need minutes, since every job costs 7-9 s), else DEFAULT_TIMEOUT_S.
    `oracle_on_failure`: when the reference fails its tests, still run rule 4. The oracle
    does not use the tests, so whether the reference agrees with it tells which side of
    the disagreement is likely wrong; the generation pipeline tells repair."""
    fmt = check_format(ex)
    if fmt.status is not Status.OK:
        # Every other rule reads metadata (timeout, partitions, constraints), so none of
        # them can be trusted to run as intended.
        reason = "skipped: metadata.json must fit the batch format first"
        return ValidationReport(ex.exercise_id, [
            fmt,
            RuleResult("safe", "static safety", Status.SKIPPED, reason, 0.0),
            *_skipped_after_safety(reason),
        ])
    safe = check_safety(ex)
    if safe.status is not Status.OK:
        # Code that fails the safety check is never run, not even the reference.
        return ValidationReport(ex.exercise_id, [
            fmt, safe, *_skipped_after_safety("skipped: the code must pass the safety check"),
        ])
    if timeout_s is None:
        timeout_s = ex.timeout_s
    reference = check_reference_passes(ex, timeout_s)
    if reference.status is not Status.OK:
        # A failing reference means the tests or the exercise are wrong, so the rules that
        # run tests would prove nothing; skipping them also saves the runs. The static API
        # check does not depend on the tests, so it still runs.
        skipped = _skipped_test_rules("skipped: the reference must pass first")
        if oracle_on_failure:
            skipped = [check_matches_oracle(ex, timeout_s) if r.rule == "4" else r
                       for r in skipped]
        return ValidationReport(ex.exercise_id, [
            fmt,
            safe,
            reference,
            *skipped,
            check_api_constraints(ex),
            check_main(ex, timeout_s),
        ])
    # The reference passes, so the remaining runs are independent of each other: run them
    # at once (a Spark exercise took 47 s instead of 96, 2026-10-30). The report keeps the
    # rule order.
    skeleton, mutants, oracle, distribution, main = _in_parallel([
        lambda: check_skeleton_fails(ex, timeout_s),
        lambda: check_mutants_killed(ex, timeout_s),
        lambda: check_matches_oracle(ex, timeout_s),
        lambda: check_distribution(ex, timeout_s),
        lambda: check_main(ex, timeout_s),
    ])
    return ValidationReport(ex.exercise_id, [
        fmt, safe, reference, skeleton, mutants, oracle, distribution,
        check_api_constraints(ex), main,
    ])


def _in_parallel(calls: list) -> list:
    """Call each function on its own thread and return the results in the same order.
    Every test run is a child process in its own temp dir; execution.py caps how many run
    at a time across the process, so the threads only wait."""
    with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
        return [future.result() for future in [pool.submit(call) for call in calls]]


def _skipped_after_safety(reason: str) -> list[RuleResult]:
    return [
        RuleResult("1", "reference passes", Status.SKIPPED, reason, 0.0),
        *_skipped_test_rules(reason),
        RuleResult("api", "api constraints", Status.SKIPPED, reason, 0.0),
        RuleResult("main", "runs on its own", Status.SKIPPED, reason, 0.0),
    ]


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


def check_safety(ex: ExerciseFiles) -> RuleResult:
    """Every .py file a run copies in, subfolders included, since pytest collects tests and
    conftest.py files recursively (student.py is the owner's and is replaced in every
    run), and every solution file that validation runs."""
    try:
        allowed = allowed_imports(ex.metadata["framework"],
                                  workspace_allow_imports(ex.exercise_dir))
    except ValueError as e:
        return RuleResult("safe", "static safety", Status.FAIL, str(e), 0.0)
    linked = links({n: p for n, p in ex.run_files(ex.skeleton).items() if n != STUDENT_FILE})
    if linked:  # checked first: a link may point outside the exercise, or into a loop
        return RuleResult("safe", "static safety", Status.FAIL, "; ".join(
            f"{name} is a link, not allowed in an exercise" for name in linked), 0.0)
    copied = _copied(ex)
    files = {name: path for name, path in copied.items() if name.endswith(".py")}
    solution_files = [ex.reference, ex.skeleton, *ex.mutants, ex.oracle, ex.oracle_test]
    for path in filter(None, solution_files):
        files[f"{path.parent.name}/{path.name}"] = path
    found = [f"{name} is not allowed in an exercise (pytest runs it on its own)"
             for name in config_files(copied)]
    for name, path in files.items():
        found += [f"{name} {hazard}" for hazard in find_hazards(path.read_text(), allowed)]
    if found:
        return RuleResult("safe", "static safety", Status.FAIL, "; ".join(found), 0.0)
    return RuleResult("safe", "static safety", Status.OK, f"{len(files)} files checked", 0.0)


def _copied(ex: ExerciseFiles) -> dict[str, Path]:
    """The files a run copies into its directory, by their path there, without student.py."""
    return copied_files({name: path for name, path in ex.run_files(ex.skeleton).items()
                         if name != STUDENT_FILE})


def check_main(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    """Rule main: what a student types first, `python student.py`, does something.
    Static for both interfaces; a function exercise's reference is also run (no arguments,
    in a temp copy), since its main block calls the student's function on the example."""
    standalone = ex.metadata.get("standalone")
    if standalone is None:  # an exercise written before the rule (2026-10-29)
        return RuleResult("main", "runs on its own", Status.NOT_APPLICABLE,
                          "standalone not set: an exercise from before this rule", 0.0)
    if standalone is False:
        return RuleResult("main", "runs on its own", Status.NOT_APPLICABLE,
                          "standalone: false (a framework launches it; no main of its own)",
                          0.0)
    missing = [name for name, path in (("student.py", ex.skeleton), ("reference.py",
                                                                     ex.reference))
               if not has_main_block(path.read_text())]
    if missing:
        return RuleResult("main", "runs on its own", Status.FAIL,
                          f"{' and '.join(missing)} lack an `if __name__ == \"__main__\":` "
                          f"block: `python student.py` must run on its own", 0.0)
    if ex.metadata.get("interface") != "function":
        # A program taking its paths from sys.argv (the slides' form); the tests run it.
        return RuleResult("main", "runs on its own", Status.OK,
                          "a program run as `python student.py <input> <output>`", 0.0)
    r = run_script(ex.run_files(ex.reference), STUDENT_FILE, timeout_s, heavy=ex.heavy)
    if r.outcome is Outcome.PASSED:
        return RuleResult("main", "runs on its own", Status.OK,
                          "`python student.py` ran the example (reference)", r.duration_s)
    return RuleResult("main", "runs on its own", Status.FAIL,
                      f"`python student.py` with the reference: {r.outcome.value}, exit code "
                      f"{r.returncode}", r.duration_s, r.output[-1500:])


def has_main_block(source: str) -> bool:
    """A module-level `if __name__ == "__main__":` (either side of the ==)."""
    try:
        return any(is_main_block(node) for node in ast.parse(source).body)
    except SyntaxError:
        return False


def check_reference_passes(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    r = _run(ex, ex.reference, timeout_s, DEFAULT_PARTITIONS)
    if r.outcome is Outcome.PASSED:
        return RuleResult("1", "reference passes", Status.OK, _summary(r), r.duration_s)
    detail = f"reference {r.outcome.value}: {_summary(r)}"
    return RuleResult("1", "reference passes", Status.FAIL, detail, r.duration_s,
                      failure_excerpt(r.output))


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
    excerpt = failure_excerpt(r.output) if r.outcome is Outcome.ERROR else ""
    return RuleResult("2", "skeleton fails", Status.FAIL, detail, r.duration_s, excerpt)


def check_mutants_killed(ex: ExerciseFiles, timeout_s: float) -> RuleResult:
    """A mutant is killed if it fails at any of the exercise's "mutant_partitions"
    (default [2]; a few exercises ask for [2, 4] to catch bugs that only show at 4)."""
    if not ex.mutants:
        return RuleResult(
            "3", "mutants killed", Status.FAIL,
            "no mutants: nothing shows the tests can catch a bug", 0.0,
        )
    counts = ex.metadata.get("mutant_partitions", [DEFAULT_PARTITIONS])

    def verdict(mutant: Path) -> tuple[str, float]:
        # One mutant's partition counts in turn: the first that catches it ends the runs.
        spent = 0.0
        for partitions in counts:
            r = _run(ex, mutant, timeout_s, partitions)
            spent += r.duration_s
            # TIMEOUT counts as killed: a mutant that hangs is caught by the time limit.
            if r.outcome in (Outcome.FAILED, Outcome.TIMEOUT):
                return "killed", spent
            if r.outcome is Outcome.ERROR:
                return "broken", spent
        return "survived", spent

    # Mutants are independent of each other, so they run at once.
    verdicts = _in_parallel([lambda m=m: verdict(m) for m in ex.mutants])
    survived = [m.stem for m, (v, _) in zip(ex.mutants, verdicts, strict=True) if v == "survived"]
    broken = [m.stem for m, (v, _) in zip(ex.mutants, verdicts, strict=True) if v == "broken"]
    duration = sum(spent for _, spent in verdicts)

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
    r = run_pytest(ex.differential_files(), timeout_s, heavy=ex.heavy)
    if r.outcome is Outcome.PASSED:
        return RuleResult("4", "matches oracle", Status.OK, _summary(r), r.duration_s)
    if r.outcome is Outcome.FAILED:
        detail = f"reference disagrees with the oracle; {_falsifying_example(r)}"
    elif r.outcome is Outcome.ERROR:
        detail = f"differential test cannot run: {_summary(r)}"
    else:
        detail = "differential test timed out: lower max_examples in test_oracle.py"
    return RuleResult("4", "matches oracle", Status.FAIL, detail, r.duration_s,
                      failure_excerpt(r.output))


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
    # The runs are independent, so they go at once; the first failure in this order is
    # reported, as when they ran one after another.
    results = _in_parallel([lambda n=partitions, e=env: _run(ex, ex.reference, timeout_s, n, e)
                            for _, partitions, env in runs])
    duration = sum(r.duration_s for r in results)
    for (description, _, _), r in zip(runs, results, strict=True):
        if r.outcome is not Outcome.PASSED:
            detail = f"reference {r.outcome.value} {description}: {_summary(r)}"
            return RuleResult("5", "distribution", Status.FAIL, detail, duration,
                              failure_excerpt(r.output))
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
    return run_pytest(ex.run_files(candidate), timeout_s, env, heavy=ex.heavy)


def _summary(r: RunResult) -> str:
    """pytest's last line, e.g. "4 passed in 8.7s"."""
    lines = [line.strip("= ") for line in r.output.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"


_SECTION = re.compile(r"^_{3,} (.+?) _{3,}$")
_TEST_LOCATION = re.compile(r"^test_\w+\.py:\d+")
_ABSOLUTE_PATH = re.compile(r"(?:/[^\s/'\"]+)+/([^\s/'\"]+)")


def failure_blocks(output: str, per_test: int = 12) -> list[tuple[str, list[str]]]:
    """(failing test, its lines) from `pytest -q` output: the test's line in the test file
    and pytest's `E` lines (the assertion and what differed), without the tracebacks
    through testkit or Spark and their warnings. Absolute paths are cut to the file name:
    they name the machine's user, and the text may go to a model."""
    blocks: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in output.splitlines():
        if line.startswith("="):  # "short test summary info", warnings, the last line
            current = None
            continue
        header = _SECTION.match(line)
        if header:
            current = []
            blocks.append((header.group(1), current))
        elif current is not None and len(current) < per_test and (
                line.startswith("E ") or _TEST_LOCATION.match(line)):
            current.append(_ABSOLUTE_PATH.sub(r"\1", line.rstrip()))
    return [(name, lines) for name, lines in blocks if lines]


def failure_excerpt(output: str, per_test: int = 12, limit: int = 2500) -> str:
    """failure_blocks as text, for whoever must fix the failure (the generation pipeline's
    repair round)."""
    text = "\n".join("\n".join([name, *lines])
                     for name, lines in failure_blocks(output, per_test))
    return text if len(text) <= limit else text[:limit] + "\n..."


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
