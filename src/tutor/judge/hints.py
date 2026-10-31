"""The hint-quality judge (docs/design.md, "[3c] Calibrated LLM judge for hint quality").

tutor.hint's third layer asks a model for a hint. A deterministic guard keeps code out of
it, but whether a hint gives the fix away in words, or points the wrong way, takes reading.
The owner had asked tutor.hint for no hint by 2026-10-31, so this measures it on simulated
students:

1. simulate: some mutants of a batch play students. A mutant's docstring names its bug,
   so it is removed first; then its tests run and the hint model writes the level-1 hint
   layer 3 would give (layers 1 and 2 are skipped: layer 2 would name the mutant itself).
   Each hint goes to <out>/hints.jsonl with the mutant's real bug, and labels.md lists
   them for the owner.
2. The owner answers two questions per hint in <out>/hint-labels.jsonl: does it leak the
   fix, and does it point the right way.
3. calibrate: the judge model answers the same questions, evidence first, and its answers
   are scored against the owner's as the teaching-quality judge's are.

The judge sees the real bug and the reference (it must tell a leak from a pointer); the
hint model never saw either.

    python -m tutor.judge.hints simulate <batch_dir> --out DIR [--per-exercise 2]
        [--profile NAME] [exercise ids ...]
    python -m tutor.judge.hints calibrate DIR [--profile NAME]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tutor import llm
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.execution import Outcome, run_pytest
from tutor.hint import mutants
from tutor.hint.llm_hint import HintModel, HintRefused, first_error, model_hint
from tutor.judge.calibrate import Score
from tutor.judge.teaching import Judge, model_name
from tutor.testkit import DEFAULT_PARTITIONS, PARTITIONS_ENV
from tutor.validation.exercise import TEST_FILE, load_exercise

PROMPT_VERSION = 2  # part of a cached verdict's key: a new prompt judges again
MAX_TOKENS = 3_000
RETRIES = 2
HINTS_FILE = "hints.jsonl"  # the simulated hints, one per line
LABELS_FILE = "hint-labels.jsonl"  # the owner's answers: {"id", "leaks", "direction"}
QUESTIONS = ("leaks", "direction")
# The answer that makes a hint bad: it leaks, or it points the wrong way. A judge "flag"
# is that answer, as an n is for the teaching judge.
BAD = {"leaks": True, "direction": False}


# --- 1. simulated students --------------------------------------------------------------


def without_docstring(source: str) -> str:
    """The module without its docstring: a mutant's says "Mutant: <the bug>, <what goes
    wrong>", which a hint model must not read."""
    tree = ast.parse(source)
    first = tree.body[0] if tree.body else None
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return source
    lines = source.splitlines(keepends=True)
    return "".join(lines[first.end_lineno:]).lstrip("\n")


def simulate(batch_dir: Path, exercise_ids: list[str], per_exercise: int, model: HintModel,
             out: Path, run_tests: Callable = run_pytest, level: int = 1) -> list[dict]:
    """Level-`level` model hints for the first `per_exercise` mutants (by name) of each
    exercise; written to out/hints.jsonl and out/labels.md, and returned."""
    manifest = batch_dir / "manifest.json"
    mode = json.loads(manifest.read_text()).get("mode", "learn") if manifest.is_file() else "learn"
    students = out / "students"
    students.mkdir(parents=True, exist_ok=True)
    jobs = []
    for exercise_id in exercise_ids:
        exercise_dir = batch_dir / "exercises" / exercise_id
        ex = load_exercise(exercise_dir, batch_dir / "solutions" / exercise_id)
        for mutant in ex.mutants[:per_exercise]:
            jobs.append((exercise_id, exercise_dir, ex, mutant))

    def one(job) -> dict | None:
        exercise_id, exercise_dir, ex, mutant = job
        # The student's file: the mutant without the docstring that names its bug, kept
        # in out/students/ so the owner can read what the hint was about.
        code = without_docstring(mutant.read_text())
        student = students / f"{exercise_id}__{mutant.stem}.py"
        student.write_text(code)
        result = run_tests(ex.run_files(student), ex.timeout_s,
                           {PARTITIONS_ENV: str(DEFAULT_PARTITIONS)}, heavy=ex.heavy)
        if result.outcome is Outcome.PASSED:
            return None  # caught only at other partition counts: no failure to hint at
        hints_file = exercise_dir / "hints.md"
        hints = hints_file.read_text() if hints_file.is_file() else ""
        # What the student already has, as tutor.hint builds it: the hint may quote it.
        visible = "\n".join([code, (exercise_dir / TEST_FILE).read_text(), hints,
                             ex.skeleton.read_text()])
        bug, effect = mutants.bug_of(mutant)
        line = {"id": f"{exercise_id}/{mutant.stem}@L{level}", "batch": batch_dir.name,
                "exercise": exercise_id, "mutant": mutant.stem, "level": level,
                "mode": mode, "bug": bug, "effect": effect,
                "student": str(student.relative_to(out)),
                "first_error": first_error(result.output)}
        try:
            hint = model_hint(model, student=code, hints=hints,
                              constraints=ex.metadata.get("constraints", {}),
                              output=result.output, reference=ex.reference.read_text(),
                              visible=visible, mode=mode, level=level)
            line.update(hint=hint.text, refused="")
        except HintRefused as e:
            line.update(hint="", refused=f"{e.kind}: {e}")
        return line

    # The model calls wait on the network, so a few go at once; the order stays.
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = [line for line in pool.map(one, jobs) if line is not None]
    (out / HINTS_FILE).write_text("".join(json.dumps(line, ensure_ascii=False) + "\n"
                                          for line in found))
    (out / "labels.md").write_text(labels_sheet(found))
    return found


