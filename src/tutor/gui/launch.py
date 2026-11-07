"""Opening things on this machine for the GUI: a folder in the file manager, a course in VS Code.

Both sit here so that one place decides how, and can say "not available" where it cannot, such
as a GUI run in a container someday (docs/design.md, "Local GUI"). The paths come from the GUI's
own checked folder names, never from a request, and each program is started from an argument
list, without a shell. Nothing waits for it: a file manager or an editor window stays open.

Tested on macOS. The Windows and Linux branches are written from their documented commands and
are not tested yet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Where VS Code keeps its `code` command when "Install 'code' command in PATH" was never run.
MACOS_VSCODE = Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")


class LaunchError(Exception):
    """The thing could not be opened. The message is one of the GUI's fixed notice codes."""


def open_folder(path: Path, start: Callable = subprocess.Popen) -> None:
    """Show `path` in Finder, Explorer or the Linux desktop's file manager."""
    if sys.platform == "win32":
        try:
            os.startfile(path)  # noqa: S606 - Explorer opens a folder; no shell, no arguments
        except OSError as e:
            raise LaunchError("failed") from e
        return
    if sys.platform == "darwin":
        command = ["open", str(path)]
    elif opener := shutil.which("xdg-open"):
        command = [opener, str(path)]
    else:
        raise LaunchError("no-file-manager")
    _start(command, start)


def find_vscode() -> str | None:
    """VS Code's `code` command: on PATH, else (macOS) inside the app."""
    if found := shutil.which("code"):
        return found
    if sys.platform == "darwin" and MACOS_VSCODE.is_file():
        return str(MACOS_VSCODE)
    return None


def open_in_vscode(folder: Path, file: Path | None, start: Callable = subprocess.Popen) -> None:
    """Open `folder` in VS Code and `file` in it. A window that already has the folder open is
    reused, so the course's own terminal settings (its venv) apply."""
    code = find_vscode()
    if code is None:
        raise LaunchError("no-vscode")
    _start([code, str(folder), *([str(file)] if file else [])], start)


def _start(command: list[str], start: Callable) -> None:
    # A new session on POSIX: stopping the GUI with Ctrl+C must not close what it opened.
    extra = {} if sys.platform == "win32" else {"start_new_session": True}
    try:
        start(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, **extra)
    except OSError as e:
        raise LaunchError("failed") from e
