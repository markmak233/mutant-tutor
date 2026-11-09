"""Create one local desktop launcher, without overwriting files or changing startup items.

Each OS uses its own command format. No shell runs user arguments, and tests substitute
the Windows COM writer. Native trust/click acceptance is separate from these file checks.
"""

from __future__ import annotations

import argparse
import base64
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def _desktop_arg(value: str) -> str:
    # Desktop Entry Exec has its own escaping, processed twice (string then argument).
    value = value.replace("%", "%%").replace("\\", "\\\\\\\\")
    for char in ('"', "`", "$"):
        value = value.replace(char, "\\\\" + char)
    return '"' + value + '"'


def _windows(path: Path, command: list[str], run) -> None:
    """COM opens existing links, so create in a private folder, then publish exclusively."""
    def quoted(value):
        return "'" + value.replace("'", "''") + "'"

    # No empty placeholder is passed to CreateShortcut as an existing .lnk to open.
    with tempfile.TemporaryDirectory(prefix="tutor-shortcut-") as raw:
        temporary = Path(raw) / "Tutor.lnk"
        script = ("$ErrorActionPreference='Stop'; "
                  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                  + quoted(str(temporary)) + "); $s.TargetPath=" + quoted(command[0])
                  + "; $s.Arguments=" + quoted(subprocess.list2cmdline(command[1:]))
                  + "; $s.Save()")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        powershell = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        done = run(powershell, capture_output=True, timeout=30)
        if done.returncode or not temporary.is_file() or not temporary.stat().st_size:
            raise ValueError("Windows could not create the shortcut")
        # Exclusive publication protects an existing shortcut, including a concurrent writer.
        created = False
        try:
            with path.open("xb") as output:
                created = True
                output.write(temporary.read_bytes())
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise


def create(directory: Path, root: Path | None = None, *, run=None) -> Path:
    """Create only the requested file. --directory allows entirely disposable acceptance."""
    run = subprocess.run if run is None else run
    command = [sys.executable, "-m", "tutor", "gui"]
    if root is not None:
        command += ["--root", str(root.resolve())]
    suffix = ".lnk" if sys.platform == "win32" else (
        ".command" if sys.platform == "darwin" else ".desktop")
    path = directory / ("Tutor" + suffix)
    if not directory.is_dir():
        raise ValueError("shortcut directory does not exist; give --directory")
    if any("\n" in arg or "\r" in arg or "\x00" in arg for arg in command):
        raise ValueError("shortcut paths cannot contain newlines or NUL")
    if sys.platform == "win32":
        if path.exists() or path.is_symlink():
            raise FileExistsError("shortcut already exists")
        _windows(path, command, run)
        return path
    # Exclusive creation also refuses existing links; never overwrite an owner's launcher.
    with open(path, "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o700)) as handle:
        if sys.platform == "darwin":
            handle.write("#!/bin/sh\nexec " + shlex.join(command) + "\n")
        else:
            handle.write("[Desktop Entry]\nType=Application\nName=Tutor\nTerminal=true\nExec="
                         + " ".join(_desktop_arg(arg) for arg in command) + "\n")
    path.chmod(0o700)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tutor shortcut", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="folder containing the courses")
    parser.add_argument("--directory", type=Path, default=Path.home() / "Desktop")
    args = parser.parse_args(argv)
    try:
        path = create(args.directory, args.root)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"shortcut: {error}", file=sys.stderr)
        return 2
    print(f"Created {path}. Open it to launch tutor; desktop trust prompts are OS-specific.")
    return 0