def labels_sheet(found: list[dict]) -> str:
    """The list the owner answers from: each hint with the real bug it should point at."""
    lines = ["# Hints to label", "",
             "For each hint answer two questions: does it **leak** the fix (the corrected code,",
             "or the exact change in words, so the student need not think)? Does it point in",
             "the right **direction** (at the real bug below)? Reply e.g. `3 leak n dir y`.", ""]
    for number, h in enumerate(found, start=1):
        lines += [f"## {number}. {h['id']}", "",
                  f"- Real bug: {h['bug']}" + (f", {h['effect']}" if h["effect"] else ""),
                  f"- Hint: {h['hint'] or '(refused: ' + h['refused'] + ')'}", ""]
    return "\n".join(lines)


# --- 2. the judge ----------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Answer(_Strict):
    evidence: str = Field(description=(
        "First: one or two sentences quoting the hint (and the bug) that decide it."))
    yes: bool


class HintVerdict(_Strict):
    leaks: Answer
    direction: Answer


SYSTEM = """You review one hint that a tutor gave a student whose code fails its tests. You
know the real bug and the reference solution; the student knows neither, and the tutor
knew neither either. Answer two questions about the hint:

1. leaks: does the hint give away more than the batch mode allows?
   - learn mode: the tutor may name the place, the idea and the change in words; it leaks
     only when it writes the corrected code, or a whole corrected statement to copy.
   - review mode: the tutor should name only the kind of error; naming the place or the
     change is a leak.
   (The owner's rule, 2026-10-31: all 22 simulated learn-mode hints, many naming the change
   in words, were fine for learning.)
2. direction: does the hint point the student toward the real bug, the place or the idea
   that is wrong? A hint about something else, or so vague it fits any bug, is no.

For each, write `evidence` first (quote the hint), then `yes`. Return only the JSON object."""


def user_prompt(h: dict, reference: str) -> str:
    return "\n\n".join([
        f"Batch mode: {h['mode']}; hint level: {h['level']} of 3 (1 is the least specific).",
        "The real bug: " + h["bug"] + (f", {h['effect']}" if h["effect"] else ""),
        "The tests' first failure the student saw:\n" + h["first_error"],
        "The reference solution:\n```python\n" + reference.rstrip() + "\n```",
        "The hint:\n" + h["hint"],
    ])


def judge_hint(h: dict, reference: str, model: Judge) -> HintVerdict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_prompt(h, reference)}]
    problem = ""
    for attempt in range(1 + RETRIES):
        reply = model.chat(model.config, messages, purpose="judge-hint", log_path=model.log_path,
                           max_tokens=MAX_TOKENS,
                           response_schema=HintVerdict.model_json_schema(),
                           thinking=model.thinking)
        try:
            return HintVerdict.model_validate_json(reply.text)
        except ValidationError as e:
            problem = "the reply does not fit the JSON schema: " + "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
        if attempt < RETRIES:
            messages = [*messages[:2], {"role": "assistant", "content": reply.text},
                        {"role": "user", "content": f"That reply has problems: {problem}. "
                                                    "Return it again with them fixed."}]
    raise ValueError(problem)


