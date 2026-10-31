"""Layer 2 of the hint command: does the student's code fail the way a known mistake does?

Every mutant of an exercise is one planted mistake with its bug written in its docstring
("<the bug>, <what goes wrong>"). A run's signature is which tests fail and with which
assertion lines; a student whose signature equals a mutant's most likely made that mistake.
Mutant signatures are computed once per exercise and kept in solutions/<id>/.hints/, keyed
by a hash of everything the run copies in (tests, data, helpers, the mutant) and the run's
settings, so a change to any of them runs the mutant again. The student sees the
mutant's description, never its code.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tutor.execution import RunResult, copied_files
from tutor.validation.checks import failure_blocks
from tutor.validation.exercise import ExerciseFiles

CACHE = Path(".hints") / "signatures.json"
Signature = dict[str, list[str]]  # failing test -> its assertion lines, normalised
_ADDRESS = re.compile(r"0x[0-9a-f]+")
_DURATION = re.compile(r"\d+\.\d+s\b")
_LINK = re.compile(r"^(?:so|and so|which means|meaning)\s+")


def signature(output: str) -> Signature:
    """Which tests failed and their `E` lines, with what differs between two runs of the
    same mistake (object addresses, timings) taken out."""
    return {name: [_normal(line) for line in lines if line.startswith("E")]
            for name, lines in failure_blocks(output)}


def _normal(line: str) -> str:
    text = " ".join(line.removeprefix("E").split())
    return _DURATION.sub("<s>", _ADDRESS.sub("<addr>", text))


def mutant_signatures(ex: ExerciseFiles, solution_dir: Path,
                      run: Callable[[Path], RunResult],
                      on_run: Callable[[str], None] | None = None,
                      settings: str = "") -> dict[str, Signature]:
    """Each mutant's signature, from the cache when nothing its run depends on changed.
    `run(candidate)` runs the exercise's tests with `candidate` as student.py; `settings`
    is what else shapes a run (timeout, environment)."""
    path = solution_dir / CACHE
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = {}
    result, changed = {}, False
    for mutant in ex.mutants:
        key = _key(ex.run_files(mutant), settings)
        entry = cached.get(mutant.stem)
        if not entry or entry.get("key") != key:
            if on_run:
                on_run(mutant.stem)
            entry, changed = {"key": key, "signature": signature(run(mutant).output)}, True
        result[mutant.stem] = entry["signature"]
        cached[mutant.stem] = entry
    if changed:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(cached, indent=1) + "\n", encoding="utf-8")
    return result


def _key(files: dict[str, Path], settings: str) -> str:
    digest = hashlib.sha256(settings.encode())
    for inside, path in copied_files(files).items():
        digest.update(inside.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class Match:
    mutant: str
    exact: bool  # same failing tests with the same assertion lines, not just the same tests
    similarity: float = 1.0  # of the assertion lines, 0-1


def matches(student: Signature, mutants: dict[str, Signature]) -> list[Match]:
    """Mutants failing the same tests, exact matches first, then by how alike the assertion
    lines read: a mistake whose output order varies (no coalesce) never matches exactly,
    even against itself. A mutant whose run failed nothing never matches."""
    found = [Match(name, sig == student, _similarity(sig, student))
             for name, sig in mutants.items() if sig and set(sig) == set(student)]
    return sorted(found, key=lambda m: (not m.exact, -m.similarity, m.mutant))


def _similarity(a: Signature, b: Signature) -> float:
    return difflib.SequenceMatcher(None, json.dumps(a, sort_keys=True),
                                   json.dumps(b, sort_keys=True)).ratio()


def indistinguishable(mutants: dict[str, Signature]) -> list[tuple[str, str]]:
    """Pairs of mutants the tests cannot tell apart (the same signature): a hint cannot
    either, and the exercise may lack a test."""
    names = sorted(mutants)
    return [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
            if mutants[a] and mutants[a] == mutants[b]]


def bug_of(mutant: Path) -> tuple[str, str]:
    """(the bug, what goes wrong) from the mutant's docstring "Mutant: <bug>, <effect>."."""
    try:
        doc = ast.get_docstring(ast.parse(mutant.read_text(encoding="utf-8"))) or ""
    except SyntaxError:
        doc = ""
    text = doc.removeprefix("Mutant:").strip().rstrip(".")
    bug, _, effect = text.partition(", ")
    # "..., so the first value is lost": the caller adds its own "so".
    return bug, _LINK.sub("", effect)
