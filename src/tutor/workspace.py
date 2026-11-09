"""Create a course workspace: `python -m tutor.workspace init DIR [--course NAME]`.

One command instead of the manual steps in docs/setup (docs/design.md, "Public repo vs
private data"): the folder and lectures/, a minimal workspace.toml, the course's own .venv
with this package, and the VS Code files that activate it in the folder's terminals.

Running it again fills in only what is missing: an existing file is kept, even a wrong one,
as the owner may have edited it. Nothing outside the folder changes: no system package, no
`~/.zshrc`. The only thing installed is Python packages into the folder's own .venv: this
package, and the pip packages workspace.toml's [requires] names (owner, 2026-11-06), so
running it again after editing [requires] is how a course's venv catches up; the GUI's
Environment page runs it for that.

`--python PATH --replace-venv` makes the .venv anew with another Python already on this
machine (tutor.pythons finds them), for a course whose packages do not run on the one it has
(docs/design.md, "Version conflicts"). The .venv before is kept as .venv.old until the new one
is installed, and put back if the install fails.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
import venv
from collections.abc import Callable
from pathlib import Path

from tutor.config import WORKSPACE_FILE, WorkspaceError, _enclosing_git_repo
from tutor.envcheck import pip_requirements, requires_problems
from tutor.package_bounds import merge

DEFAULT_EXTRAS = "gen,dev"  # generating exercises, and pytest + hypothesis to validate them
REPO = Path(__file__).resolve().parents[2]

_COURSE = re.compile(r"^[\w.-]+$")
_EXTRAS = re.compile(r"^[a-z]+(,[a-z]+)*$")

# The minimal workspace.toml. [isolation] stays a comment: the container needs Docker and a
# built image, which this command does not install, and a course that has not chosen runs on
# the host with a warning naming both choices (tutor.container).
WORKSPACE_TOML = """\
course = "{course}"

[requires]
python = "{version}"

