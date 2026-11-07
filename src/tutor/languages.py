"""Which languages a course's exercises are written in (docs/design.md, "C").

Python needs nothing declared. Another language is declared in workspace.toml by a table of its
own, which only the owner writes (the GUI's Environment page offers it), e.g.

    [languages.c]
    flags = ["-Wall", "-std=c99"]

Supported besides Python: C. A table for any other language, or a [requires] entry naming the
toolchain of one (javac, rustc, ...), is reported as not supported yet, instead of being taken
for a pip package and failing later.

suggested_c_flags reads the course's cached lectures for the gcc command lines they show and
proposes their flags, with the line it took them from; no model is asked.

student_path tells which file of an exercise folder is the learner's code. It lives here, in
a module of the standard library only, because the attempts plugin (loaded in every pytest
run) and the progress report need it and must stay light.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from tutor.testkit_c import check_flags

SUPPORTED = ("c",)
C_KEYS = frozenset({"flags"})
# The suffix of each language's code files: student<suffix>, skeleton<suffix>,
# reference<suffix>, mutants/*<suffix> (validation/exercise.py lays the files out).
CODE_SUFFIX = {"python": ".py", "c": ".c"}
# Toolchains of languages not supported yet, by the name a [requires] entry would give them.
TOOLCHAINS = {
    "javac": "Java", "maven": "Java", "mvn": "Java", "gradle": "Java",
    "g++": "C++", "clang++": "C++", "cmake": "C++",
    "rustc": "Rust", "cargo": "Rust", "go": "Go", "ghc": "Haskell", "cabal": "Haskell",
    "node": "JavaScript", "nodejs": "JavaScript", "npm": "JavaScript", "tsc": "TypeScript",
    "dotnet": "C#", "rscript": "R", "octave": "MATLAB/Octave", "matlab": "MATLAB",
    "kotlinc": "Kotlin", "scalac": "Scala", "sbt": "Scala", "swift": "Swift",
    "spim": "assembly", "mars": "assembly", "rars": "assembly",
}
_SUPPORTED_TEXT = "exercises can be in Python or C"


def declared(data: Mapping) -> list[str]:
    """The languages the parsed workspace.toml declares besides Python, as written."""
    tables = data.get("languages", {})
    return list(tables) if isinstance(tables, dict) else []


def is_c(data: Mapping) -> bool:
    """Whether the course declares C ([languages.c])."""
    return "c" in declared(data)


def student_path(exercise_dir: Path) -> Path:
    """The learner's code file in an exercise folder: student.c in a C exercise, else
    student.py (also when neither is there, so a message names the usual file). Told by the
    file that is there, not by metadata.json: that sits in solutions/, and the learner's
    side (the GUI's pages, the attempts plugin) reads only the exercise folder."""
    for suffix in CODE_SUFFIX.values():
        if (path := exercise_dir / f"student{suffix}").is_file():
            return path
    return exercise_dir / "student.py"


def problems(data: Mapping) -> list[str]:
    """What is wrong with the parsed workspace.toml's [languages], one line each."""
    tables = data.get("languages", {})
    if not isinstance(tables, dict):
        return ["[languages] must be a table of languages, e.g. [languages.c]"]
    found = []
    for name, table in tables.items():
        if name not in SUPPORTED:
            found.append(f"[languages.{name}]: {name} is not supported yet ({_SUPPORTED_TEXT})")
            continue
        if not isinstance(table, dict):
            found.append(f"[languages.{name}] must be a table")
            continue
        unknown = sorted(set(table) - C_KEYS)
        if unknown:
            found.append(f"[languages.{name}]: unknown key {', '.join(unknown)} (known: flags)")
        if "flags" in table:
            try:
                check_flags(table["flags"], f"[languages.{name}] flags")
            except ValueError as e:
                found.append(str(e))
    return found


def toolchain_problem(name: str) -> str | None:
    """Why a [requires] name is refused, when it is the toolchain of a language not supported
    yet; None otherwise."""
    language = TOOLCHAINS.get(name.lower())
    if language is None:
        return None
    return (f"[requires] {name} is a {language} toolchain, and {language} is not supported yet "
            f"({_SUPPORTED_TEXT})")


# --- the flags the lectures compile with ------------------------------------------------------

_COMMAND = re.compile(r"(?<![\w.-])(?:gcc|cc|clang)(?![\w.+-])([^\n`]*)")
_FLAG = re.compile(r"(?<!\S)(-std=[\w+]+|-W[\w=-]+|-pedantic(?:-errors)?|-ansi)(?!\S)")


@dataclass(frozen=True)
class FlagSuggestion:
    """The flags most of the lectures' compile lines use, and where. `count`: how many lines
    use exactly these; `lines`: how many compile lines there are."""
    flags: list[str]
    lecture: str
    page: str
    line: str
    count: int
    lines: int


def suggested_c_flags(lecture_pages: Iterable[tuple[str, str, str]]) -> FlagSuggestion | None:
    """From (lecture, page label, text) of the cached lectures: the set of standard and
    warning flags the most compile lines share, in their first line's order; None when no
    line shows any."""
    # By the set of flags, so "-Wall -std=c99" and "-std=c99 -Wall" count together.
    seen: Counter[frozenset[str]] = Counter()
    # Each set's first line: (its flags in the order written, lecture, page, the line).
    first: dict[frozenset[str], tuple[list[str], str, str, str]] = {}
    total = 0
    for lecture, page, text in lecture_pages:
        for line in text.splitlines():
            match = _COMMAND.search(line)
            flags = list(dict.fromkeys(_FLAG.findall(match.group(1)))) if match else []
            if not flags:
                continue
            total += 1
            key = frozenset(flags)
            seen[key] += 1
            first.setdefault(key, (flags, lecture, page, line.strip()))
    if not seen:
        return None
    key, count = seen.most_common(1)[0]
    flags, lecture, page, line = first[key]
    return FlagSuggestion(flags, lecture, page, line, count, total)


def lecture_pages(workspace: Path) -> list[tuple[str, str, str]]:
    """(lecture, page label, text) of every cached lecture page, for suggested_c_flags."""
    from tutor.materials import cache  # here: materials imports far more than this module needs
    from tutor.materials.requires import lecture_files

    pages = []
    for path in lecture_files(workspace):
        lecture = path.name.removesuffix(".md")
        for _, entry in sorted(cache.load(path).items()):
            pages.append((lecture, f"p.{entry.number}", entry.body))
    return pages
