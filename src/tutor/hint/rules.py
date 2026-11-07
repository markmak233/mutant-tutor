"""Layer 1 of the hint command: known low-level mistakes, recognised without a model.

Each rule looks at the student's source or the first failures of their test run and names
the line and the cause. The rules come from mistakes seen while tutoring (docs/design.md,
roadmap 3c). In review mode a hint names only the kind of error, as the tutoring rules do.

A C exercise has rules of its own (C_RULES): the compiler's first error, a missing #include
or prototype, a function that is not defined, a program that never finishes, and what the
sanitizers report (an index outside an array, memory used after free, a bad pointer,
integer overflow, ...), read from the run's output the way testkit_c words it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

from tutor.validation import csource
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


def first_rule(source: str, output: str, constraints: dict, mode: str,
               language: str = "python") -> Hint | None:
    """The first rule that applies, or None. A forbidden API comes after the source-level
    rules (the code must parse) and before the run's errors."""
    if language == "c":
        return _first_c_rule(source, output, constraints, mode)
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



# --- C exercises (docs/design.md, "C", step 4b) ------------------------------------------
# What the compiler and the sanitizers print, as testkit_c passes it into the test output.
# A place is "student.c:14" or "student.c:14:5", maybe with folders before the file name
# (gcc in the container may print them), which are left out of the hint.
_C_FILE = r"(?:[^\s:'\"`]*/)?([\w.-]+\.[ch])"
_C_ERROR = re.compile(_C_FILE + r":(\d+):\d+: (?:fatal )?error: ([^\n]+)")
# clang: "call to undeclared library function 'printf' ..." / "call to undeclared function
# 'helper'"; gcc: "implicit declaration of function 'printf'" (quotes may be curly).
_C_UNDECLARED = re.compile(_C_FILE + r":(\d+):\d+: (?:error|warning): (?:call to undeclared "
                           r"(library )?function|implicit declaration of function) "
                           r"[‘'](\w+)[’']")
# The header a compiler's note suggests: clang "include the header <stdio.h>", gcc
# "include '<stdio.h>'".
_C_HEADER_NOTE = re.compile(r"note: include (?:the header )?[‘']?<([\w./]+)>")
# The linker: gcc "undefined reference to `name'", macOS "_name", referenced from".
_C_UNDEFINED = re.compile(r"undefined reference to [`‘'](\w+)[’']|\"_(\w+)\", referenced from")
# One frame of a sanitizer's stack: "#2 0x00010413d3b8 in read_readings student.c:14".
_C_FRAME = re.compile(r"#\d+ 0x[0-9a-f]+ in (\w+) " + _C_FILE + r":(\d+)")
_C_UBSAN = re.compile(_C_FILE + r":(\d+):\d+: runtime error: ([^\n]+)")
# ASan's kind ("heap-buffer-overflow", "SEGV", "double-free"); LeakSanitizer's "detected".
_C_ASAN = re.compile(r"(?:ERROR|SUMMARY): (?:Address|Leak)Sanitizer:? (?:attempting )?([\w-]+)")
_STUDENT_FILES = ("student.c", "student.h")


def _c_place(output: str) -> str:
    """Where a sanitizer's report points: the first frame in the student's own file, else
    the first in any C file (driver.c, given), e.g. "line 14 of student.c, in read_readings";
    "" when the report names none."""
    frames = [(m[1], m[2], m[3]) for m in _C_FRAME.finditer(output)]
    if not frames:
        if found := _C_UBSAN.search(output):
            return f"line {found[2]} of {found[1]}"
        return ""
    function, file, line = next((f for f in frames if f[1] in _STUDENT_FILES), frames[0])
    return f"line {line} of {file}, in {function}"


def _c_full_width(source: str, _output: str) -> str | None:
    """A full-width character in the code itself, where the compiler stops; in a comment
    (the question is one) or a string it is fine."""
    code = _C_STRINGS.sub(lambda m: " " * len(m.group()), csource.without_comments(source))
    for number, line in enumerate(code.splitlines(), 1):
        for char in line:
            if char in FULL_WIDTH:
                return f"line {number}: {char!r} should be {FULL_WIDTH[char]!r}"
    return None


_C_STRINGS = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')


def _c_undeclared(_source: str, output: str) -> str | None:
    found = _C_UNDECLARED.search(output)
    if not found:
        return None
    where, name = f"line {found[2]} of {found[1]}", found[4]
    header = _C_HEADER_NOTE.search(output, found.end())
    if found[3] or header:  # a library function: its header is missing
        include = f"#include <{header[1]}>" if header else "the header that declares it"
        return f"{where}: `{name}` is used without its header; add {include} at the top"
    return (f"{where}: `{name}` is called before the compiler has seen it; declare it (a "
            "prototype) above that line, or define it earlier in the file")


