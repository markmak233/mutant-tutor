"""Report what a course workspace's records say, with no model calls.

    python -m tutor.eval report [FOLDER ...]

Each workspace (default: the one WORKSPACE_DIR names) gets reports/report.md. See
tutor.eval.report for what it reads and what it leaves out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tutor.config import Workspace, WorkspaceError, load_env, load_workspace
from tutor.eval.report import write_report


def _workspaces(folders: list[Path]) -> list[Workspace]:
    """The named folders with the checks any workspace gets, or the one in WORKSPACE_DIR."""
    if folders:
        return [load_workspace({"WORKSPACE_DIR": str(folder)}) for folder in folders]
    load_env()  # only when no folder is named: .env is what names the default workspace
    return [load_workspace()]


def main(argv: list[str] | None = None) -> int:
    """Exit code 0: every report written; 2: a folder is not a course workspace."""
    parser = argparse.ArgumentParser(prog="python -m tutor.eval",
                                     description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="write each workspace's reports/report.md")
    report.add_argument("folders", nargs="*", type=Path,
                        help="course workspaces (default: WORKSPACE_DIR)")
    args = parser.parse_args(argv)
    try:
        workspaces = _workspaces(args.folders)
    except WorkspaceError as e:
        print(f"eval: {e}", file=sys.stderr)
        return 2
    for workspace in workspaces:
        print(write_report(workspace.root, workspace.course))
    return 0


if __name__ == "__main__":
    sys.exit(main())
