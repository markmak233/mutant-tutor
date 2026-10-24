"""Locate the private course workspace.

The repo holds code only. Course material lives in a workspace outside any git repo,
found through WORKSPACE_DIR (docs/design.md, "Public repo vs private data").
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_FILE = "workspace.toml"


class WorkspaceError(Exception):
    """The workspace is missing or unsafe to use. The message says how to fix it."""


@dataclass(frozen=True)
class Workspace:
    """One course workspace, already validated by `load_workspace`.

    root:        the workspace directory (WORKSPACE_DIR, resolved)
    course:      course id from workspace.toml, e.g. "comp9313"
    requires:    the [requires] table, name -> version prefix, e.g. {"java": "17"}
    lecture_dir: LECTURE_DIR if set, else <root>/lectures
    """

    root: Path
    course: str
    requires: dict[str, str]
    lecture_dir: Path

    # Subfolders are only computed here; code that writes into them creates them on demand.
    @property
    def batches_dir(self) -> Path:
        """One subfolder per generation run, e.g. batches/2026-10-27_p2-golden/."""
        return self.root / "batches"

    @property
    def attempts_dir(self) -> Path:
        """Where attempts.jsonl (one line per test run) lives."""
        return self.root / "attempts"


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from `.env` (default: current directory) into os.environ.

    Variables already set in the environment win, so a shell export overrides `.env`.
    """
    path = Path(".env") if path is None else path
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # setdefault: only fill in variables the shell has not set already.
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_workspace(environ: Mapping[str, str] | None = None) -> Workspace:
    """Find, check and load the workspace named by WORKSPACE_DIR.

    Checks, in order: variable set -> directory exists -> not inside a git repo ->
    workspace.toml present -> it names a course. The first failure raises WorkspaceError.

    `environ` defaults to os.environ; tests pass a plain dict instead.
    """
    env = os.environ if environ is None else environ

    raw_root = env.get("WORKSPACE_DIR")
    if not raw_root:
        raise WorkspaceError("WORKSPACE_DIR is not set. Copy .env.example to .env and set it.")
    root = _private_dir(raw_root, "WORKSPACE_DIR")

    toml_path = root / WORKSPACE_FILE
    if not toml_path.is_file():
        raise WorkspaceError(
            f"{toml_path} not found. WORKSPACE_DIR must point at a course workspace."
        )
    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    if "course" not in data:
        raise WorkspaceError(f"{toml_path} has no `course` entry.")
    # str(): a version written unquoted in TOML (java = 17) would load as an int.
    requires = {name: str(version) for name, version in data.get("requires", {}).items()}

    raw_lectures = env.get("LECTURE_DIR")
    lecture_dir = (
        _private_dir(raw_lectures, "LECTURE_DIR") if raw_lectures else root / "lectures"
    )
    return Workspace(root=root, course=data["course"], requires=requires, lecture_dir=lecture_dir)


def _private_dir(raw: str, var: str) -> Path:
    """Resolve a directory holding private material; refuse one inside a git repo."""
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise WorkspaceError(f"{var} does not exist: {path}")
    repo = _enclosing_git_repo(path)
    if repo is not None:
        raise WorkspaceError(
            f"{var} is inside the git repository {repo}. "
            "Move it out so private material cannot be committed."
        )
    return path


def _enclosing_git_repo(path: Path) -> Path | None:
    """Return the nearest directory at or above `path` that contains .git, if any."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None
