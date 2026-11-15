"""python -m tutor.gui [--root DIR] [--port N] [--no-browser]

Starts the GUI on 127.0.0.1 and opens it in the browser. `--root` is the folder that holds
the course folders; by default the one above WORKSPACE_DIR (from the environment or `.env`),
which is how `python -m tutor.workspace init` lays courses out. Installed wheels with no
WORKSPACE_DIR use per-user courses/config folders. The port is a free one unless given.
A repeat launch verifies and reuses the existing local server. Ctrl+C stops the server.
When a course there runs its tests in a container, every launch starts an installed but
stopped Docker Desktop, or says to install Docker when there is none.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
import threading
import tomllib
import webbrowser
from pathlib import Path

from tutor import locations, project_source
from tutor.config import WORKSPACE_FILE, WorkspaceError, _enclosing_git_repo, _private_dir
from tutor.gui import launcher_state


def _workspace_dir() -> str | None:
    """WORKSPACE_DIR from the environment, else from `.env`, read for this one name. Loading
    all of `.env` would put its API keys into this process's environment, and from there into
    every program the GUI starts: VS Code, its terminals and the code run in them."""
    if value := os.environ.get("WORKSPACE_DIR"):
        return value
    path = Path(".env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "WORKSPACE_DIR":
            return value.strip().strip("'\"") or None
    return None


def _root(given: str | None) -> Path:
    """The folder holding the course folders: `--root`, else the one above WORKSPACE_DIR,
    else (only for an installed tool, not this checkout) the per-user courses folder."""
    if given:
        raw = given
    else:
        workspace = _workspace_dir()
        if not workspace:
            if project_source.checkout() is None:
                root = (locations.home() / "courses").resolve()
                if _enclosing_git_repo(root):
                    raise WorkspaceError("default courses folder cannot be inside a git repository")
                return root  # explicit main launch creates it under its startup lock
            raise WorkspaceError("give --root, the folder holding your course folders, or set "
                                 "WORKSPACE_DIR to one course")
        raw = str(Path(workspace).expanduser().resolve().parent)
    # The same refusal as a workspace: private material never sits inside a repository.
    return _private_dir(raw, "--root" if given else "the folder above WORKSPACE_DIR")


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

    if shutil.which("docker") is None:
        print("Docker is not installed, and a course here runs its tests in a container: "
              "install Docker Desktop (docker.com) yourself, then launch again.", flush=True)
        return
    if container.docker_problem() is None:  # waits while a sleeping VM wakes
        return
    if container.start_docker_desktop():
        print("Docker Desktop was not running: starting it. It takes a minute; then press "
              "\"Check again\" on a course's Environment page.", flush=True)
    else:
        print("Docker is not running, and no Docker Desktop was found to start: start Docker "
              "yourself.", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Start (or reuse) the GUI's server for one root; 2 when it cannot start."""
    # The module docstring's second paragraph; argparse wraps it again.
    parser = argparse.ArgumentParser(prog="python -m tutor.gui",
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
        webbrowser.open(url)
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
