"""Create one local desktop launcher, without overwriting files or changing startup items.

Each OS uses its own command format. No shell runs user arguments, and tests substitute
the Windows COM writer. Native trust/click acceptance is separate from these file checks.
Without --directory the launcher goes on the desktop the system shows (default_directory),
not a guessed ~/Desktop.

The GUI's server runs in the console window the launcher opens, and closing that window
is how it stops (the pages have no Quit). On Windows the link starts that window
minimised (owner, 2026-11-12, after a click on the real desktop: "a terminal stays there
the whole time"): it waits on the taskbar, titled by the link's name, and its tooltip
says how to stop it. macOS (.command) and Linux (.desktop, Terminal=true) still open a
normal terminal window.
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
    """One argument of a Linux .desktop file's Exec line, quoted for it."""
    # Desktop Entry Exec has its own escaping, processed twice (string then argument):
    # `%` is a field code there, and a backslash is unescaped once per pass.
    value = value.replace("%", "%%").replace("\\", "\\\\\\\\")
    for char in ('"', "`", "$"):
        value = value.replace(char, "\\\\" + char)
    return '"' + value + '"'


# The .lnk's window style: 7 opens the server's console minimised (1 is a normal window,
# 3 maximised), so it waits on the taskbar instead of covering the browser.
WINDOW_MINIMISED = 7
# The .lnk's comment, shown as its tooltip: the one place the minimised window explains itself.
LINK_TOOLTIP = "Opens the tutor pages. The server runs in a minimised window; close it to stop."


def _windows(path: Path, command: list[str], run) -> None:
    """COM opens existing links, so create in a private folder, then publish exclusively."""
    def quoted(value):
        # A PowerShell single-quoted string: nothing inside is expanded, '' is one quote.
        return "'" + value.replace("'", "''") + "'"

    # No empty placeholder is passed to CreateShortcut as an existing .lnk to open.
    with tempfile.TemporaryDirectory(prefix="tutor-shortcut-") as raw:
        temporary = Path(raw) / "Tutor.lnk"
        script = ("$ErrorActionPreference='Stop'; "
                  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                  + quoted(str(temporary)) + "); $s.TargetPath=" + quoted(command[0])
                  + "; $s.Arguments=" + quoted(subprocess.list2cmdline(command[1:]))
                  + f"; $s.WindowStyle={WINDOW_MINIMISED}"
                  + "; $s.Description=" + quoted(LINK_TOOLTIP)
                  + "; $s.Save()")
        # -EncodedCommand takes UTF-16LE base64: no quoting of the script by any shell.
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        powershell = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        # stdin closed: powershell.exe reads a redirected stdin to its end before it runs
        # the command, and under a runner (CI's step, a GUI task) that pipe never ends, so
        # it sat until the timeout (Windows CI, 2026-11-11: 30 s, then exit 2). A cold
        # Windows PowerShell with a fresh profile is also slow to start: a minute's room.
        done = run(powershell, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        if done.returncode or not temporary.is_file() or not temporary.stat().st_size:
            raise ValueError("Windows could not create the shortcut: powershell.exe exit "
                             f"{done.returncode}, {done.stderr.decode(errors='replace')[:300]}")
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


# Windows' id for the Desktop known folder (KNOWNFOLDERID FOLDERID_Desktop, from KnownFolders.h).
FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"


def _windows_desktop() -> Path:
    """The Desktop folder Windows shows, wherever it is: OneDrive's when its backup moved it
    there, or another folder set in its Properties. ~/Desktop is then a stale or missing
    folder, and a shortcut put in it is not seen (the walk-throughs of 2026-11-15/16, whose
    INSTALL text had a workaround). The same answer as [Environment]::GetFolderPath('Desktop'),
    without starting PowerShell or decoding its output, which a non-ASCII user name
    would need to survive."""
    import ctypes  # WinDLL exists only on Windows, so only here
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    folder_id = GUID.from_buffer_copy(uuid.UUID(FOLDERID_DESKTOP).bytes_le)
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                             wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long  # an HRESULT: 0 is success
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    found = ctypes.c_void_p()
    result = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(found))
    try:
        if result != 0 or not found.value:
            raise ValueError("Windows did not say where the desktop is "
                             f"(0x{result & 0xFFFFFFFF:08X}); give --directory FOLDER")
        return Path(ctypes.wstring_at(found.value))
    finally:
        ole32.CoTaskMemFree(found)  # Windows allocated the text; freeing None is allowed


def _linux_desktop(run) -> Path:
    """The desktop folder of this user's language ("Schreibtisch", "桌面"), from
    xdg-user-dir, which reads ~/.config/user-dirs.dirs; ~/Desktop without it. xdg-user-dir
    answers the home folder itself when no desktop is set: a launcher there is on no desktop,
    so that counts as none."""
    home = Path.home()
    try:
        done = run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=10)
        answer = done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        answer = ""  # not installed (a minimal system): the usual name
    desktop = Path(answer) if answer else home / "Desktop"
    if desktop == home:
        raise ValueError("this system has no desktop folder; give --directory FOLDER")
    return desktop


def default_directory(*, run=None) -> Path:
    """Where `tutor shortcut` puts the launcher when no --directory is given: the desktop
    this system shows. Linux with none is an error naming --directory (owner, 2026-11-16:
    not a silent fallback to the applications menu, where the user would not look)."""
    run = subprocess.run if run is None else run
    if sys.platform == "win32":
        return _windows_desktop()
    if sys.platform == "darwin":
        return Path.home() / "Desktop"  # Finder's desktop is always this folder
    desktop = _linux_desktop(run)
    if not desktop.is_dir():
        raise ValueError(f"the desktop folder {desktop} does not exist; give --directory FOLDER")
    return desktop


def create(directory: Path, root: Path | None = None, *, run=None) -> Path:
    """Create only the requested file. --directory allows entirely disposable acceptance."""
    run = subprocess.run if run is None else run
    # This interpreter (the tool's own venv) runs the GUI; a fixed root goes with it.
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
    """`tutor shortcut`: exit 0 with the path made, 2 with the reason it was not."""
    parser = argparse.ArgumentParser(prog="tutor shortcut", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="folder containing the courses")
    parser.add_argument("--directory", type=Path,
                        help="where to put it (default: this system's desktop folder)")
    args = parser.parse_args(argv)
    try:
        # Looked up only when needed: a given --directory never asks the system.
        path = create(args.directory or default_directory(), args.root)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"shortcut: {error}", file=sys.stderr)
        return 2
    print(f"Created {path}. Open it to launch tutor; desktop trust prompts are OS-specific.")
    return 0
