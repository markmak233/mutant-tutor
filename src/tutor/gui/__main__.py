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

from tutor.config import WorkspaceError, _private_dir, load_env


def _root(given: str | None) -> Path:
    if given:
        raw = given
    else:
        load_env()
        workspace = os.environ.get("WORKSPACE_DIR")
        if not workspace:
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
        print(f"gui: {e.name} is missing: pip install -e \".[gui]\"", file=sys.stderr)
        return 2

    token = secrets.token_urlsafe(32)
    app = create_app(root, token)
    # Port 0 lets the system pick a free one; the Host check needs the real one.
    server = create_server(app, host="127.0.0.1", port=args.port)
    app.config["PORT"] = server.effective_port
    url = f"http://127.0.0.1:{server.effective_port}/?token={token}"
    print(f"Courses in {root}\nOpen {url}\nCtrl+C stops the server.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
