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
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
import venv
from collections.abc import Callable
from pathlib import Path

from tutor.config import WORKSPACE_FILE, WorkspaceError, _enclosing_git_repo
from tutor.envcheck import pip_requirements, requires_problems

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


def venv_python(venv_dir: Path) -> Path:
    """The venv's interpreter: Scripts/python.exe on Windows, bin/python elsewhere."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _make_venv(venv_dir: Path) -> None:
    # EnvBuilder uses the base interpreter even when run from another venv, so the new venv
    # does not depend on the one this command runs in.
    venv.EnvBuilder(with_pip=True).create(venv_dir)


def init(root: Path, course: str | None = None, extras: str = DEFAULT_EXTRAS,
         make_venv: Callable[[Path], None] = _make_venv,
         run: Callable = subprocess.run,
         say: Callable[[str], None] = lambda line: None) -> list[str]:
    """Create what is missing in `root` and return one line per step.

    `make_venv` and `run` are parameters so tests need no network and no real venv. `say`
    gets each step's line as it happens, and a line before the install, which takes minutes
    the first time: the command prints them at once, so its log (the GUI's progress) never
    looks stuck. Raises WorkspaceError, before changing anything, on a bad argument or a
    folder inside a git repository. A failed install raises too: run the command again
    after fixing it.
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
    packages = pip_requirements(_requires(root))  # [] for a new course: python only

    steps: list[str] = []

    def step(line: str) -> None:
        steps.append(line)
        say(line)

    (root / "lectures").mkdir(parents=True, exist_ok=True)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
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
    if venv_python(venv_dir).exists():
        step("kept    .venv")
    else:
        say("creating .venv ...")
        make_venv(venv_dir)
        step(f"created .venv (Python {version})")
    # Every time: pip does nothing if all is there, and this finishes an install that failed
    # and adds extras and [requires] packages named later. One pip call, so pip resolves this
    # package's own dependencies and the course's together.
    what = ", ".join([f"tutor[{extras}]", *packages])
    say(f"installing {what} into .venv (a few minutes the first time) ...")
    result = run(install_command(venv_dir, extras, packages), capture_output=True, text=True)
    if result.returncode != 0:
        raise WorkspaceError(f"pip install failed, run this again once fixed:\n{result.stderr}")
    step(f"installed {what} into .venv")
    return steps


def install_command(venv_dir: Path, extras: str, packages: list[str]) -> list[str]:
    """The pip command init runs; the GUI shows it before its Install button."""
    return [str(venv_python(venv_dir)), "-m", "pip", "install", "-q", "-e", f"{REPO}[{extras}]",
            *packages]


def _requires(root: Path) -> dict[str, str]:
    """[requires] of the folder's workspace.toml, checked; {} before there is one. Read
    before anything is created, so a broken table changes nothing."""
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
    if problems := requires_problems(requires):
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
    args = parser.parse_args(argv)
    try:
        init(args.folder, args.course, args.extras, say=lambda line: print(line, flush=True))
    except WorkspaceError as e:
        print(f"workspace: {e}", file=sys.stderr)
        return 1
    print(f"{args.folder.expanduser().resolve()} is ready. Put the lecture PDFs in lectures/, "
          "open the folder in VS Code, open a new terminal and check `which pytest` points to "
          "its .venv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
