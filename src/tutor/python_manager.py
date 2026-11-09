"""Optional uv-managed CPython discovery and explicit download; never an automatic fix.

Listing uses uv's local catalog, offline, without config or Python downloads. Installation
accepts only a freshly listed stable CPython identifier, no request-supplied paths/URLs.
It adds an interpreter; the existing guarded course switch remains a separate action.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tutor import compat, runtime_env, workspace
from tutor.config import WorkspaceError, load_workspace

INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"
_IDENTIFIER = re.compile(
    r"cpython-(3\.(?:1[1-9]|[2-9]\d)\.\d+)-(?:macos|linux|windows)-[a-z0-9_-]+")
# Trusted diagnostic code, not generated/student code; no validation CLI or .env loader.
_CHECK = ("import json; from dataclasses import asdict; "
          "from tutor.config import load_workspace; from tutor.envcheck import check_workspace; "
          "print(json.dumps([asdict(r) for r in check_workspace(load_workspace())]))")


@dataclass(frozen=True)
class Entry:
    identifier: str
    version: str
    path: Path | None


def environment() -> dict[str, str]:
    """Retain storage locations, not credentials, mirrors, indexes or arbitrary UV_* knobs."""
    env = runtime_env.environment()
    names = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME",
             "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR"}
    env.update({k: v for k, v in os.environ.items() if k.upper() in names})
    env.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never")
    return env


def catalog(*, installed=False, which=None, run=None) -> list[Entry]:
    which, run = which or shutil.which, run or subprocess.run
    uv = which("uv")
    if not uv:
        raise ValueError(f"uv is not installed. Install it explicitly: {INSTALL_URL}")
    command = [uv, "--no-config", "python", "list", "--offline", "--managed-python",
               "--only-installed" if installed else "--only-downloads", "--output-format", "json"]
    done = run(command, capture_output=True, text=True, encoding="utf-8", timeout=20,
               env=environment())
    if done.returncode or len(done.stdout) > 1024 * 1024:
        raise ValueError("uv could not list Pythons; check uv's version and installation")
    try:
        rows = json.loads(done.stdout)
        if not isinstance(rows, list):
            raise ValueError
        found = {}
        for row in rows:
            value = str(row.get("key", "")) if isinstance(row, dict) else ""
            match = _IDENTIFIER.fullmatch(value)
            if not match:
                continue  # exclude PyPy, prereleases, debug/free-threaded and foreign formats
            path = row.get("path")
            if installed and (not isinstance(path, str) or not path):
                continue
            item = Entry(match[0], match[1], Path(path) if isinstance(path, str) and path else None)
            found[item.identifier] = item
        return sorted(found.values(), key=lambda e: tuple(map(int, e.version.split("."))),
                      reverse=True)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("uv returned an unsupported Python catalog; update/check uv") from None


def course_results(course, *, run=None) -> list[dict]:
    """Read removed-import evidence from the course venv, without loading keys or reports."""
    python = workspace.venv_python(course.root / ".venv")
    if not python.is_file():
        return []
    env = runtime_env.environment()
    env.update(WORKSPACE_DIR=str(course.root), LECTURE_DIR=str(course.lecture_dir))
    done = (run or subprocess.run)([str(python), "-c", _CHECK], env=env, cwd=course.root,
                                   capture_output=True, text=True, encoding="utf-8", timeout=60)
    try:
        rows = json.loads(done.stdout) if not done.returncode else None
        if not isinstance(rows, list) or len(done.stdout) > 1024 * 1024:
            raise ValueError
        return [r for r in rows if isinstance(r, dict)]
    except ValueError:
        raise ValueError("course Python check failed; fix/recreate its venv "
                         "before downloading") from None


def problems(requires: dict, entry: Entry, results: list[dict]) -> list[str]:
    """Known runtime conflicts and measured removed imports, never a model's recollection."""
    found = compat.conflicts(requires, entry.version)
    version = tuple(map(int, entry.version.split(".")[:2]))
    for row in results:
        removed = compat.REMOVED_MODULES.get(row.get("required"))
        if row.get("name", "").endswith(" imports") and removed:
            if version >= tuple(map(int, removed.split("."))):
                found.append(f"{row['name']} {row['required']}, removed in Python {removed}")
    return found


def install(course, identifier: str, *, which=None, run=None) -> Entry:
    """Explicit action; revalidate catalog/course immediately before the fixed uv command."""
    which, run = which or shutil.which, run or subprocess.run
    selected = next((e for e in catalog(which=which, run=run) if e.identifier == identifier), None)
    if selected is None:
        raise ValueError("that Python is no longer offered; list the catalog again")
    if reasons := problems(course.effective_requires, selected, course_results(course, run=run)):
        raise ValueError("; ".join(reasons))
    env = environment()
    env.pop("UV_PYTHON_DOWNLOADS")  # only this explicit action permits uv's standard download
    command = [which("uv"), "--no-config", "python", "install", "--no-bin", identifier]
    done = run(command, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    if done.returncode:
        raise ValueError("uv Python download failed; check connectivity and disk space, then retry")
    installed = next((e for e in catalog(installed=True, which=which, run=run)
                      if e.identifier == identifier), None)
    if not installed or installed.path is None:
        raise ValueError("uv finished but the interpreter could not be rediscovered")
    return installed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tutor python", description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=("list", "install"))
    parser.add_argument("course", type=Path)
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--confirm", action="store_true",
                        help="allow a Python download into uv's user directory; no PATH aliases")
    args = parser.parse_args(argv)
    try:
        course = load_workspace({"WORKSPACE_DIR": str(args.course.resolve())})
        if args.action == "install":
            if not args.confirm or not args.identifier:
                raise ValueError("review `tutor python list COURSE`, then "
                                 "install COURSE ID --confirm")
            found = install(course, args.identifier)
            print(f"Installed Python {found.version} at {found.path}. "
                  "Check the Environment page to choose it for the course; the venv is unchanged.")
        else:
            results = course_results(course)
            for entry in catalog():
                reasons = problems(course.effective_requires, entry, results)
                print(entry.identifier + (": " + "; ".join(reasons) if reasons else ": offered"))
    except (OSError, ValueError, WorkspaceError, subprocess.TimeoutExpired) as error:
        print(f"python: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
