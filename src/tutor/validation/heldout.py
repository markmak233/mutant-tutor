"""The held-out check (roadmap 3b; docs/design.md, "Independent checks").

VALID says the tests, reference, mutants and oracle agree. They come from one generation,
so they can share one misreading of the question, and killing every planted mutant only
shows the planted bugs are caught. The held-out set is code the generator and repair
never saw, judged by the exercise's own tests:

- a correct solution (the owner's passing student.py) must pass under every condition the
  validator puts the reference through (rules 1 and 5), and agree with the oracle on
  random inputs (rule 4). If it does not, the tests reject a correct answer, or they let
  the owner's code pass with a bug that only shows at other partition counts or inputs;
- a wrong solution (a failing student.py the attempts plugin kept) must still be caught,
  as rule 3 catches a mutant. If it is not, the tests have become weaker since.

So this module adds no new kind of check: it runs the validator's own rules with another
candidate in place of the reference or a mutant. It only reports. VALID and
validation.json never change, and nothing here feeds the generation pipeline's repair
round, or the set would stop being held out. Its findings measure how often a VALID
exercise is still wrong (phase 5).

The owner's code is run like their own pytest runs (it is theirs, so it skips the static
safety check); the exercise's files must pass that check, as for validation. Every run is
in a temp dir, so the attempts plugin does not log it.

    python -m tutor.validation.heldout <batch_dir> [<exercise_id> ...]

prints one line per check and saves them in solutions/<id>/heldout.json.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tutor.attempts import ATTEMPTS_FILE
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.validation.checks import (
    RuleResult,
    Status,
    check_distribution,
    check_format,
    check_matches_oracle,
    check_reference_passes,
    check_safety,
    in_parallel,
    mutant_verdict,
)
from tutor.validation.exercise import STUDENT_FILE, ExerciseError, ExerciseFiles, load_exercise

REPORT_FILE = "heldout.json"
CORRECT = "correct"  # must pass
WRONG = "wrong"  # must be caught


@dataclass(frozen=True)
class Candidate:
    """One held-out solution."""

    kind: str  # CORRECT or WRONG
    label: str  # how the report names it: "student.py", "attempt 10-30 23:35:43"
    path: Path
    source: str  # where it came from, relative: exercises/<id>/student.py, attempts/code/...


@dataclass(frozen=True)
class HeldOutResult:
    """One line of the report: one check of one candidate."""

    candidate: Candidate
    check: str  # "tests", "distribution", "oracle" (a correct one); "caught" (a wrong one)
    status: Status  # FAIL is a finding for the owner to look at, not proof of a bad exercise
    detail: str
    duration_s: float


def owner_candidates(batch_dir: Path, exercise_id: str, attempts_dir: Path) -> list[Candidate]:
    """The owner's code for one exercise, found through attempts.jsonl:
    - CORRECT: the exercise's student.py, if a run of it ever passed. The log keeps no copy
      of passing code, so this is the file as it is now; if the owner changed it after
      passing, its "tests" check says so;
    - WRONG: each student.py kept from a "failed" run (the plugin keeps one copy per
      content). "error" runs are left out: the tests never ran (a syntax or import error),
      so they show nothing about the tests. "incomplete" and "interrupted" runs keep none.
    """
    log = attempts_dir / ATTEMPTS_FILE
    runs = []
    if log.is_file():
        for raw in log.read_text().splitlines():
            try:
                run = json.loads(raw)
            except json.JSONDecodeError:
                continue  # a half-written line, from a run killed while it logged
            if run.get("batch") == batch_dir.name and run.get("exercise") == exercise_id:
                runs.append(run)

    found = []
    student = batch_dir / "exercises" / exercise_id / STUDENT_FILE
    if student.is_file() and any(run.get("result") == "passed" for run in runs):
        found.append(Candidate(CORRECT, STUDENT_FILE, student,
                               f"exercises/{exercise_id}/{STUDENT_FILE}"))
    seen = set()  # several runs of unchanged code name the same copy
    for run in runs:
        code = run.get("code")  # relative to attempts/, e.g. code/<batch>/<id>/<hash>.py
        if run.get("result") != "failed" or not code or code in seen:
            continue
        seen.add(code)
        path = attempts_dir / code
        if path.is_file():  # the owner may have deleted a copy by hand
            found.append(Candidate(WRONG, f"attempt {_when(run.get('time', ''))}", path,
                                   f"{attempts_dir.name}/{code}"))
    return found


def _when(time: str) -> str:
    """"2026-10-30T23:35:43" -> "10-30 23:35:43": enough to find the run in the log."""
    return time[5:].replace("T", " ") or "(no time)"


def check_heldout(ex: ExerciseFiles, candidates: list[Candidate],
                  timeout_s: float | None = None) -> list[HeldOutResult]:
    """Every check of every candidate, candidates in the given order."""
    if timeout_s is None:
        timeout_s = ex.timeout_s
    # The exercise's own files run in every check, so they pass the validator's first two
    # gates first: rule 0 (the rules read their settings from metadata) and the static
    # safety check (code that fails it is never run). Safety needs the metadata's
    # framework, hence the order.
    gate = check_format(ex)
    if gate.status is Status.OK:
        gate = check_safety(ex)
    if gate.status is not Status.OK:
        reason = f"skipped: the exercise fails rule {gate.rule}: {gate.detail}"
        return [HeldOutResult(c, "all", Status.SKIPPED, reason, 0.0) for c in candidates]
    # Candidates are independent: check them at once (execution.py caps how many test runs
    # go at a time) and flatten the results back into the candidates' order.
    per_candidate = in_parallel([lambda c=c: _check_one(ex, c, timeout_s) for c in candidates])
    return [result for results in per_candidate for result in results]


def _check_one(ex: ExerciseFiles, c: Candidate, timeout_s: float) -> list[HeldOutResult]:
    if c.kind == WRONG:
        # Rule 3's verdict, as if the owner's mistake were a planted mutant.
        verdict, spent = mutant_verdict(ex, c.path, timeout_s)
        status, detail = {
            "killed": (Status.OK, "the tests still catch it"),
            "survived": (Status.FAIL, "passes the tests now: they no longer catch this mistake"),
            "broken": (Status.NOT_APPLICABLE, "now errors before any test runs, so it shows "
                                              "nothing about the tests"),
        }[verdict]
        return [HeldOutResult(c, "caught", status, detail, spent)]

    # A correct one: rule 1 first, at the partition count of the owner's own runs.
    tests = check_reference_passes(ex, timeout_s, c.path, c.label)
    if tests.status is not Status.OK:
        # It passed these tests once. Either the file changed since or the tests did; the
        # check cannot tell which, so it says both. Rules 4 and 5 would prove nothing.
        detail = f"{tests.detail}; it passed before, so it changed since or the tests did"
        skipped = "skipped: it must pass the tests first"
        return [HeldOutResult(c, "tests", Status.FAIL, detail, tests.duration_s),
                HeldOutResult(c, "distribution", Status.SKIPPED, skipped, 0.0),
                HeldOutResult(c, "oracle", Status.SKIPPED, skipped, 0.0)]
    # Then rules 5 and 4 at once: they do not depend on each other.
    distribution, oracle = in_parallel([
        lambda: check_distribution(ex, timeout_s, c.path, c.label),
        lambda: _oracle(ex, timeout_s, c),
    ])
    return [_line(c, "tests", tests), _line(c, "distribution", distribution),
            _line(c, "oracle", oracle)]


def _oracle(ex: ExerciseFiles, timeout_s: float, c: Candidate) -> RuleResult:
    """Rule 4 on the candidate. Without an oracle the exercise is already INVALID (rule 4);
    here that is not a finding about the candidate, so it is n/a."""
    if ex.oracle is None or ex.oracle_test is None:
        return RuleResult("4", "matches oracle", Status.NOT_APPLICABLE,
                          "no oracle.py / test_oracle.py in this exercise", 0.0)
    return check_matches_oracle(ex, timeout_s, c.path, c.label)


def _line(c: Candidate, check: str, r: RuleResult) -> HeldOutResult:
    return HeldOutResult(c, check, r.status, r.detail, r.duration_s)


def format_report(exercise_id: str, results: list[HeldOutResult]) -> str:
    lines = [exercise_id]
    if not results:
        lines.append("  no held-out solutions yet: no passed or failed run in attempts.jsonl")
    for r in results:
        lines.append(f"  {r.status.value:<5} {r.candidate.kind:<8} {r.candidate.label:<23}"
                     f" {r.check:<13} {r.detail}")
    return "\n".join(lines)


def report_to_dict(exercise_id: str, results: list[HeldOutResult], checked_at: str) -> dict:
    """heldout.json: kept next to validation.json, which it never changes."""
    return {
        "exercise_id": exercise_id,
        "checked_at": checked_at,
        "flagged": sum(r.status is Status.FAIL for r in results),
        "results": [
            {
                "kind": r.candidate.kind,
                "candidate": r.candidate.label,
                "source": r.candidate.source,
                "check": r.check,
                "status": r.status.value,
                "detail": r.detail,
                "duration_s": round(r.duration_s, 1),
            }
            for r in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Exit code 0: nothing flagged; 1: something flagged or an exercise would not load;
    2: wrong usage or no workspace."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: python -m tutor.validation.heldout <batch_dir> [<exercise_id> ...]")
        return 2
    load_env()  # WORKSPACE_DIR for attempts/, JAVA_HOME etc. for the Spark runs
    try:
        attempts_dir = load_workspace().attempts_dir
    except WorkspaceError as e:
        print(f"workspace: {e}", file=sys.stderr)
        return 2
    batch = Path(args[0])
    # Every exercise of the batch unless some are named; hidden folders are not exercises.
    ids = args[1:] or sorted(p.name for p in (batch / "solutions").iterdir()
                             if p.is_dir() and not p.name.startswith("."))
    flagged = without = broken = 0
    for exercise_id in ids:
        solution = batch / "solutions" / exercise_id
        try:
            ex = load_exercise(batch / "exercises" / exercise_id, solution)
        except ExerciseError as e:
            print(f"{exercise_id}: {e}", file=sys.stderr)
            broken += 1
            continue
        results = check_heldout(ex, owner_candidates(batch, exercise_id, attempts_dir))
        print(format_report(exercise_id, results), flush=True)
        if not results:
            without += 1
            continue  # no file for an exercise nobody has run yet
        data = report_to_dict(exercise_id, results, datetime.now().isoformat(timespec="seconds"))
        (solution / REPORT_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        flagged += data["flagged"]
    print(f"held-out: {flagged} flagged; {without} exercise(s) without held-out solutions"
          + (f"; {broken} would not load" if broken else ""))
    return 1 if flagged or broken else 0


if __name__ == "__main__":
    sys.exit(main())
