"""Layer 3 of the hint command: a model's hint, when neither a rule nor a known mistake fits.

The model sees what the student sees (student.py with the question, hints.md, the
constraints) and their first real error, never the reference, the mutants or the oracle.
It replies with a diagnosis, which only the code reads, and a hint, which the student
reads. Asking again with the same code makes the hint more specific (levels 1-3); a
review batch never gets code at any level.

The prompt lists what gives the exercise away, and a deterministic check rejects a hint
that does it anyway: a code block (one or two lines only at level 3 of a learn batch), a
line of the reference the student has not seen, or code close to the reference. A
rejected hint is asked for once more with the reason; a second rejection falls back to
the first error and hints.md.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from tutor import llm
from tutor.hint.rules import Hint
from tutor.validation.checks import failure_excerpt

MAX_LEVEL = 3
MAX_TOKENS = 8000
SIMILAR = 0.8  # a code span this close to some reference lines is the reference
MIN_LINE = 15  # characters (spaces removed) for a reference line to count as giving away
_JAVA_FRAME = re.compile(r"^E?\s*(at [\w$.<>]+\(.*\)|\.\.\. \d+ more)\s*$")
_HOME = re.compile(r"/(?:Users|home)/[^/\s]+")
_CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


class HintReply(BaseModel):
    diagnosis: str = Field(description=(
        "For the checker only, never shown to the student: what is wrong and why, as exact "
        "as you like. Write it first."))
    hint: str = Field(description=(
        "Shown to the student: at most four sentences, following the level and the rules."))


class HintRefused(Exception):
    """No model hint: `kind` is "blocked" (it gave code away twice) or "error"."""

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind


@dataclass(frozen=True)
class HintModel:
    config: llm.LLMConfig
    chat: Callable = llm.chat
    log_path: Path | None = None


RULES = """\
Rules. A checker rejects a hint that breaks one, and you are asked again:
1. Never write the solution or any part of it as code. {blocks}
2. Name functions, methods and variables in backticks (`reduceByKey`, `total`), but never
   write a call with the arguments that would fix the bug, and never a corrected version
   of the student's line (except as level 3 allows).
3. Never copy a line of a correct solution, even inside backticks; describe what it does
   in words.
4. Do not give expected outputs beyond what the question and the tests already show.
5. One mistake per hint: the first real error, not every problem in the file.
6. hints.md is already open to the student: do not repeat it; if one of its hints covers
   the mistake, say "Hint N in hints.md" instead."""

LEVELS = {
    1: "Level 1: name the concept behind the mistake and the direction to look in (e.g. "
       "why an RDD cannot be looped over on the driver). Do not say where in the code it is.",
    2: "Level 2 (the student asked again without changing the code): say which function or "
       "step holds the mistake and what is wrong there, still in words.",
    3: "Level 3 (third request, same code): for a low-level mistake (a typo, a wrong "
       "argument, a missing return, a wrong output format) you may show the one corrected "
       "line, at most two lines in one code block. For a concept mistake stay at level 2 and "
       "explain the concept again with a different, small example in words.",
}

REVIEW = ("This is a review batch: the student wants to find the fix alone. At level 1 name "
          "only the kind of mistake; at levels 2 and 3 say where it is and what kind it is. "
          "Never show code, at any level.")

SPEC = """\
You are a programming tutor. A student is solving the exercise below and asked for a hint
because their tests fail. A good hint lets them find the fix themselves: it explains the
concept they are missing and points in the right direction, and it is short.

{rules}

{level}

Reply with JSON: "diagnosis" first (for the checker only, never shown: what is wrong and
why, as exact as you like), then "hint" (shown to the student)."""


def allowed_lines(mode: str, level: int) -> int:
    """Lines of code a hint may show: only a learn batch's level 3 shows any."""
    return 2 if mode == "learn" and level >= MAX_LEVEL else 0


def system_prompt(mode: str, level: int) -> str:
    lines = allowed_lines(mode, level)
    blocks = (f"No code block, except one of at most {lines} lines as level 3 allows."
              if lines else "No code blocks at all.")
    level_text = LEVELS[min(level, MAX_LEVEL)] if mode == "learn" else (
        LEVELS[min(level, MAX_LEVEL)].split(":")[0] + ". " + REVIEW)
    return SPEC.format(rules=RULES.format(blocks=blocks), level=level_text)


