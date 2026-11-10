"""The modules and headers a course's lecture code uses beyond what exercises may use by
default, found in the cached lectures with no model (owner, 2026-11-10; Environment step 1).

Generated exercises may import only the core modules (validation/safety.CORE_IMPORTS), the
framework's, and what `[safety] allow_imports` names; C exercises may include only the
standard headers (validation/csource.C_HEADERS) and `[safety] allow_headers`. The [requires]
draft proposes pip packages, and ticking one allows its module; but a module that is not a
pip package (`subprocess`, `socket`, `threading`, `struct`: the standard library, which the
draft leaves out on purpose) was never proposed, so a course that teaches it got exercises
the static check refused. The same for a header a course ships. Import and include lines
are exact text, so this reads them with a regular expression: no cost, and every candidate
comes with the lecture, the page and the line that show it; the owner ticks each one on the
page, as widening the check is a safety decision (design.md, "Decisions not to do").

A name that starts processes or shells, reaches the network, loads code or maps memory is
marked: allowing it lets generated exercises do that, so it is for a course that teaches it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tutor.materials import cache
from tutor.validation.csource import C_HEADERS
from tutor.validation.safety import (
    CORE_IMPORTS,
    FRAMEWORK_IMPORTS,
    workspace_allow_headers,
    workspace_allow_imports,
)

# `import a.b as c`, `import a, b` and `from a.b import c`, at the start of a line (code on a
# slide, in a block or not); the top-level module is what allow_imports holds.
_IMPORT = re.compile(r"^\s*(?:import\s+([\w.]+(?:\s*,\s*[\w.]+)*)|from\s+([\w.]+)\s+import\b)",
                     re.MULTILINE)
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([\w./-]+\.h)[>"]', re.MULTILINE)
# Standard-library modules that start processes or shells, reach the network, load or run
# code, or touch the interpreter: allowing one is a decision about what exercises may do.
RISKY = frozenset({
    "subprocess", "os", "socket", "socketserver", "http", "urllib", "ftplib", "smtplib",
    "telnetlib", "ssl", "asyncio", "multiprocessing", "threading", "concurrent", "ctypes",
    "importlib", "runpy", "code", "codeop", "pickle", "shelve", "marshal", "signal",
    "mmap", "pty", "tty", "termios", "resource", "gc", "inspect", "sys", "builtins",
    "shutil", "glob", "io", "select", "selectors", "xmlrpc", "webbrowser", "platform",
    "sysconfig", "pkgutil", "zipimport", "atexit", "faulthandler", "tracemalloc",
})
FRAMEWORK_MODULES = frozenset().union(*FRAMEWORK_IMPORTS.values())


@dataclass(frozen=True)
class Source:
    lecture: str
    page: int
    line: str  # the import or include line, as the slide shows it


@dataclass
class Found:
    """One module (kind "import") or header (kind "include") the lectures' code uses and
    exercises may not: with every place it was seen, most-cited first in the list."""
    kind: str  # "import" or "include"
    name: str
    sources: list[Source] = field(default_factory=list)
    risky: bool = False  # RISKY: it does something allowing it lets exercises do

    @property
    def first(self) -> Source:
        return self.sources[0]


def found_in_lectures(workspace: Path) -> list[Found]:
    """Every module and header the cached lectures' code uses that the course does not
    allow yet, with its sources; imports first, each kind most-cited first."""
    from tutor.materials.requires import lecture_files

    allowed_modules = CORE_IMPORTS | FRAMEWORK_MODULES | workspace_allow_imports(workspace)
    allowed_headers = C_HEADERS | workspace_allow_headers(workspace)
    found: dict[tuple[str, str], Found] = {}
    for path in lecture_files(workspace):
        lecture = cache.lecture_name(path) or path.name
        for number, entry in sorted(cache.load(path).items()):
            for line, names in _imports(entry.body):
                for name in names:
                    if name in allowed_modules:
                        continue
                    key = ("import", name)
                    item = found.setdefault(key, Found("import", name, risky=name in RISKY))
                    item.sources.append(Source(lecture, number, line))
            for line, header in _includes(entry.body):
                if header in allowed_headers:
                    continue
                item = found.setdefault(("include", header), Found("include", header))
                item.sources.append(Source(lecture, number, line))
    return sorted(found.values(), key=lambda f: (f.kind != "import", -len(f.sources), f.name))


def _imports(text: str) -> list[tuple[str, list[str]]]:
    """(the line, the top-level modules it imports) for each import line of a page."""
    out = []
    for match in _IMPORT.finditer(text):
        names = match.group(1).split(",") if match.group(1) else [match.group(2)]
        tops = [n.strip().split(".")[0] for n in names if n.strip()]
        end = text.find("\n", match.start())
        line = text[match.start():end if end != -1 else len(text)].strip()  # the whole line
        out.append((line, [t for t in tops if t]))
    return out


def _includes(text: str) -> list[tuple[str, str]]:
    """(the line, the header) for each #include of a page; a path keeps its last part."""
    return [(m.group(0).strip(), m.group(1).rsplit("/", 1)[-1]) for m in _INCLUDE.finditer(text)]
