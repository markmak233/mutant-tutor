"""Long tasks the GUI starts: existing CLI commands run in the background, one after another.

Each task is a folder in the course's logs/gui-tasks/: task.json (what to run, and its state)
and output.log (everything the commands print). `start` writes task.json and launches
`python -m tutor.gui.runner <folder>` in a session of its own, then returns; the runner runs
the commands, writes their output to the log and their exit codes to task.json. So the page
only reads files, and a task goes on if the GUI stops (docs/design.md, "Local GUI").

The commands come from the GUI's own code with checked paths, never from a request. A course
runs one task at a time: validation already runs its checks in parallel, and two batches at
once could push runs past their time limits, a false INVALID.

The runner owns task.json once it has started; before that, this module writes it once.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tutor import locations

TASKS_DIR = Path("logs") / "gui-tasks"
TASK_FILE = "task.json"
LOG_FILE = "output.log"
KEEP = 20  # finished tasks kept per course; older ones are deleted when a new one starts
TAIL_BYTES = 64_000  # how much of the end of a log a page shows
STOP_GRACE_S = 10.0  # after Ctrl+C, how long the commands get before they are killed
# Commands read their config themselves; the GUI never loads keys into its environment.
# Packaged tasks use private app config rather than an unwritable site-packages parent.
REPO = locations.commands()
_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_start_lock = threading.Lock()  # waitress serves requests on several threads

RUNNING, DONE, FAILED, STOPPED, LOST = "running", "done", "failed", "stopped", "lost"


class TaskBusy(Exception):
    """The course already has a running task; `task` is that one."""

    def __init__(self, task: Task):
        super().__init__(task.id)
        self.task = task


@dataclass(frozen=True)
class Task:
    id: str
    folder: Path
    title: str
    status: str  # running, done, failed, stopped or lost
    started: str  # local time, "2026-11-06 15:30:12"
    finished: str | None
    commands: list[list[str]]
    exit_codes: list[int | None]  # one per command run so far; None for a stopped one
    pid: int | None
    # What the task belongs to, for the pages: e.g. {"kind": "plan", "label": "w9-streams"}
    # for generation (generation.py). The runner ignores it.
    meta: dict = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status == RUNNING


def start(course_root: Path, title: str, commands: list[list[str]],
          env: dict[str, str], grace_s: float = STOP_GRACE_S, meta: dict | None = None) -> Task:
    """Write the task and launch its runner. Raises TaskBusy if one is running already.
    `env`: variables these commands need on top of this process's own (the course's
    WORKSPACE_DIR and LECTURE_DIR). `meta`: see Task."""
    with _start_lock:
        locations.prepare()
        if (busy := running(course_root)) is not None:
            raise TaskBusy(busy)
        base = course_root / TASKS_DIR
        _prune(base)
        # Names sort by start time: the second, then its microseconds in six hex digits
        # (it was random, and two tasks in one second could sort the wrong way round, so a
        # plan's page took the older task for its newest). Unique, as starts take the lock.
        now = datetime.now()
        task_id = f"{now:%Y%m%d-%H%M%S}-{now.microsecond:06x}"
        folder = base / task_id
        folder.mkdir(parents=True)
        _write(folder, {"title": title, "status": RUNNING, "started": _now(), "finished": None,
                        "commands": commands, "env": env, "exit_codes": [], "pid": None,
                        "grace_s": grace_s, "meta": meta or {}})
        (folder / LOG_FILE).touch()
        # A session of its own: Ctrl+C on the GUI's terminal does not reach it, and stop()
        # can signal it alone. Nothing of it is inherited: no open files, no terminal.
        flags = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                 if sys.platform == "win32" else {"start_new_session": True})
        subprocess.Popen([sys.executable, "-m", "tutor.gui.runner", str(folder)], cwd=REPO,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **flags)
        return get(course_root, task_id)


def get(course_root: Path, task_id: str) -> Task | None:
    if not _ID.match(task_id):
        return None
    folder = course_root / TASKS_DIR / task_id
    try:
        data = json.loads((folder / TASK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = data.get("status", LOST)
    pid = data.get("pid")
    if status == RUNNING and pid is not None and not _alive(pid):
        status = LOST  # the runner died without writing its end
    elif status == RUNNING and pid is None and _age_s(str(data.get("started", ""))) > 30:
        status = LOST  # the runner never started, so it would block the course for good
    return Task(task_id, folder, str(data.get("title", task_id)), status,
                str(data.get("started", "")), data.get("finished"),
                list(data.get("commands", [])), list(data.get("exit_codes", [])), pid,
                meta if isinstance(meta := data.get("meta"), dict) else {})


def list_tasks(course_root: Path) -> list[Task]:
    """Newest first."""
    base = course_root / TASKS_DIR
    names = sorted((p.name for p in base.iterdir() if _ID.match(p.name)), reverse=True) \
        if base.is_dir() else []
    return [task for name in names if (task := get(course_root, name)) is not None]


def running(course_root: Path) -> Task | None:
    return next((t for t in list_tasks(course_root) if t.active), None)


def stop(task: Task) -> bool:
    """Ask the runner to stop: it passes Ctrl+C to every process of the task, then kills what
    is left after the grace time. False if it is not running (or has not started yet)."""
    if not task.active or task.pid is None:
        return False
    try:
        os.kill(task.pid, signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def tail(task: Task, limit: int = TAIL_BYTES) -> tuple[str, bool]:
    """The end of the log, and whether earlier output was cut."""
    path = task.folder / LOG_FILE
    try:
        with path.open("rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return "", False
    return data.decode("utf-8", errors="replace"), size > limit


QUIET_S = 30  # a running task silent this long gets a line saying what it waits for


def quiet_s(task: Task) -> float | None:
    """How long a running task's log has had no new output, once that is QUIET_S or more;
    None otherwise. A step waiting for a model prints nothing until the reply is whole (a
    plan call once took 12 minutes), and the owner could not tell that from a stuck task
    (owner, 2026-11-06)."""
    if not task.active:
        return None
    try:
        since = time.time() - (task.folder / LOG_FILE).stat().st_mtime
    except OSError:
        return None
    return since if since >= QUIET_S else None


def _prune(base: Path) -> None:
    """Keep the newest KEEP - 1 tasks, so with the new one there are KEEP; never a running one."""
    if not base.is_dir():
        return
    names = sorted((p.name for p in base.iterdir() if _ID.match(p.name)), reverse=True)
    for name in names[KEEP - 1:]:
        task = get(base.parent.parent, name)
        if task is not None and not task.active:
            shutil.rmtree(base / name, ignore_errors=True)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        # A runner that died there showed as running for good, and held the course, which
        # runs one task at a time (found by Windows CI, 2026-11-08).
        from tutor import winjob  # ctypes.WinDLL exists only on Windows

        return winjob.process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _age_s(started: str) -> float:
    try:
        return (datetime.now() - datetime.strptime(started, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return float("inf")


def _write(folder: Path, data: dict) -> None:
    """Whole-file replace, so a reader never sees half a file."""
    tmp = folder / f".{TASK_FILE}.tmp"
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, folder / TASK_FILE)
