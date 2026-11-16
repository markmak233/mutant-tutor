"""python -m tutor.gui [--root DIR] [--port N] [--no-browser]

Starts the GUI on 127.0.0.1 and opens it in the browser. `--root` is the folder that holds
the course folders; by default the one above a course, which is how `python -m
tutor.workspace init` lays courses out: the course WORKSPACE_DIR names, else the course this
command runs in, else the one WORKSPACE_DIR names in `.env` (here, or in this checkout).
Installed wheels with none of these use per-user courses/config folders. The port is a free
one unless given.
A repeat launch verifies and reuses the existing local server. Ctrl+C stops the server.
When a course there runs its tests in a container, every launch starts an installed but
stopped Docker Desktop, or says to install Docker when there is none.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import threading
import tomllib
import webbrowser
from pathlib import Path

from tutor import locations, project_source
from tutor.config import WORKSPACE_FILE, WorkspaceError, _enclosing_git_repo, _private_dir
from tutor.gui import launcher_state

# How the refusal of a root inside a repository names where the root came from.
ABOVE_WORKSPACE_DIR = "the folder above WORKSPACE_DIR"
ABOVE_THIS_COURSE = "the folder above this course"


def _env_value(path: Path) -> str | None:
    """WORKSPACE_DIR from one `.env` file, read for this one name. Loading all of `.env`
    would put its API keys into this process's environment, and from there into every
    program the GUI starts: VS Code, its terminals and the code run in them."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "WORKSPACE_DIR":
            return value.strip().strip("'\"") or None
    return None


def _enclosing_course(start: Path) -> Path | None:
    """The nearest folder at or above `start` with a workspace.toml: the course a terminal
    is in. A course's VS Code terminal sets WORKSPACE_DIR only on macOS (workspace.SETTINGS),
    and a course `.venv`'s tutor is this checkout's, so without this it could not start
    there with no --root (found 2026-11-16)."""
    for folder in (start, *start.parents):
        if (folder / WORKSPACE_FILE).is_file():
            return folder
    return None


def _workspace_dir() -> tuple[str, str] | None:
    """One course whose parent holds the courses, and how it was found (for the refusal of
    a root inside a repository), first match wins:
    1. WORKSPACE_DIR in the environment: set on purpose, as a course's terminal does;
    2. the course this command runs in, on any system;
    3. WORKSPACE_DIR in `.env` here: the checkout's own folder, as before;
    4. WORKSPACE_DIR in this checkout's `.env`, from any folder (the attempts plugin reads
       the same file). An installed tool has no checkout, so only 1 to 3 apply to it."""
    if value := os.environ.get("WORKSPACE_DIR"):
        return value, ABOVE_WORKSPACE_DIR
    try:
        here = Path.cwd()
    except OSError:  # the folder this terminal is in was deleted
        here = None
    if here is not None and (course := _enclosing_course(here)):
        return str(course), ABOVE_THIS_COURSE
    checkout = project_source.checkout()
    for env_file in (Path(".env"), *([checkout / ".env"] if checkout else [])):
        if value := _env_value(env_file):
            return value, ABOVE_WORKSPACE_DIR
    return None


def _root(given: str | None) -> Path:
    """The folder holding the course folders: `--root`, else the one above the course
    `_workspace_dir` finds, else (only for an installed tool, not this checkout) the per-user
    courses folder."""
    if given:
        raw, label = given, "--root"
    else:
        found = _workspace_dir()
        if not found:
            if project_source.checkout() is None:
                root = (locations.home() / "courses").resolve()
                if _enclosing_git_repo(root):
                    raise WorkspaceError("default courses folder cannot be inside a git repository")
                return root  # explicit main launch creates it under its startup lock
            raise WorkspaceError("give --root, the folder holding your course folders, run "
                                 "this inside a course folder, or set WORKSPACE_DIR to one "
                                 "course")
        workspace, label = found
        raw = str(Path(workspace).expanduser().resolve().parent)
    # The same refusal as a workspace: private material never sits inside a repository.
    return _private_dir(raw, label)


def _needs_docker(root: Path) -> bool:
    """Whether a course under `root` runs its tests in a container ([isolation] runner)."""
    from tutor.gui import courses  # after main's check that the [gui] extra is installed

    for course in courses.find_courses(root)[0]:
        try:
            with (course.workspace.root / WORKSPACE_FILE).open("rb") as f:
                if tomllib.load(f).get("isolation", {}).get("runner") == "container":
                    return True
        except (OSError, tomllib.TOMLDecodeError):
            continue  # that course's own pages report the broken file
    return False