# --- 3. calibration --------------------------------------------------------------------


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def judged(out: Path, workspace: Path, model: Judge) -> list[tuple[dict, dict, HintVerdict]]:
    """(hint, the owner's labels, the judge's verdict) for every labelled hint; verdicts
    are kept in out/verdicts/ and reused while the hint, prompt and model are the same."""
    hints = {h["id"]: h for h in _rows(out / HINTS_FILE)}
    labels = [row for row in _rows(out / LABELS_FILE) if row["id"] in hints]
    store = out / "verdicts"
    store.mkdir(exist_ok=True)

    def one(label: dict) -> tuple[dict, dict, HintVerdict]:
        h = hints[label["id"]]
        reference = (workspace / "batches" / h["batch"] / "solutions" / h["exercise"]
                     / "reference.py").read_text()
        key = hashlib.sha256(json.dumps([PROMPT_VERSION, model_name(model), h, reference],
                                        sort_keys=True).encode()).hexdigest()
        path = store / (h["id"].replace("/", "__").replace("@", "_") + ".json")
        if path.is_file() and json.loads(path.read_text()).get("key") == key:
            return h, label, HintVerdict.model_validate(json.loads(path.read_text())["verdict"])
        verdict = judge_hint(h, reference, model)
        path.write_text(json.dumps({"key": key, "verdict": verdict.model_dump()}, indent=2,
                                   ensure_ascii=False) + "\n")
        return h, label, verdict

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, labels))


def scores(results: list[tuple[dict, dict, HintVerdict]]) -> list[Score]:
    """Per question, scored as the teaching judge is: the owner's bad answers are its n
    labels, and the judge must find them without flagging the good ones."""
    found = []
    for q in QUESTIONS:
        both = [(label[q] == BAD[q], getattr(v, q).yes == BAD[q]) for _, label, v in results
                if q in label]
        found.append(Score(q, n_labels=sum(o for o, _ in both),
                           found=sum(o and j for o, j in both),
                           y_labels=sum(not o for o, _ in both),
                           false_n=sum(j and not o for o, j in both)))
    return found


def report(results: list[tuple[dict, dict, HintVerdict]]) -> str:
    lines = [f"{'question':<10} {'owner bad':>9} {'found':>5} {'owner ok':>8} {'false':>5}  status"]
    for s in scores(results):
        lines.append(f"{s.point:<10} {s.n_labels:>9} {s.found:>5} {s.y_labels:>8} "
                     f"{s.false_n:>5}  {s.status}")
    lines += ["", "disagreements:"]
    for h, label, v in results:
        for q in QUESTIONS:
            if q in label and label[q] != getattr(v, q).yes:
                lines.append(f"  {h['id']}: {q}: owner {'yes' if label[q] else 'no'}, judge "
                             f"{'yes' if getattr(v, q).yes else 'no'}: {getattr(v, q).evidence}")
    return "\n".join(lines)


# --- command line -----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.judge.hints")
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate", help="model hints for some mutants of a batch")
    sim.add_argument("batch_dir", type=Path)
    sim.add_argument("exercise_ids", nargs="*", help="default: every exercise")
    sim.add_argument("--out", type=Path, required=True)
    sim.add_argument("--per-exercise", type=int, default=2)
    sim.add_argument("--profile", help="the hint model's profile, if no `hint` role is set")
    cal = sub.add_parser("calibrate", help="judge the labelled hints, compare with the owner")
    cal.add_argument("out", type=Path)
    cal.add_argument("--profile", help="the judge's profile instead of the `judge` role")
    args = parser.parse_args(argv)
    load_env()
    try:
        root = load_workspace().root
        if args.command == "simulate":
            model = HintModel(llm.config_for("hint", args.profile, workspace=root),
                              log_path=root / "logs" / "llm_calls.jsonl")
        else:
            judge = Judge(llm.config_for("judge", args.profile, workspace=root),
                          log_path=root / "logs" / "llm_calls.jsonl")
    except (WorkspaceError, llm.LLMError) as e:
        print(f"hints: {e}", file=sys.stderr)
        return 2
    if args.command == "simulate":
        ids = args.exercise_ids or sorted(p.name for p in (args.batch_dir / "solutions").iterdir()
                                          if p.is_dir() and not p.name.startswith("."))
        found = simulate(args.batch_dir, ids, args.per_exercise, model, args.out)
        refused = sum(bool(h["refused"]) for h in found)
        print(f"{len(found)} hints ({refused} refused) in {args.out / HINTS_FILE}; "
              f"labels.md lists them for the owner")
        return 0
    results = judged(args.out, root, judge)
    print(f"{len(results)} labelled hints, judged by {model_name(judge)}\n")
    print(report(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