# Where exercise code runs (README, "Where exercise code runs"). With Docker running and the
# image built (`python -m tutor.container build`), uncomment to run every test in a container:
# [isolation]
# runner = "container"
"""

# ZDOTDIR makes this folder's terminals read .vscode/zsh instead of ~/.zshrc. The two paths win
# over the repo's .env, which can name only one workspace: without them the attempts plugin
# skips this folder's tests without a word.
SETTINGS = """\
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "terminal.integrated.env.osx": {
    "ZDOTDIR": "${workspaceFolder}/.vscode/zsh",
    "WORKSPACE_DIR": "${workspaceFolder}",
    "LECTURE_DIR": "${workspaceFolder}/lectures"
  }
}
"""
# The venv's path is worked out from this file's own place, so the folder can move.
ZSHRC = """\
# VS Code terminals of this folder start here (terminal.integrated.env.osx in ../settings.json).
# Load the usual config, then put this folder's venv ahead of conda's base: under another
# Python, pytest runs without the attempts plugin and attempts.jsonl silently misses the run.
HISTFILE="$HOME/.zsh_history"  # keep one history, not one inside this folder
source "$HOME/.zshrc"
source "${${(%):-%x}:A:h:h:h}/.venv/bin/activate"
"""
ZPROFILE = """\
# ZDOTDIR points here, so zsh no longer reads ~/.zprofile by itself (see .zshrc in this folder).
[[ -f "$HOME/.zprofile" ]] && source "$HOME/.zprofile"
"""

# VS Code's C/C++ extension takes the C standard for its completion and red underlines from
# .vscode/c_cpp_properties.json; without one it assumes the compiler's default, not the
# course's -std (docs/design.md, "C", step 4b). Written when the owner ticks C on the
# Environment page. Only a file named as this one's (C_EDITOR_NAME) is rewritten or removed,
# so a file the owner wrote stays theirs.
C_EDITOR_FILE = ".vscode/c_cpp_properties.json"
C_EDITOR_NAME = "tutor: from [languages.c] in workspace.toml"
# gcc's -std values, by the cStandard VS Code names the same standard with.
_C_STANDARDS = {
    "c89": "c89", "c90": "c89", "iso9899:1990": "c89", "iso9899:199409": "c89",
    "c99": "c99", "c9x": "c99", "iso9899:1999": "c99",
    "c11": "c11", "c1x": "c11", "iso9899:2011": "c11",
    "c17": "c17", "c18": "c17", "iso9899:2017": "c17", "iso9899:2018": "c17",
    "c2x": "c23", "c23": "c23",
    "gnu89": "gnu89", "gnu90": "gnu89", "gnu99": "gnu99", "gnu9x": "gnu99",
    "gnu11": "gnu11", "gnu1x": "gnu11", "gnu17": "gnu17", "gnu18": "gnu17",
    "gnu2x": "gnu23", "gnu23": "gnu23",
}


def c_standard(flags: list[str]) -> str:
    """VS Code's cStandard for these compiler flags; the last -std wins, as in gcc.
    "${default}" (the compiler's own) when they name none or one VS Code has no name for."""
    standard = "${default}"
    for flag in flags:
        if flag == "-ansi":
            standard = "c89"
        elif flag.startswith("-std="):
            standard = _C_STANDARDS.get(flag.removeprefix("-std="), "${default}")
    return standard


def c_editor_config(flags: list[str]) -> str:
    """The text of .vscode/c_cpp_properties.json for a C course with these flags. Headers
    the exercises include ("student.h") sit beside student.c, so the folder is searched."""
    return json.dumps({"configurations": [{
        "name": C_EDITOR_NAME,
        "cStandard": c_standard(flags),
        "compilerArgs": list(flags),
        "includePath": ["${workspaceFolder}/**"],
        "intelliSenseMode": "${default}",
    }], "version": 4}, indent=2) + "\n"


def c_editor_config_kept(root: Path) -> bool:
    """Whether the course has a c_cpp_properties.json of its own, which is left alone."""
    path = root / C_EDITOR_FILE
    if not path.is_file():
        return False
    try:
        names = [c.get("name") for c in json.loads(path.read_text(encoding="utf-8"))
                 .get("configurations", [])]
    except (ValueError, AttributeError, OSError):
        return True  # unreadable as ours: someone else's
    return names != [C_EDITOR_NAME]


def write_c_editor_config(root: Path, flags: list[str] | None) -> bool:
    """Write (flags) or remove (None) this course's c_cpp_properties.json, unless the course
    has one of its own; False when it was left alone for that reason."""
    if c_editor_config_kept(root):
        return False
    path = root / C_EDITOR_FILE
    if flags is None:
        path.unlink(missing_ok=True)
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(c_editor_config(flags), encoding="utf-8", newline="\n")
    return True


def venv_python(venv_dir: Path) -> Path:
    """The venv's interpreter: Scripts/python.exe on Windows, bin/python elsewhere."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _make_venv(venv_dir: Path, python: Path | None = None) -> None:
    """The .venv, with this command's Python, or with `python` (another interpreter on this
    machine), which then makes it itself."""
    if python is None:
        # EnvBuilder uses the base interpreter even when run from another venv, so the new
        # venv does not depend on the one this command runs in.
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        return
    done = subprocess.run([str(python), "-m", "venv", str(venv_dir)], capture_output=True,
                          text=True)
    if done.returncode != 0:
        raise WorkspaceError(f"{python} could not make a venv:\n{done.stderr.strip()}")


def init(root: Path, course: str | None = None, extras: str = DEFAULT_EXTRAS,
         make_venv: Callable[..., None] = _make_venv,
         run: Callable = subprocess.run,
         say: Callable[[str], None] = lambda line: None,
         python: Path | None = None, python_version: str | None = None,
         replace_venv: bool = False) -> list[str]:
    """Create what is missing in `root` and return one line per step.

    `make_venv` and `run` are parameters so tests need no network and no real venv. `say`
    gets each step's line as it happens, and a line before the install, which takes minutes
    the first time: the command prints them at once, so its log (the GUI's progress) never
    looks stuck. Raises WorkspaceError, before changing anything, on a bad argument or a
    folder inside a git repository. A failed install raises too: run the command again
    after fixing it.

    `python` (with its `python_version`, "3.12") makes the .venv with that interpreter, and
    [requires] is checked as if it named that Python. `replace_venv`: an existing .venv is
    moved to .venv.old first, deleted once the new one is installed, and moved back if
    anything fails, so a failed switch leaves the course as it was.
    """
    root = root.expanduser().resolve()
    course = course or root.name
    if not _COURSE.match(course):
        raise WorkspaceError(f"course name {course!r}: use letters, digits, '.', '_' or '-'")
    if not _EXTRAS.match(extras):
        raise WorkspaceError(f"extras {extras!r}: a comma-separated list such as gen,dev")
    # The same refusal as load_workspace: private material never sits inside a repository.
    if (repo := _enclosing_git_repo(root)) is not None:
        raise WorkspaceError(f"{root} is inside the git repository {repo}; choose a folder "
                             "outside it, so course material is never committed")
    if not (REPO / "pyproject.toml").is_file():
        raise WorkspaceError(f"no pyproject.toml in {REPO}: run this from a source checkout")
    if replace_venv and python is None:
        raise WorkspaceError("--replace-venv makes the .venv with another Python: give --python")
    # [requires] as it will be: a switch to another Python is checked against that one, as a
    # package may conflict with the Python the course has (what the switch is for).
    packages = pip_requirements(_requires(root, python_version))

    steps: list[str] = []

    def step(line: str) -> None:
        steps.append(line)
        say(line)

    (root / "lectures").mkdir(parents=True, exist_ok=True)
    version = python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    files = {
        WORKSPACE_FILE: WORKSPACE_TOML.format(course=course, version=version),
        ".vscode/settings.json": SETTINGS,
        ".vscode/zsh/.zshrc": ZSHRC,
        ".vscode/zsh/.zprofile": ZPROFILE,
    }
    for name, text in files.items():
        path = root / name
        if path.exists():
            step(f"kept    {name}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        step(f"created {name}")

    venv_dir = root / ".venv"
    old = root / ".venv.old"
    if replace_venv and venv_dir.exists():
        if old.exists():  # left by a switch that was stopped: the .venv now is the newer
            shutil.rmtree(old)
        venv_dir.rename(old)
        step("moved   .venv to .venv.old (put back if this fails)")
    try:
        if venv_python(venv_dir).exists():
            step("kept    .venv")
        else:
            say("creating .venv ...")
            if python is None:
                make_venv(venv_dir)
            else:
                make_venv(venv_dir, python)
            step(f"created .venv (Python {version})")
        # Every time: pip does nothing if all is there, and this finishes an install that
        # failed and adds extras and [requires] packages named later. One pip call, so pip
        # resolves this package's own dependencies and the course's together.
        what = ", ".join([f"tutor[{extras}]", *packages])
        say(f"installing {what} into .venv (a few minutes the first time) ...")
        result = run(install_command(venv_dir, extras, packages), capture_output=True,
                     text=True)
        if result.returncode != 0:
            raise WorkspaceError("pip install failed, run this again once fixed:\n"
                                 f"{result.stderr}")
    except BaseException:
        if replace_venv and old.exists():  # the switch failed (or was stopped): undo it
            shutil.rmtree(venv_dir, ignore_errors=True)
            old.rename(venv_dir)
            say("put the .venv before back")
        raise
    step(f"installed {what} into .venv")
    if replace_venv and old.exists():
        shutil.rmtree(old, ignore_errors=True)
        step("deleted .venv.old")
    return steps


def install_command(venv_dir: Path, extras: str, packages: list[str]) -> list[str]:
    """The pip command init runs; the GUI shows it before its Install button."""
    return [str(venv_python(venv_dir)), "-m", "pip", "install", "-q", "-e", f"{REPO}[{extras}]",
            *packages]


def _requires(root: Path, python_version: str | None = None) -> dict[str, str]:
    """[requires] of the folder's workspace.toml, checked; {} before there is one. Read
    before anything is created, so a broken table changes nothing. `python_version`: checked
    as if its python entry named that one (a switch to it)."""
    path = root / WORKSPACE_FILE
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise WorkspaceError(f"{path}: {e}") from e
    # str(), as load_workspace does: a version written unquoted would load as a number.
    requires = {name: str(version) for name, version in data.get("requires", {}).items()}
    try:
        requires = merge(requires, data.get("package-bounds", {}))
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from None
    checked = {**requires, "python": python_version} if python_version else requires
    if problems := requires_problems(checked, allow_bounds=True):
        raise WorkspaceError(f"{path}: {problems[0]}; fix it, then run this again")
    return requires


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.workspace",
                                     description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("init", help="create what is missing in a course workspace")
    create.add_argument("folder", type=Path)
    create.add_argument("--course", help="course id (default: the folder's name)")
    create.add_argument("--extras", default=DEFAULT_EXTRAS,
                        help=f"this package's extras to install (default: {DEFAULT_EXTRAS})")
    create.add_argument("--python", type=Path,
                        help="make the .venv with this interpreter (default: the one running "
                             "this)")
    create.add_argument("--replace-venv", action="store_true",
                        help="make the .venv anew with --python; the one before is put back "
                             "if this fails")
    args = parser.parse_args(argv)
    python_version = None
    if args.python is not None:
        from tutor import pythons

        found = pythons.ask(args.python)
        if found is None:
            print(f"workspace: {args.python} does not run as a Python", file=sys.stderr)
            return 1
        python_version = found.minor
    try:
        init(args.folder, args.course, args.extras, say=lambda line: print(line, flush=True),
             python=args.python, python_version=python_version,
             replace_venv=args.replace_venv)
    except WorkspaceError as e:
        print(f"workspace: {e}", file=sys.stderr)
        return 1
    root = args.folder.expanduser().resolve()
    if python_version and _requires(root, python_version).get("python") != python_version:
        # The GUI's switch writes it (gui/python_switch.py); from here, the owner does.
        print(f'note: [requires] in workspace.toml still names another Python: set python = '
              f'"{python_version}" there, so the check and the test image use this one too.')
    print(f"{args.folder.expanduser().resolve()} is ready. Put the lectures (PDFs or saved "
          "web pages) in lectures/, open the folder in VS Code, open a new terminal and check "
          "`which pytest` points to its .venv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
