"""The teaching-quality judge (roadmap 3b step 3; docs/design.md, "Validity vs teaching
quality").

VALID says the tests, reference, mutants and oracle agree; it does not say an exercise is
good to learn from. A model rates that on the owner's four points, y or n each:

- clear: someone who has never met the content can learn it from the question: after
  reading it they understand it and have at least an idea how to start (the owner's
  definition, 2026-10-31; it was "fully specified", which missed all of the owner's n);
- fits: matches how the lectures present the idea (a departure must be stated);
- taught: needs nothing from lectures not yet taught;
- targets: solving it needs the knowledge point it is filed under, not a workaround.

It reads what the student reads (question.md, the starting file), the reference (to see
whether solving needs the knowledge point; the student never sees it), the exercise's
lecture with its own pages marked, the lectures taught so far and the batch mode; never
the tests, mutants or hints. Each point has `evidence` (a quote that decides it) before its
y/n: written first, it is a scratchpad, as `plan` is for the held-out writer.

It never touches validity, and nothing in generation reads it. A point's verdicts are
trusted only once calibrate.py shows they agree with the owner's ratings.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tutor import llm
from tutor.materials.slides import lecture_context, lectures_taught

# Bump when the prompt changes: it is part of the inputs' hash, so every verdict is redone.
PROMPT_VERSION = 2
MAX_TOKENS = 4_000  # four short points
RETRIES = 2  # more replies after the first, each told what was wrong with the last
REPORT_FILE = "quality.json"  # in solutions/<id>/, next to validation.json
POINTS = ("clear", "fits", "taught", "targets")  # the order of the owner's ratings


class JudgeError(Exception):
    """No usable verdict came back after the retries; the message says why."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(_Strict):
    # The field descriptions go into the JSON Schema the model is given.
    evidence: str = Field(description=(
        "First: one or two sentences quoting the question (or the slides) that decide this "
        "point, and the problem if there is one."))
    ok: bool = Field(description="true: no problem a student would hit; false: there is one.")


class Verdict(_Strict):
    clear: Point
    fits: Point
    taught: Point
    targets: Point

    def letters(self) -> str:
        """The owner's notation: "yyny" in POINTS order."""
        return "".join("y" if getattr(self, p).ok else "n" for p in POINTS)


@dataclass(frozen=True)
class Judge:
    """Which model judges, and how to call it (tests pass a fake `chat`)."""

    config: llm.LLMConfig
    chat: Callable = llm.chat
    log_path: Path | None = None  # logs/llm_calls.jsonl, for the cost
    thinking: bool = False  # GLM-5.3 spent whole outputs on reasoning (graph/settings.py)


