"""A hint for an exercise you are solving (docs/design.md, roadmap 3c).

    python -m tutor.hint <exercise folder>     the folder with your student.py

Runs your tests in a temporary copy (student.py is never changed, and the run is not
recorded as an attempt), then tries the cheapest and most certain layer first:
1. rules: a known low-level mistake (syntax, full-width punctuation, a TODO left, an RDD
   used like a list, output as tuples, ...) with its line and cause;
2. known mistakes: your tests fail exactly as one of the exercise's planted mutants does,
   so you are told that mistake (in a review batch, only what goes wrong).
Otherwise it shows your first real error and points to hints.md. A batch's mode comes from
its manifest.json. Each hint is logged in the workspace's attempts.jsonl.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from tutor.attempts import ATTEMPTS_FILE, exercise_of
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.execution import DEFAULT_TIMEOUT_S, Outcome, run_pytest
from tutor.hint import mutants
from tutor.hint.rules import Hint, first_rule
from tutor.testkit import DEFAULT_PARTITIONS, PARTITIONS_ENV
from tutor.validation.checks import failure_excerpt
from tutor.validation.exercise import STUDENT_FILE, ExerciseError, load_exercise


def locate(exercise_dir: Path) -> tuple[Path, str]:
    """The solution folder and the batch's mode, for a batch (batches/<b>/exercises/<id>)
    or an example (examples/<name>/exercise)."""
    if exercise_dir.parent.name == "exercises":
        batch = exercise_dir.parent.parent
        try:
            mode = json.loads((batch / "manifest.json").read_text()).get("mode", "learn")
        except (OSError, json.JSONDecodeError):
            mode = "learn"
        return batch / "solutions" / exercise_dir.name, mode
    return exercise_dir.parent / "solution", "learn"


def hint_for(exercise_dir: Path, run_tests=run_pytest, say=print) -> Hint | None:
    """The hint for the student.py in `exercise_dir`, or None when every test passes."""
    solution_dir, mode = locate(exercise_dir)
    ex = load_exercise(exercise_dir, solution_dir)
    timeout = float(ex.metadata.get("timeout_s", DEFAULT_TIMEOUT_S))
    env = {PARTITIONS_ENV: str(DEFAULT_PARTITIONS)}

    def run(candidate: Path):
        return run_tests(ex.run_files(candidate), timeout, env)

    student = exercise_dir / STUDENT_FILE
    result = run(student)
    if result.outcome is Outcome.PASSED:
        return None
    rule = first_rule(student.read_text(), result.output, ex.metadata.get("constraints", {}),
                      mode)
    if rule:
        return rule
    ran: list[str] = []

    def running(name: str) -> None:
        ran.append(name)
        say(f"(first hint here: running the known mistake {name} once)")

    known = mutants.mutant_signatures(ex, solution_dir, run, on_run=running,
                                      settings=json.dumps([timeout, env]))
    if ran:
        for a, b in mutants.indistinguishable(known):
            say(f"(note on the exercise: its tests cannot tell {a} from {b})")
    found = mutants.matches(mutants.signature(result.output), known)
    exact = [m for m in found if m.exact]
    if len(exact) > 1:
        # The tests cannot tell these mistakes apart, so neither can a hint.
        described = [mutants.bug_of(solution_dir / "mutants" / f"{m.mutant}.py")
                     for m in exact]
        parts = [bug if mode == "learn" else (effect or "the result differs")
                 for bug, effect in described]
        return Hint("mutant", "|".join(m.mutant for m in exact),
                    "your tests fail exactly as several known mistakes do; it is one of: "
                    + "; ".join(parts))
    if found:
        best = found[0]
        bug, effect = mutants.bug_of(solution_dir / "mutants" / f"{best.mutant}.py")
        # Exact: the same tests fail with the same values. Otherwise only the same tests
        # fail, which other mistakes may share, so it is offered as a guess.
        how = ("your tests fail exactly as a known mistake" if best.exact else
               "the tests you fail are the ones a known mistake fails; it may be that one")
        if mode == "learn":
            text = f"{how}: {bug}" + (f", so {effect}" if effect else "")
        else:
            text = f"{how}, where {effect or 'the result differs'}; look for the cause"
        return Hint("mutant", best.mutant, text)
    first = failure_excerpt(result.output, per_test=6, limit=800) or result.output[-800:]
    return Hint("none", "", "no known mistake matches; your first error:\n" + first
                + "\nhints.md has three hints, each more specific than the last.")


def log_hint(exercise_dir: Path, hint: Hint | None) -> None:
    """One line in the workspace's attempts.jsonl, for exercises of a workspace batch."""
    try:
        workspace = load_workspace()
    except WorkspaceError:
        return
    where = exercise_of(exercise_dir / STUDENT_FILE, workspace.root)
    if where is None:
        return
    workspace.attempts_dir.mkdir(exist_ok=True)
    line = {"time": datetime.now().isoformat(timespec="seconds"), "batch": where[0],
            "exercise": where[1], "event": "hint",
            "layer": hint.layer if hint else "passed", "key": hint.key if hint else ""}
    with open(workspace.attempts_dir / ATTEMPTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    load_env()
    exercise_dir = Path(args[0]).resolve()
    try:
        hint = hint_for(exercise_dir)
    except ExerciseError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    print("every test passes; also try TUTOR_PARTITIONS=4 pytest test_student.py"
          if hint is None else f"hint: {hint.text}")
    log_hint(exercise_dir, hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
