"""Static checks of C source: read it, never run it (docs/design.md, "Other languages").

Two uses, as for Python (safety.py and api.py):
- find_hazards, the C part of rule `safe`. A static check cannot bound what C does (pointers,
  casts, system calls), so C exercises are validated only in a container. This check is for
  the other place generated C runs: the learner's own `pytest test_student.py` on their
  machine, which compiles the skeleton and any given driver.c. It stops the obvious:
  1. `#include <...>` only of the C standard headers (C_HEADERS) and a course's extra ones
     (workspace.toml `[safety] allow_headers`); `#include "..."` only of a file of the
     exercise itself, by its plain name; no other form (a macro, `#embed`, `#import`), since
     the compiler would read what it names;
  2. no inline assembly, and no `##` token pasting, which builds names this check cannot see;
  3. none of SYSTEM_NAMES (processes, the shell, sockets, loading code, raw system calls):
     a header check alone misses a function the code declares itself;
  4. no literal path that starts with "/" or "~" or contains ".." in a file-opening call.
- find_violations and defines, the C part of rule `api`: forbidden calls and required
  definitions on the reference.

Both work on tokens, with comments and the contents of string and character literals left
out, so a word in a comment or a printed message is not a use. No preprocessor runs: a macro
can still hide a name (the reason `##` is refused), so this is a filter, not a proof.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

# The C standard library's headers (C99 and C11), without <threads.h>: what an exercise of a
# programming course needs. A course using POSIX headers (<unistd.h> for getopt) lists them in
# [safety] allow_headers.
C_HEADERS = frozenset({
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h", "inttypes.h",
    "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h", "stdalign.h", "stdarg.h",
    "stdbool.h", "stddef.h", "stdint.h", "stdio.h", "stdlib.h", "stdnoreturn.h", "string.h",
    "tgmath.h", "time.h", "uchar.h", "wchar.h", "wctype.h",
})
# Names that start processes or shells, reach the network, load or map code, or make system
# calls directly. Most need a POSIX header, but C lets code declare a function itself.
SYSTEM_NAMES = frozenset({
    "system", "popen", "fork", "vfork", "clone",
    "execl", "execle", "execlp", "execv", "execve", "execvp", "execvpe", "fexecve",
    "posix_spawn", "posix_spawnp", "syscall", "ptrace", "kill",
    "socket", "connect", "bind", "listen", "accept", "socketpair",
    "dlopen", "dlsym", "mmap", "mprotect",
})
ASM_NAMES = frozenset({"asm", "__asm", "__asm__"})
# Calls whose string arguments are paths (standard C and the POSIX ones a course may allow).
PATH_CALLS = frozenset({"fopen", "freopen", "remove", "rename", "open", "creat", "opendir"})

_TOKEN = re.compile(r"""
      (?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))
    | (?P<string>"(?:\\.|[^"\\\n])*")
    | (?P<char>'(?:\\.|[^'\\\n])*')
    | (?P<name>[A-Za-z_]\w*)
    | (?P<paste>\#\#)
    | (?P<newline>\n)
    | (?P<other>\S)
    | \s
""", re.VERBOSE | re.DOTALL)
_INCLUDE = re.compile(r"#\s*(include|include_next|import|embed)\b(.*)")
_INCLUDE_NAME = re.compile(r'^(?:<([A-Za-z0-9_./+-]+)>|"([^"]+)")$')


@dataclass(frozen=True)
class Token:
    kind: str  # "name", "string", "char", "paste" or "other" (one punctuation character)
    text: str
    line: int


def tokens(source: str) -> list[Token]:
    """The tokens of `source` that matter here, each with its line, without comments."""
    found = []
    line = 1
    for match in _TOKEN.finditer(source):
        kind = match.lastgroup
        if kind and kind not in ("comment", "newline"):
            found.append(Token(kind, match.group(), line))
        line += match.group().count("\n")
    return found


def without_comments(source: str) -> str:
    """`source` with every comment replaced by spaces, its newlines kept, so line numbers
    stay; a "//" inside a string literal is not a comment."""
    parts = []
    for match in _TOKEN.finditer(source):
        text = match.group()
        parts.append(re.sub(r"[^\n]", " ", text) if match.lastgroup == "comment" else text)
    return "".join(parts)


def directives(source: str) -> list[tuple[int, str]]:
    """(line, text) of every preprocessor directive, comments removed and continued lines
    (a backslash at the end) joined."""
    lines = without_comments(source).split("\n")
    found = []
    i = 0
    while i < len(lines):
        start, text = i, lines[i]
        while text.endswith("\\") and i + 1 < len(lines):
            i += 1
            text = text[:-1] + " " + lines[i]
        if text.lstrip().startswith("#"):
            found.append((start + 1, text.strip()))
        i += 1
    return found


def find_hazards(source: str, headers: Collection[str], local_files: Collection[str]) -> list[str]:
    """One message per finding, e.g. "line 3: #include <unistd.h> is not allowed".
    `headers`: the allowed <...> headers; `local_files`: the names an `#include "..."` may
    give, the exercise's own files."""
    found = []
    for line, text in directives(source):
        include = _INCLUDE.match(text)
        if include:
            found += [f"line {line}: {m}" for m in _include_hazards(include, headers,
                                                                    local_files)]
    toks = tokens(source)
    for i, tok in enumerate(toks):
        if tok.kind == "paste":
            found.append(f"line {tok.line}: token pasting (##) is not allowed: it builds names "
                         "this check cannot see")
        elif tok.kind == "name" and tok.text in ASM_NAMES:
            found.append(f"line {tok.line}: inline assembly ({tok.text}) is not allowed")
        elif tok.kind == "name" and tok.text in SYSTEM_NAMES:
            # Any use, not only a call: `int (*run)(const char *) = system;` calls it too.
            # A variable of one's own with such a name is refused as well, so the message
            # says how to get past that.
            found.append(f"line {tok.line}: {tok.text} is not allowed (processes, network or "
                         "system calls; if it is a name of your own, rename it)")
        elif tok.kind == "name" and tok.text in PATH_CALLS and _next_is(toks, i, "("):
            found += [f"line {s.line}: {tok.text}({s.text}) reaches outside the exercise"
                      for s in _call_strings(toks, i + 1) if _outside(_literal(s.text))]
    return _sorted(found)


def _include_hazards(include: re.Match, headers: Collection[str],
                     local_files: Collection[str]) -> list[str]:
    keyword, rest = include.group(1), include.group(2).strip()
    if keyword != "include":
        return [f"#{keyword} is not allowed"]
    name = _INCLUDE_NAME.match(rest)
    if not name:
        return [f"#include {rest} is not allowed: name a <header> or a \"file\" of the "
                "exercise directly"]
    system, local = name.group(1), name.group(2)
    if system is not None:
        return [] if system in headers else [
            f"#include <{system}> is not allowed (allowed: the C standard headers and the "
            "course's [safety] allow_headers)"]
    if local in local_files:
        return []
    return [f'#include "{local}" is not allowed: only a file of the exercise, by its plain name']


def find_violations(source: str, constraints: Mapping[str, list]) -> list[str]:
    """Rule api for C: one message per violation, e.g. "line 12: uses qsort"; empty when the
    code keeps `constraints` (schema.CConstraints)."""
    unknown = set(constraints) - {"forbidden_calls", "required_definitions"}
    if unknown:
        return [f"unknown constraint key(s) for C: {', '.join(sorted(unknown))}"]
    forbidden = set(constraints.get("forbidden_calls", []))
    toks = tokens(source)
    found = [f"line {t.line}: uses {t.text}" for t in toks
             if t.kind == "name" and t.text in forbidden]
    defined = defines(toks)
    found += [f"does not define {name}" for name in constraints.get("required_definitions", [])
              if name not in defined]
    return found


def defines(toks: list[Token] | str) -> set[str]:
    """The functions `toks` (or a source) defines: a name, its parenthesised parameters, then
    a body `{` (a declaration ends with `;` instead)."""
    if isinstance(toks, str):
        toks = tokens(toks)
    names = set()
    depth = 0  # of braces: a function is defined at file level only
    for i, tok in enumerate(toks):
        if tok.text == "{":
            depth += 1
        elif tok.text == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and tok.kind == "name" and _next_is(toks, i, "("):
            close = _closing(toks, i + 1)
            if close is not None and close + 1 < len(toks) and toks[close + 1].text == "{":
                names.add(tok.text)
    return names


def _next_is(toks: list[Token], i: int, text: str) -> bool:
    return i + 1 < len(toks) and toks[i + 1].text == text


def _closing(toks: list[Token], open_at: int) -> int | None:
    """Index of the ")" matching the "(" at `open_at`."""
    depth = 0
    for j in range(open_at, len(toks)):
        if toks[j].text == "(":
            depth += 1
        elif toks[j].text == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def _call_strings(toks: list[Token], open_at: int) -> Iterable[Token]:
    """The string literals among a call's arguments."""
    close = _closing(toks, open_at)
    end = close if close is not None else len(toks)
    return [t for t in toks[open_at:end] if t.kind == "string"]


def _literal(text: str) -> str:
    return text[1:-1]


def _outside(path: str) -> bool:
    return path.startswith(("/", "~", "\\")) or ".." in path.replace("\\", "/").split("/")


def _sorted(found: list[str]) -> list[str]:
    return sorted(set(found), key=lambda m: (int(m.split(":")[0].split()[1]), m))