@dataclass(frozen=True)
class Inputs:
    """Everything the judge sees for one exercise."""

    question: str
    skeleton: str
    reference: str
    slides: str  # the exercise's lecture, its pages marked; "" if none found
    taught: list[str] = field(default_factory=list)  # lecture names up to this one
    mode: str = "learn"

    def key(self) -> str:
        """A hash of the inputs and the prompt version: unchanged means no new call."""
        text = json.dumps([PROMPT_VERSION, asdict(self)], sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()


def gather(batch_dir: Path, exercise_id: str, workspace: Path,
           question: str | None = None) -> Inputs:
    """What the judge sees for one exercise of a batch. `question`: a text to judge in
    place of the exercise's question.md (calibration uses the version the owner rated)."""
    solution = batch_dir / "solutions" / exercise_id
    exercise = batch_dir / "exercises" / exercise_id
    metadata = json.loads((solution / "metadata.json").read_text())
    # The original skeleton: exercises/<id>/student.py may hold the owner's answer.
    skeleton = (solution / "skeleton.py").read_text()
    if question is None:
        question_md = exercise / "question.md"
        question = (question_md.read_text() if question_md.is_file()
                    else ast.get_docstring(ast.parse(skeleton)) or "")
    manifest = batch_dir / "manifest.json"
    mode = json.loads(manifest.read_text()).get("mode", "learn") if manifest.is_file() else "learn"
    source = metadata.get("source")
    return Inputs(question=question, skeleton=skeleton,
                  reference=(solution / "reference.py").read_text(),
                  slides=lecture_context(workspace, source),
                  taught=lectures_taught(workspace, source), mode=mode)


# --- the prompt -------------------------------------------------------------------------

SYSTEM = """You review one programming exercise of a university course for its teaching
quality, before a student solves it. You do not decide whether its code is correct: tests
do that. Rate four points, each true (fine) or false (a problem a student would hit):

1. clear: a student who has never met this content can learn it from the question alone:
   after reading it once they understand the idea and the task, and have at least an idea
   of how to start. False when it assumes what it should teach, skips a step the student
   needs, buries the task in text, or leaves the student with no way in. Read it as that
   student would, not as an expert who already knows the topic.
2. fits: it matches how the lecture slides present the idea: the same definitions, rules,
   formulas and boundaries. A departure is fine only when the question states it (for
   example as an assumption).
3. taught: it needs nothing (an API, an idea) that only the lectures after the ones listed
   as taught cover.
4. targets: solving it needs the knowledge point it is about; a workaround that skips the
   concept (a library call that does the work, brute force where the task is the method)
   would not do. Look at the task and the reference solution.

The batch mode: "learn" (one knowledge point, the concept explained in the question) or
"review" (exam-style, points combined, little explanation). Judge each point against it.

For each point write `evidence` first: one or two sentences quoting the question or the
slides that decide it, and the problem if there is one; then `ok`. Be strict but fair:
false only for a real problem, named in the evidence.

Return only the JSON object."""


def user_prompt(inputs: Inputs) -> str:
    taught = ", ".join(inputs.taught) or "(not known)"
    parts = [f"Batch mode: {inputs.mode}",
             f"Lectures taught so far, in order: {taught}",
             "The question, as the student reads it:\n" + inputs.question,
             "The starting file the student gets (student.py):\n```python\n"
             + inputs.skeleton.rstrip() + "\n```",
             "The reference solution (never shown to the student):\n```python\n"
             + inputs.reference.rstrip() + "\n```",
             ("The lecture slides (the exercise's own pages are marked):\n" + inputs.slides
              if inputs.slides else "No slides are available for this exercise.")]
    return "\n\n".join(parts)


def judge(inputs: Inputs, model: Judge) -> tuple[Verdict, dict[str, int]]:
    """One verdict and the tokens it took; JudgeError if no reply fits the schema."""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_prompt(inputs)}]
    tokens = {"input": 0, "output": 0}
    problem = ""
    for attempt in range(1 + RETRIES):
        reply = model.chat(model.config, messages, purpose="judge", log_path=model.log_path,
                           max_tokens=MAX_TOKENS, response_schema=Verdict.model_json_schema(),
                           thinking=model.thinking)
        tokens["input"] += reply.input_tokens or 0
        tokens["output"] += reply.output_tokens or 0
        try:
            return Verdict.model_validate_json(reply.text), tokens
        except ValidationError as e:
            problem = "the reply does not fit the JSON schema: " + "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
        if attempt < RETRIES:
            # The model sees its own reply and what was wrong with it, and answers again.
            messages = [*messages[:2], {"role": "assistant", "content": reply.text},
                        {"role": "user", "content": f"That reply has problems: {problem}. "
                                                    "Return it again with them fixed."}]
    raise JudgeError(problem)


def model_name(model: Judge) -> str:
    return f"{model.config.name}: {model.config.model}"


def write(batch_dir: Path, exercise_id: str, workspace: Path, model: Judge,
          force: bool = False) -> tuple[str, Verdict | None]:
    """Judge one exercise into solutions/<id>/quality.json; return a one-line summary and
    the verdict (None when kept: the same model already judged the same inputs)."""
    inputs = gather(batch_dir, exercise_id, workspace)
    path = batch_dir / "solutions" / exercise_id / REPORT_FILE
    if not force and path.is_file():
        old = json.loads(path.read_text())
        if old.get("inputs") == inputs.key() and old.get("model") == model_name(model):
            return f"kept: {old.get('letters', '?')}", None
    verdict, tokens = judge(inputs, model)
    data = {
        "judged_at": datetime.now().isoformat(timespec="seconds"),
        "model": model_name(model),
        "inputs": inputs.key(),
        "letters": verdict.letters(),
        "points": verdict.model_dump(),
        "tokens": tokens,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return verdict.letters(), verdict