def _c_undefined(_source: str, output: str) -> str | None:
    if found := _C_UNDEFINED.search(output):
        return found[1] or found[2]
    return None


def _c_compile(_source: str, output: str) -> str | None:
    if found := _C_ERROR.search(output):
        return f"line {found[2]} of {found[1]}: {found[3].strip()}"
    return None


def _c_report(pattern: str) -> Callable[[str, str], str | None]:
    """A finder for the sanitizer reports `pattern` matches (ASan's kind or UBSan's
    message); its detail is where the report points."""
    regex = re.compile(pattern)

    def found(_source: str, output: str) -> str | None:
        kinds = [m[1] for m in _C_ASAN.finditer(output)] + [m[3] for m in
                                                             _C_UBSAN.finditer(output)]
        hit = next((kind for kind in kinds if regex.search(kind)), None)
        if hit is None:
            return None
        return _c_place(output) or "where the report points"
    return found


C_RULES = [
    Rule("full-width", "a full-width character where C needs ASCII ({detail}); switch the "
         "input method to English for code", "a character C cannot read", _c_full_width),
    Rule("c-undeclared", "{detail}", "a function used before it is declared", _c_undeclared),
    Rule("c-undefined", "`{detail}` is called but not defined anywhere: check its name and "
         "parameters against the question (and student.h, if there is one)",
         "a function that is not defined", _c_undefined),
    Rule("c-compile", "the code does not compile: {detail}. Fix the first error first: later "
         "ones often follow from it", "a compile error", _c_compile),
    # A forbidden function comes here (source rules first, then the run's errors).
    Rule("c-timeout", "the program did not finish: a loop whose condition never becomes "
         "false, or a read waiting for input the test does not give (is the end of the input "
         "handled?)", "a program that does not finish",
         _in_output(r"did not finish within")),
    Rule("c-bounds", "{detail}: an index outside an array (the sanitizer stopped it there). An "
         "array of n elements has indexes 0 to n - 1: check the loop's bounds against the "
         "array's size", "an index outside an array",
         _c_report(r"buffer-overflow|buffer-underflow|container-overflow|out of bounds")),
    Rule("c-use-after-free", "{detail}: memory is used after free() gave it back; use it "
         "first, free it last", "memory used after it was freed",
         _c_report(r"use-after-free|use-after-return|use-after-scope")),
    Rule("c-double-free", "{detail}: the same memory is freed twice (or memory malloc did "
         "not give is freed); free each block exactly once", "a wrong free()",
         _c_report(r"double-free|bad-free|free on address")),
    Rule("c-bad-pointer", "{detail}: a NULL or invalid pointer is followed. Check it points "
         "at memory before using it: was it set, and was malloc's result checked?",
         "a bad pointer", _c_report(r"SEGV|null pointer|misaligned address")),
    Rule("c-stack-overflow", "{detail}: the stack ran out, usually a recursion that never "
         "reaches its base case (or a very large local array)", "a stack overflow",
         _c_report(r"stack-overflow")),
    Rule("c-leak", "{detail}: memory from malloc is never freed; free it once you are done "
         "with it", "memory not freed", _c_report(r"^detected$")),
    Rule("c-overflow", "{detail}: an int result goes past the largest int; use a wider type "
         "(long long) or check before it overflows", "an integer overflow",
         _c_report(r"signed integer overflow")),
    Rule("c-division", "{detail}: a division by zero; check the divisor first (an empty "
         "input?)", "a division by zero", _c_report(r"division by zero")),
    Rule("c-shift", "{detail}: a shift by a negative amount or by the type's width or more",
         "a bad shift", _c_report(r"shift exponent|left shift of")),
    Rule("c-undefined-behaviour", "{detail}: undefined behaviour, which the sanitizer "
         "stopped; read its message in the test output", "undefined behaviour",
         _c_report(r".")),
    Rule("c-crash", "the program was killed by {detail}: a bad pointer or index is the usual "
         "cause in C", "a crash", _in_output(r"SIG(?:SEGV|BUS|ABRT|FPE)")),
]
_C_SOURCE_RULES = 4  # full-width and the compiler's errors come before a forbidden function


def _first_c_rule(source: str, output: str, constraints: dict, mode: str) -> Hint | None:
    for rule in C_RULES[:_C_SOURCE_RULES]:
        if (detail := rule.found(source, output)):
            return _hint(rule, detail, mode)
    banned = constraints.get("forbidden_calls") or []
    if banned and (violations := csource.find_violations(source, {"forbidden_calls": banned})):
        text = (f"this exercise does not allow it: {violations[0]}" if mode == "learn"
                else "a function this exercise does not allow")
        return Hint("rule", "forbidden-api", text)
    for rule in C_RULES[_C_SOURCE_RULES:]:
        if (detail := rule.found(source, output)):
            return _hint(rule, detail, mode)
    return None