def user_prompt(student: str, hints: str, constraints: dict, error: str) -> str:
    banned = {k: v for k, v in constraints.items() if v}
    return "\n\n".join([
        "## student.py (the question is its docstring; the rest is the student's code)",
        student,
        "## Constraints from the exercise's metadata",
        str(banned) if banned else "(none)",
        "## The first real error from their test run",
        error,
        "## hints.md (already open to the student)",
        hints,
    ])


def first_error(output: str) -> str:
    """The failing tests' assertion lines, without Java stack frames or home folders."""
    text = failure_excerpt(output, per_test=10, limit=2000) or output[-2000:]
    lines = [line for line in text.splitlines() if not _JAVA_FRAME.match(line)]
    return _HOME.sub("~", "\n".join(lines))


def leak(hint: str, reference: str, visible: str, allowed: int) -> str | None:
    """Why `hint` gives code away, or None. `visible`: what the student already has
    (student.py, tests, hints.md), whose lines may be quoted freely."""
    blocks = _CODE_BLOCK.findall(hint)
    for block in blocks:
        count = sum(1 for line in block.splitlines() if line.strip())
        if count > allowed:
            return (f"it has a code block of {count} lines; " +
                    (f"at most {allowed} here" if allowed else "no code blocks here"))
    seen = _squash(visible)
    shown = _squash(hint)
    for line in _code_lines(reference):
        if len(line) >= MIN_LINE and line not in seen and line in shown:
            return "it repeats a line of the solution; describe what the line does in words"
    reference_lines = _code_lines(reference)
    for span in [*blocks, *_INLINE_CODE.findall(hint)]:
        squashed = _squash(span)
        if len(squashed) >= MIN_LINE and squashed not in seen and _close(squashed,
                                                                          reference_lines):
            return "its code is close to the solution's; describe the idea in words"
    return None


def _squash(text: str) -> str:
    return "".join(text.split())


def _code_lines(source: str) -> list[str]:
    """The reference's lines without comments or blank lines, spaces removed."""
    lines = []
    for line in source.splitlines():
        code = re.sub(r"\s+#.*$", "", line).strip()
        if code and not code.startswith("#"):
            lines.append(_squash(code))
    return lines


def _close(span: str, reference_lines: list[str]) -> bool:
    """Some run of consecutive reference lines reads like `span`."""
    for start in range(len(reference_lines)):
        window = ""
        for line in reference_lines[start:]:
            window += line
            if difflib.SequenceMatcher(None, span, window).ratio() >= SIMILAR:
                return True
            if len(window) > 2 * len(span):
                break
    return False


def model_hint(model: HintModel, *, student: str, hints: str, constraints: dict,
               output: str, reference: str, visible: str, mode: str, level: int) -> Hint:
    """The model's hint at `level`, or HintRefused."""
    level = min(level, MAX_LEVEL)
    allowed = allowed_lines(mode, level)
    messages = [{"role": "system", "content": system_prompt(mode, level)},
                {"role": "user", "content": user_prompt(student, hints, constraints,
                                                        first_error(output))}]
    reason = ""
    for _ in range(2):
        try:
            reply = model.chat(model.config, messages, purpose="hint", log_path=model.log_path,
                               max_tokens=MAX_TOKENS, response_schema=HintReply.model_json_schema())
        except llm.LLMError as e:
            raise HintRefused("error", str(e)) from None
        try:
            hint = HintReply.model_validate_json(reply.text).hint.strip()
        except ValidationError:
            reason = "the reply was not the JSON asked for"
            hint = ""
        else:
            reason = leak(hint, reference, visible, allowed) or ""
        if not reason:
            return Hint("llm", f"level-{level}", hint)
        messages += [{"role": "assistant", "content": reply.text},
                     {"role": "user", "content": f"The checker rejected the hint: {reason}. "
                                                 "Write it again, following the rules."}]
    raise HintRefused("blocked", reason)
