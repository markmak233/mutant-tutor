"""The Pythons on this machine that a course's .venv can be made with (docs/design.md,
"Version conflicts"): what the Environment page offers when a course needs another Python.

Found, never installed: on PATH as python3.11, python3.12, ... (and python3), and on Windows
through the py launcher's list. Each is asked for its own version, since a name can point at
anything. Only versions this project runs on (3.11 or later) are kept.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LOWEST = (3, 11)  # pyproject.toml's requires-python
_NEWEST_MINOR = 20  # names python3.11 .. python3.20 are looked for
# Its version, and the interpreter a venv's python stands for (a venv found on PATH is not the
# Python itself: the new .venv is made from the base, as `python -m venv` would anyway).
_ASK = ("import sys; print('%d.%d.%d' % sys.version_info[:3]); "
        "print(getattr(sys, '_base_executable', '') or sys.executable)")
# The py launcher's list (`py -0p`): " -V:3.12 *        C:\Python312\python.exe"; the path
# may hold spaces, so it is everything after the tag and the default's star.
_PY_LIST = re.compile(r"^\s*-V:\S+\s+(?:\*\s+)?(.+?)\s*$")


@dataclass(frozen=True)
class Found:
    version: str  # "3.12.4"
    path: Path

    @property
    def minor(self) -> str:
        """"3.12": what [requires] python holds."""
        return ".".join(self.version.split(".")[:2])


def ask(python: Path, run: Callable = subprocess.run) -> Found | None:
    """The interpreter's own answer: its version and its base interpreter, or None when it
    does not run."""
    try:
        done = run([str(python), "-c", _ASK], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or len(lines) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", lines[0]):
        return None
    return Found(lines[0], Path(lines[1]))


def _candidates(which: Callable, run: Callable) -> list[Path]:
    names = [f"python3.{minor}" for minor in range(LOWEST[1], _NEWEST_MINOR + 1)] + ["python3"]
    found = [Path(path) for name in names if (path := which(name))]
    if sys.platform == "win32" and (launcher := which("py")):
        try:
            listed = run([launcher, "-0p"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.TimeoutExpired):
            listed = ""
        found += [Path(m.group(1)) for line in listed.splitlines()
                  if (m := _PY_LIST.match(line))]
    return found


def find(which: Callable = shutil.which, run: Callable = subprocess.run) -> list[Found]:
    """Every Python 3.11 or later found, newest first, each once (two names for one file,
    such as python3 and python3.12, count once)."""
    seen: dict[Path, Found] = {}
    for path in _candidates(which, run):
        try:
            real = path.resolve()
        except OSError:
            continue
        if real in seen:
            continue
        found = ask(path, run)
        if found and tuple(int(p) for p in found.version.split(".")[:2]) >= LOWEST:
            seen[real] = found
    by_version: dict[str, Found] = {}
    for found in seen.values():  # one per version: the first name found for it
        by_version.setdefault(found.version, found)
    return sorted(by_version.values(),
                  key=lambda f: tuple(int(p) for p in f.version.split(".")), reverse=True)