def _wake_docker(root: Path) -> None:
    """Start Docker Desktop when a course here needs it and it is installed but stopped
    (owner, 2026-11-15), so the first test run does not stop at "Docker not running". It
    never installs Docker, and it does not wait for Docker to come up: the pages' "Check
    again" shows when it is ready. Courses that all run on this machine are left alone,
    since Docker's VM takes memory a local model may need."""
    if not _needs_docker(root):
        return
    from tutor import container

    state = container.docker_state()  # waits while a sleeping VM wakes
    if state.kind is None:
        return
    if state.kind == container.MISSING:
        print("Docker is not installed, and a course here runs its tests in a container: "
              "install Docker Desktop (docker.com) yourself, then launch again.", flush=True)
        return
    if state.kind not in (container.STOPPED, container.SLOW):
        # Installed but not on this terminal's PATH, or not this user's to use: starting it
        # would not help, so only say what would (container.docker_state).
        print(f"{state.message}.", flush=True)
        return
    if container.start_docker_desktop():
        print("Docker Desktop was not running: starting it. It takes a minute; then press "
              "\"Check again\" on a course's Environment page.", flush=True)
    else:
        print("Docker is not running, and no Docker Desktop was found to start: start Docker "
              "yourself.", flush=True)


def _in_wsl() -> bool:
    """Whether this is Linux under WSL (Windows Subsystem for Linux): WSL sets
    WSL_DISTRO_NAME in every shell, and its kernel's release names Microsoft."""
    if not sys.platform.startswith("linux"):
        return False
    return bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in os.uname().release.lower()


NOT_OPENED = "The browser did not open by itself: open the link above in one."


def _open_browser(url: str) -> None:
    """Open the pages in the browser, or say to open the link by hand.

    WSL (2026-11-16, a joint test of the owner's PC and its WSL): Python's webbrowser found
    no browser there and fell through to `gio`, which printed "Operation not supported"
    and nothing else. Windows' own browser does reach a server in WSL that listens on
    127.0.0.1 (WSL forwards localhost by default; checked with curl and the owner's
    browser), so under WSL the link goes to Windows: `explorer.exe URL` opens it in the
    default browser. explorer.exe exits 1 even when it worked, so only a failure to
    start it counts. Without Windows interop (turned off in /etc/wsl.conf) there is no
    explorer.exe, and the usual way is tried."""
    if _in_wsl() and (explorer := shutil.which("explorer.exe")):
        try:
            subprocess.Popen([explorer, url], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    # webbrowser.open says False when it found no browser at all. A browser that fails
    # after starting (gio's case) still reads as True: the link printed above remains.
    if not webbrowser.open(url):
        print(NOT_OPENED, flush=True)


def main(argv: list[str] | None = None) -> int:
    """Start (or reuse) the GUI's server for one root; 2 when it cannot start."""
    # The module docstring's second paragraph; argparse wraps it again. The name is the one
    # an installed tool is run by (`python -m tutor.gui` still works in a checkout).
    parser = argparse.ArgumentParser(prog="tutor gui",
                                     description=__doc__.split("\n\n")[1])
    parser.add_argument("--root", help="the folder that holds the course folders")
    parser.add_argument("--port", type=int, default=0, help="default: a free port")
    parser.add_argument("--no-browser", action="store_true", help="only print the link")
    parser.add_argument("--reset-state", action="store_true",
                        help="discard stale launch metadata after checking the old server")
    args = parser.parse_args(argv)
    try:
        root = _root(args.root)
    except WorkspaceError as e:
        print(f"gui: {e}", file=sys.stderr)
        return 2
    try:  # the extra may not be installed; say how to get it instead of a traceback
        from waitress import create_server

        from tutor.gui.app import create_app
    except ImportError as e:
        print(f"gui: {e.name} is missing: reinstall this repository with its [gui] extra",
              file=sys.stderr)
        return 2

    # In a thread: `docker info` can wait 30 s for a sleeping VM, and the page should open
    # meanwhile. A daemon, so a server stopped with Ctrl+C does not wait for it.
    waker = threading.Thread(target=_wake_docker, args=(root,), daemon=True)
    waker.start()
    server = None
    try:
        with launcher_state.locked(root, reset=args.reset_state):
            state = launcher_state.existing(root)
            if state is None:
                locations.prepare()  # only this explicit action creates packaged config
                token = secrets.token_urlsafe(32)
                app = create_app(root, token)
                # Port 0 lets the system pick a free one; the Host check needs the real one.
                server = create_server(app, host="127.0.0.1", port=args.port)
                # Waitress reports its effective port as text; persist a validated integer.
                port = int(server.effective_port)
                app.config["PORT"] = port
                state = launcher_state.save(root, port, token)
    except (OSError, ValueError) as e:
        if server is not None:
            server.close()
        print(f"gui: {e}", file=sys.stderr)
        return 2
    url = launcher_state.url(state)
    action = "Ctrl+C stops the server." if server is not None else "Reused the verified server."
    print(f"Courses in {root}\nOpen {url}\n{action}", flush=True)
    if not args.no_browser:
        _open_browser(url)
    if server is None:
        waker.join()  # a reused server: this process ends here, and the start must go out first
        return 0
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        launcher_state.clear(root, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
