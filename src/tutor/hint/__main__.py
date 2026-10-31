"""A hint for an exercise you are solving (docs/design.md, roadmap 3c).

    python -m tutor.hint [--no-llm] <exercise folder>     the folder with your student.py

Runs your tests in a temporary copy (student.py is never changed, and the run is not
recorded as an attempt), then tries the cheapest and most certain layer first:
1. rules: a known low-level mistake (syntax, full-width punctuation, a TODO left, an RDD
   used like a list, output as tuples, ...) with its line and cause;
2. known mistakes: your tests fail exactly as one of the exercise's planted mutants does,
   so you are told that mistake (in a review batch, only what goes wrong);
3. a model's hint, when the workspace names a `hint` model in [llm.roles] (--no-llm skips
   it): your code goes to that model's provider, the solution never does. Asking again
   without changing your code gives a more specific hint, up to level 3.
Otherwise it shows your first real error and points to hints.md. A batch's mode comes from
its manifest.json. Each hint is logged in the workspace's attempts.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from tutor.attempts import ATTEMPTS_FILE, exercise_of
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.execution import Outcome, run_pytest
from tutor.hint import mutants
from tutor.hint.llm_hint import HintModel, HintRefused, model_hint
from tutor.hint.rules import Hint, first_rule
from tutor.llm import LLMError, config_for, roles
from tutor.testkit import DEFAULT_PARTITIONS, PARTITIONS_ENV
from tutor.validation.checks import failure_excerpt
from tutor.validation.exercise import STUDENT_FILE, TEST_FILE, ExerciseError, load_exercise


def locate(exercise_dir: Path) -> tuple[Path, str]:
    """The solution folder and the batch's mode, for a batch (batches/<b>/exercises/<id>)
    or an example (examples/<name>/exercise)."""
    if exercise_dir.parent.name == "exercises":
        batch = exercise_dir.parent.parent
        try:
            manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
            mode = manifest.get("mode", "learn")
        except (OSError, json.JSONDecodeError):
            mode = "learn"
        return batch / "solutions" / exercise_dir.name, mode
    return exercise_dir.parent / "solution", "learn"


def hint_for(exercise_dir: Path, run_tests=run_pytest, say=print,
             model: HintModel | None = None, level: int = 1) -> Hint | None:
    """The hint for the student.py in `exercise_dir`, or None when every test passes.
    `model` gives layer 3, at `level` (1 + the model hints already given for this code)."""
    solution_dir, mode = locate(exercise_dir)
    ex = load_exercise(exercise_dir, solution_dir)
    timeout = ex.timeout_s
    env = {PARTITIONS_ENV: str(DEFAULT_PARTITIONS)}

    def run(candidate: Path):
        return run_tests(ex.run_files(candidate), timeout, env)

    student = exercise_dir / STUDENT_FILE
    result = run(student)
    if result.outcome is Outcome.PASSED:
        return None
    rule = first_rule(student.read_text(encoding="utf-8"), result.output,
                      ex.metadata.get("constraints", {}), mode)
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
    # Layer 3: neither a rule nor a known mistake fits, so ask the model (if there is one).
    key, note = "", ""
    if model:
        hints_file = exercise_dir / "hints.md"
        hints = hints_file.read_text(encoding="utf-8") if hints_file.is_file() else ""
        # What the student already has: the hint may quote it freely.
        visible = "\n".join([student.read_text(encoding="utf-8"),
                             (exercise_dir / TEST_FILE).read_text(encoding="utf-8"),
                             hints, ex.skeleton.read_text(encoding="utf-8")])
        try:
            return model_hint(model, student=student.read_text(encoding="utf-8"), hints=hints,
                              constraints=ex.metadata.get("constraints", {}),
                              output=result.output,
                              reference=ex.reference.read_text(encoding="utf-8"),
                              visible=visible, mode=mode, level=level)
        except HintRefused as e:
            # Blocked twice by the leak guard, or the model failed: fall through to the
            # first error. The key records why in attempts.jsonl.
            key = f"llm-{e.kind}"
            note = f" (no model hint: {e})" if e.kind == "error" else ""
    first = failure_excerpt(result.output, per_test=6, limit=800) or result.output[-800:]
    return Hint("none", key, f"no known mistake matches{note}; your first error:\n" + first
                + "\nhints.md has three hints, each more specific than the last.")


def code_hash(exercise_dir: Path) -> str:
    """A short fingerprint of student.py: the same hash means the code did not change."""
    return hashlib.sha256((exercise_dir / STUDENT_FILE).read_bytes()).hexdigest()[:12]


def model_hints_given(exercise_dir: Path) -> int:
    """How many model hints this exercise got for the student.py as it is now."""
    try:
        workspace = load_workspace()
    except WorkspaceError:
        return 0
    where = exercise_of(exercise_dir / STUDENT_FILE, workspace.root)
    path = workspace.attempts_dir / ATTEMPTS_FILE
    if where is None or not path.is_file():
        return 0
    # Count this exercise's earlier model hints whose logged hash matches the code now;
    # hints for an older version of the code do not count, so a change starts at level 1.
    code = code_hash(exercise_dir)
    lines = (json.loads(line)
             for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return sum(1 for line in lines if line.get("event") == "hint" and line.get("layer") == "llm"
               and (line.get("batch"), line.get("exercise")) == where
               and line.get("code") == code)


def log_hint(exercise_dir: Path, hint: Hint | None) -> None:
    """One line in the workspace's attempts.jsonl, for exercises of a workspace batch;
    `code` (a hash of student.py) tells a repeated request from one after a change."""
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
            "layer": hint.layer if hint else "passed", "key": hint.key if hint else "",
            "code": code_hash(exercise_dir)}
    with open(workspace.attempts_dir / ATTEMPTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def hint_model() -> HintModel | None:
    """The workspace's `hint` model, or None when [llm.roles] names none."""
    try:
        root = load_workspace().root
        if "hint" not in roles(root):
            # No hint model configured: layer 3 is off and the command works as before.
            return None
        return HintModel(config_for("hint", workspace=root),
                         log_path=root / "logs" / "llm_calls.jsonl")
    except (WorkspaceError, LLMError) as e:
        print(f"(no model hint: {e})", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    use_model = "--no-llm" not in args
    args = [a for a in args if a != "--no-llm"]
    if len(args) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    load_env()
    exercise_dir = Path(args[0]).resolve()
    model = hint_model() if use_model else None
    # The level only matters with a model: 1 for a first request on this code, up to 3.
    try:
        hint = hint_for(exercise_dir, model=model,
                        level=1 + (model_hints_given(exercise_dir) if model else 0))
    except ExerciseError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    print("every test passes; also try TUTOR_PARTITIONS=4 pytest test_student.py"
          if hint is None else f"hint: {hint.text}")
    if hint and hint.key.startswith("level-") and hint.key != "level-3":
        print("(ask again without changing your code for a more specific hint)")
    log_hint(exercise_dir, hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
