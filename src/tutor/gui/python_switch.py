"""Another Python for a course's .venv, from the Environment page's check (docs/design.md,
"Version conflicts").

When the check finds that a package does not run on the course's Python (a known conflict,
or an import of a module that Python removed), the page offers the other Pythons on this
machine (tutor.pythons). Which suit the course is worked out by code, not by a model: the
known conflicts (tutor.compat) on each version, and each removed import the check reported,
which rules out every version from the one that removed it on.

The switch is a task: `python -m tutor.gui.python_switch <course> <python>` makes the .venv
anew with that interpreter (`tutor.workspace init --replace-venv`: the .venv before comes back
if it fails), and only then writes [requires] python, so a failed switch changes nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from tutor import compat, pythons, workspace
from tutor.config import WorkspaceError
from tutor.gui import toml_edit

IMPORTS = " imports"  # envcheck's probe names its rows "<package> imports"


@dataclass(frozen=True)
class Option:
    """A Python on this machine, as the page offers it."""

    found: pythons.Found
    current: bool  # the .venv's own
    problems: list[str] = field(default_factory=list)  # why it does not suit this course

    @property
    def offered(self) -> bool:
        return not self.current and not self.problems


def related(results: list[dict]) -> bool:
    """Whether the check found something another Python could fix: a known conflict with
    this one (its row is named python) or a removed module a package imports."""
    return any(not r.get("ok")
               and (r.get("name") == "python" or r.get("name", "").endswith(IMPORTS))
               for r in results)


def options(requires: dict[str, str], results: list[dict], current: str | None,
            find: Callable[[], list[pythons.Found]] | None = None) -> list[Option]:
    """Each Python found, with why it does not suit the course ([] when it does). `results`:
    the check's rows; `current`: the .venv's version ("3.13.3"), None when unknown."""
    # A removed module a package imports rules out every Python from its removal on.
    removed = {(r["name"][:-len(IMPORTS)], r["required"]) for r in results
               if r.get("name", "").endswith(IMPORTS)
               and r.get("required") in compat.REMOVED_MODULES}
    found = []
    for python in (find or pythons.find)():  # looked up now, so a stand-in can replace it
        problems = compat.conflicts(requires, python.minor)
        problems += [f"{package} imports `{module}`, which Python "
                     f"{compat.REMOVED_MODULES[module]} removed"
                     for package, module in sorted(removed)
                     if _minor(python.minor) >= _minor(compat.REMOVED_MODULES[module])]
        found.append(Option(python, python.version == current, problems))
    return found


def _minor(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".")[:2])


def command(root: Path, python: Path) -> list[str]:
    """The task's command: this module, run with the GUI's own interpreter (it has tomlkit)."""
    return [sys.executable, "-m", "tutor.gui.python_switch", str(root), str(python)]


def record(root: Path, minor: str) -> list[str]:
    """[requires] python = minor, comments kept, the file before as workspace.toml.bak; []
    when written, else the problem."""
    requires = {k: str(v) for k, v in toml_edit.read(root).get("requires", {}).items()}
    expected = {**requires, "python": minor}

    def change(doc) -> None:
        if "requires" not in doc:
            doc["requires"] = tomlkit.table()
        toml_edit.put(doc["requires"], "python", minor)

    return toml_edit.edit(root, "requires", change, expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.gui.python_switch",
                                     description="Make a course's .venv anew with another Python.")
    parser.add_argument("course", type=Path, help="the course folder")
    parser.add_argument("python", type=Path, help="the interpreter, as tutor.pythons found it")
    args = parser.parse_args(argv)
    found = pythons.ask(args.python)
    if found is None:
        print(f"python_switch: {args.python} does not run as a Python", file=sys.stderr)
        return 1
    print(f"Making the .venv anew with Python {found.version} ({args.python}).", flush=True)
    try:
        workspace.init(args.course, python=args.python, python_version=found.minor,
                       replace_venv=True, say=lambda line: print(line, flush=True))
    except WorkspaceError as e:
        print(f"python_switch: {e}", file=sys.stderr)
        return 1
    if problems := record(args.course.expanduser().resolve(), found.minor):
        print(f"python_switch: the .venv now has Python {found.version}, but [requires] could "
              f"not be written: {problems[0]} Set python = \"{found.minor}\" there by hand.",
              file=sys.stderr)
        return 1
    print(f'[requires] python = "{found.minor}" (the file before is workspace.toml.bak). '
          "A course whose tests run in a container needs its test image built again.",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
