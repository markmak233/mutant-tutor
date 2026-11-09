"""python -m tutor.gui [--root DIR] [--port N] [--no-browser]

Starts the GUI on 127.0.0.1 and opens it in the browser. `--root` is the folder that holds
the course folders; by default the one above WORKSPACE_DIR (from the environment or `.env`),
which is how `python -m tutor.workspace init` lays courses out. The port is a free one unless
given. Ctrl+C stops the server.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import webbrowser
from pathlib import Path

from tutor import locations, project_source
from tutor.config import WorkspaceError, _enclosing_git_repo, _private_dir
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.gui", description=__doc__.split("\n")[2])
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
                app.config["PORT"] = server.effective_port
                state = launcher_state.save(root, server.effective_port, token)
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
