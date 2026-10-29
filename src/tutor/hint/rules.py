"""Layer 1 of the hint command: known low-level mistakes, recognised without a model.

Each rule looks at the student's source or the first failures of their test run and names
the line and the cause. The rules come from mistakes seen while tutoring (docs/design.md,
roadmap 3c). In review mode a hint names only the kind of error, as the tutoring rules do.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

from tutor.validation.api import find_violations

# Punctuation a Chinese input method types in place of the ASCII one Python needs.
FULL_WIDTH = {"，": ",", "：": ":", "（": "(", "）": ")", "“": '"', "”": '"', "‘": "'",
              "’": "'", "；": ";", "【": "[", "】": "]", "　": " "}


@dataclass(frozen=True)
class Hint:
    layer: str  # "rule", "mutant" or "none"
    key: str  # which rule or mutant, for the attempt log
    text: str


@dataclass(frozen=True)
class Rule:
    key: str
    learn: str  # may name the cause and the direction
    review: str  # the kind of error only
    found: Callable[[str, str], str | None]  # (source, pytest output) -> detail or None


def _full_width(source: str, _output: str) -> str | None:
    """Only the character Python stops at: in comments and strings full-width punctuation
    is fine, and a hint about it would hide the real mistake."""
    try:
        ast.parse(source)
    except SyntaxError as e:
        found = re.search(r"U\+([0-9A-F]{4,6})", e.msg)
        char = chr(int(found[1], 16)) if found else ""
        if char in FULL_WIDTH:
            return f"line {e.lineno}: {char!r} should be {FULL_WIDTH[char]!r}"
    return None


def _syntax(source: str, _output: str) -> str | None:
    try:
        ast.parse(source)
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"
    return None


def _in_output(pattern: str) -> Callable[[str, str], str | None]:
    regex = re.compile(pattern)

    def found(_source: str, output: str) -> str | None:
        match = regex.search(output)
        return match.group(0) if match else None
    return found


RULES = [
    Rule("full-width", "a full-width character where Python needs ASCII ({detail}); "
         "switch the input method to English for code", "a character Python cannot read",
         _full_width),
    Rule("syntax", "student.py does not parse ({detail})", "a syntax error", _syntax),
    Rule("python-version", "Spark's workers run another Python than the tests; set "
         "PYSPARK_PYTHON to this venv's python (docs/setup)", "an environment problem",
         _in_output(r"PYTHON_VERSION_MISMATCH")),
    Rule("todo-left", "a function still raises NotImplementedError: its TODO is not "
         "written yet", "an unfinished function", _in_output(r"NotImplementedError")),
    Rule("rdd-on-driver", "an RDD is used like a Python list on the driver ({detail}). An "
         "RDD is a plan the cluster runs: transform it, or bring results back with an "
         "action such as collect()", "an RDD used like a local collection",
         _in_output(r"'(?:Pipelined)?RDD' object is not (?:iterable|subscriptable)")),
    Rule("spark-context-left", "a SparkContext is still running from an earlier test, "
         "which crashed before sc.stop(); stop it in a finally block",
         "a Spark session left open", _in_output(r"Cannot run multiple SparkContexts")),
    Rule("tuple-output", "the output lines are Python tuples such as ('a', 1): format "
         "each record as the question shows before saving it", "output in the wrong format",
         _in_output(r"unexpected: \[\s*['\"]\(")),
    Rule("not-saved", "nothing was saved to outputPath itself: save the result there with "
         "one saveAsTextFile(outputPath)", "no output where the tests look",
         _in_output(r"no output was saved to outputPath")),
]


def first_rule(source: str, output: str, constraints: dict, mode: str) -> Hint | None:
    """The first rule that applies, or None. A forbidden API comes after the source-level
    rules (the code must parse) and before the run's errors."""
    for rule in RULES[:2]:
        if (detail := rule.found(source, output)):
            return _hint(rule, detail, mode)
    banned = {k: v for k, v in constraints.items() if k in ("forbidden_calls",
                                                             "forbidden_imports")}
    if banned and (violations := find_violations(source, banned)):
        text = (f"this exercise does not allow it: {violations[0]}" if mode == "learn"
                else "an API this exercise does not allow")
        return Hint("rule", "forbidden-api", text)
    for rule in RULES[2:]:
        if (detail := rule.found(source, output)):
            return _hint(rule, detail, mode)
    return None


def _hint(rule: Rule, detail: str, mode: str) -> Hint:
    text = rule.learn.format(detail=detail) if mode == "learn" else rule.review
    return Hint("rule", rule.key, text)
