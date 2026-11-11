"""python -m tutor.gui.runner <task folder>: runs one GUI task's commands (see tasks.py).

The commands run one after another, each to the end even if an earlier one failed (a batch's
validation goes on past an INVALID exercise), with their output appended to output.log. The
runner then writes each exit code and the task's end to task.json: done if every command
exited 0, failed otherwise.

Stopping (tasks.stop sends this process Ctrl+C). The command's own processes are not all in
this process group: the test runner starts each pytest or `docker run` in a session of its
own. So the runner first lists every process below it, passes Ctrl+C to each, as a terminal's
Ctrl+C would reach the command, and lets them finish what they do on Ctrl+C (a `docker run`
client passes it into its container). What is still alive after the grace time is killed;
a container left without its client ends at its own time limit, which is at most the
exercise's limit plus 5 s.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from tutor.gui.tasks import DONE, FAILED, LOG_FILE, REPO, STOPPED, TASK_FILE, _now, _write


def descendants(pid: int) -> list[int]:
    """Every process below `pid`, parents before children. POSIX `ps`; empty elsewhere."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, queue = [], list(children.get(pid, []))
    while queue:
        child = queue.pop(0)
        found.append(child)
        queue.extend(children.get(child, []))
    return found


def _signal_all(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def stop_tree(child: subprocess.Popen, grace_s: float) -> None:
    """Ctrl+C to the command and everything below it, then a kill after `grace_s`."""
    if sys.platform == "win32":  # not tested: no `ps`, and no process groups to reach
        child.terminate()
        child.wait()
        return
    tree = descendants(os.getpid())  # taken first: an orphan leaves the tree once its parent dies
    _signal_all(tree, signal.SIGINT)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and child.poll() is None:
        time.sleep(0.1)
    # Kill, deepest first, what is left: the list from before, and anything started since.
    left = tree + [pid for pid in descendants(os.getpid()) if pid not in tree]
    _signal_all(list(reversed(left)), signal.SIGKILL)
    child.wait()


def run(folder: Path) -> int:
    # Ctrl+C must reach this process however it was started: a process started in the
    # background (`&`, and maybe a desktop shortcut) has SIGINT ignored, its children inherit
    # that, and Python then installs no handler, so Stop did nothing (seen 2026-11-06).
    # Setting a handler also gives the commands the default back, as exec resets a caught one.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    task = json.loads((folder / TASK_FILE).read_text(encoding="utf-8"))
    task["pid"] = os.getpid()
    _write(folder, task)
    env = {**os.environ, **task.get("env", {}), "PYTHONUNBUFFERED": "1",
           "PYTHONIOENCODING": "utf-8"}
    root = task.get("env", {}).get("WORKSPACE_DIR")
    codes: list[int | None] = []
    stopped = False
    child: subprocess.Popen | None = None
    with (folder / LOG_FILE).open("ab", buffering=0) as log:
        try:
            for command in task["commands"]:
                log.write(f"$ {shlex.join(_shown(command, root))}\n".encode())
                child = subprocess.Popen(command, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT)
                code = child.wait()
                codes.append(code)
                log.write(f"[exit {code}]\n\n".encode())
                task["exit_codes"] = codes
                _write(folder, task)
        except KeyboardInterrupt:  # tasks.stop, wherever it lands
            # A second Stop must not end the runner before it has written the task's end.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            stopped = True
            log.write(b"\n[stopping: Ctrl+C sent to every process of this task]\n")
            if child is not None and child.poll() is None:
                stop_tree(child, float(task.get("grace_s", 10.0)))
                codes.append(None)
            log.write(b"[stopped]\n")
    task.update(exit_codes=codes, finished=_now(),
                status=STOPPED if stopped else DONE if all(c == 0 for c in codes) else FAILED)
    _write(folder, task)
    # The next task of the course, if one waits: the queue goes on with no GUI around.
    from tutor.gui.tasks import kick

    try:
        kick(folder.parents[2])  # <course>/logs/gui-tasks/<id>
    except OSError:
        pass  # the page's own kick starts it when it next reads the course
    return 0


def _shown(command: list[str], root: str | None) -> list[str]:
    """The command as a person would type it in the course folder: `python`, not the venv's
    full path, and paths inside the course relative to it."""
    shown = ["python", *command[1:]] if command and command[0] == sys.executable else command
    if root:
        top = root.rstrip("/\\")
        prefix = top + os.sep
        # The course folder itself is "." (e.g. `tutor.workspace init .`), what is inside it
        # relative to it.
        shown = ["." if part == top else part[len(prefix):] if part.startswith(prefix)
                 else part for part in shown]
    return shown


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":  # Ctrl+Break is how tasks.stop reaches it there
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    return run(Path(args[0]))


if __name__ == "__main__":
    sys.exit(main())
