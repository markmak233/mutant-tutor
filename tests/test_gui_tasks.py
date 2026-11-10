"""GUI tasks with real runner processes: order, exit codes, the log, one task per course,
and stopping, down to a process in a session of its own (as the test runner starts pytest
and `docker run`)."""

import hashlib
import json
import os
import shlex
import signal
import sys
import time
from pathlib import Path

import pytest

from tutor.gui import tasks

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="stopping uses ps and signals")


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def wait_end(course: Path, task: tasks.Task, within_s: float = 30) -> tasks.Task:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        now = tasks.get(course, task.id)
        if not now.active:
            return now
        time.sleep(0.1)
    raise AssertionError(f"task still running after {within_s} s")


def wait_for(check, within_s: float = 15):
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if (value := check()):
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def gone(pid: int, within_s: float = 5) -> bool:
    """Ended, a zombie counting as ended (see tests/test_execution.py)."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            state = stat.rsplit(") ", 1)[1].split()[0]
            if state == "Z":
                return True
        except FileNotFoundError:
            if sys.platform.startswith("linux"):
                return True
        except OSError:
            pass
        if not sys.platform.startswith("linux"):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
        time.sleep(0.05)
    return False


def test_commands_run_in_order_and_every_one_runs(tmp_path):
    """A failing command does not stop the next: a batch's validation goes on past one
    INVALID exercise."""
    task = tasks.start(tmp_path, "demo", [py("print('first')"), py("import sys; sys.exit(3)"),
                                          py("print('third')")], {})
    done = wait_end(tmp_path, task)
    log, cut = tasks.tail(done)

    assert done.status == tasks.FAILED and done.exit_codes == [0, 3, 0]
    assert log.index("first") < log.index("[exit 3]") < log.index("third") and not cut
    assert "$ python -c" in log  # the command as typed, not the venv's full path
    assert done.finished


def test_a_task_gets_the_courses_variables(tmp_path):
    task = tasks.start(tmp_path, "env", [py("import os; print(os.environ['WORKSPACE_DIR'])")],
                       {"WORKSPACE_DIR": "/the/course"})

    assert wait_end(tmp_path, task).status == tasks.DONE
    assert "/the/course" in tasks.tail(task)[0]


def test_a_second_task_queues_and_starts_when_the_first_ends(tmp_path):
    """A course runs one task at a time; the next waits, then the runner hands over."""
    first = tasks.start(tmp_path, "slow", [py("import time; time.sleep(1.5)")], {})
    wait_for(lambda: tasks.get(tmp_path, first.id).pid)
    second = tasks.start(tmp_path, "second", [py("print('ran')")], {})
    third = tasks.start(tmp_path, "third", [py("print('ran too')")], {})

    assert first.active and second.queued and third.queued and second.pid is None
    assert [t.id for t in tasks.queued(tmp_path)] == [second.id, third.id]  # next first
    assert [t.id for t in tasks.pending(tmp_path)] == [first.id, second.id, third.id]
    assert tasks.running(tmp_path).id == first.id
    assert tasks.kick(tmp_path) is None  # nothing to do while the first runs

    wait_end(tmp_path, first)
    done = wait_for(lambda: (t := tasks.get(tmp_path, third.id)).status == tasks.DONE and t)
    assert tasks.get(tmp_path, second.id).status == tasks.DONE
    log = (second.folder / tasks.LOG_FILE).read_text(encoding="utf-8")
    assert log.count("\nran\n") == 1  # ran once (the command line echoes "ran" too)
    assert done.started >= tasks.get(tmp_path, second.id).started  # started when it did
    assert (second.folder / tasks.CLAIM_FILE).is_file()  # claimed once, by the hand-over


def test_a_kick_that_loses_the_claim_starts_nothing_else(tmp_path, monkeypatch):
    """Two processes (a runner ending, a page's poll) read the same queue: the one whose
    claim on the oldest task fails must not go on and start the next one beside it (one
    task per course). Here the other process is a claim made by hand."""
    started = []
    monkeypatch.setattr(tasks.subprocess, "Popen",
                        lambda cmd, **kw: started.append(Path(cmd[-1]).name))
    first = tasks.start(tmp_path, "first", [py("pass")], {})  # claimed here, by this call
    data = json.loads((first.folder / tasks.TASK_FILE).read_text(encoding="utf-8"))
    tasks._write(first.folder, {**data, "status": tasks.DONE})  # ended (no runner ran)
    for title in ("second", "third"):
        monkeypatch.setattr(tasks, "running", lambda root: first)  # queue only, no start
        tasks.start(tmp_path, title, [py("pass")], {})
    monkeypatch.undo()
    monkeypatch.setattr(tasks.subprocess, "Popen",
                        lambda cmd, **kw: started.append(Path(cmd[-1]).name))
    second, third = tasks.queued(tmp_path)
    assert tasks._claim(second.folder)  # the other process, this moment
    started.clear()

    assert tasks.kick(tmp_path) is None
    assert started == [] and tasks.get(tmp_path, third.id).queued


def test_a_failed_task_does_not_hold_the_queue(tmp_path):
    first = tasks.start(tmp_path, "fails", [py("import sys; sys.exit(3)")], {})
    second = tasks.start(tmp_path, "second", [py("pass")], {})

    wait_for(lambda: tasks.get(tmp_path, second.id).status == tasks.DONE)
    assert tasks.get(tmp_path, first.id).status == tasks.FAILED


def test_a_queued_task_can_be_removed_but_not_a_running_one(tmp_path):
    first = tasks.start(tmp_path, "slow", [py("import time; time.sleep(30)")], {})
    wait_for(lambda: tasks.get(tmp_path, first.id).pid)
    second = tasks.start(tmp_path, "second", [py("pass")], {})
    try:
        assert tasks.remove(second) is True
        assert tasks.get(tmp_path, second.id) is None and tasks.queued(tmp_path) == []
        assert tasks.remove(first) is False
        assert tasks.get(tmp_path, first.id).active
    finally:
        tasks.stop(tasks.get(tmp_path, first.id))
        wait_end(tmp_path, first)


def test_a_claimed_task_starts_for_one_caller_only(tmp_path, monkeypatch):
    """Two processes may notice the queue at once (a runner ending, a page loading)."""
    launched = []
    monkeypatch.setattr(tasks.subprocess, "Popen", lambda *a, **k: launched.append(a[0]))
    task = tasks.start(tmp_path, "one", [py("pass")], {})  # launched by start's own kick
    assert len(launched) == 1 and tasks.get(tmp_path, task.id).active

    # Pretend the runner's end and a page both kick while the task waits: write it back
    # as queued with its claim file in place, as a competing process would find it.
    data = json.loads((task.folder / tasks.TASK_FILE).read_text(encoding="utf-8"))
    data["status"] = tasks.QUEUED
    tasks._write(task.folder, data)
    assert tasks.kick(tmp_path) is None and len(launched) == 1  # the claim stands


def test_a_queue_left_by_a_dead_runner_goes_on_at_the_next_kick(tmp_path):
    """The runner hands over at its end; a runner that died (lost) cannot, so the next
    kick, from a page reading the course, starts the queue again."""
    folder = tmp_path / tasks.TASKS_DIR / "20260101-000000-abcdef"
    folder.mkdir(parents=True)
    dead = os.getpid() + 10_000_000  # not a real process: lost
    (folder / tasks.TASK_FILE).write_text(json.dumps(
        {"title": "x", "status": "running", "started": "2026-01-01 00:00:00", "pid": dead,
         "commands": [], "exit_codes": [], "meta": {}}), encoding="utf-8")
    waiting = tmp_path / tasks.TASKS_DIR / "20260101-000001-abcdef"
    waiting.mkdir()
    tasks._write(waiting, {"title": "second", "status": tasks.QUEUED, "started": "",
                           "finished": None, "commands": [py("pass")], "env": {},
                           "exit_codes": [], "pid": None, "grace_s": 1, "meta": {}})
    assert tasks.get(tmp_path, waiting.name).queued

    started = tasks.kick(tmp_path)  # what a page does when it reads the course

    assert started is not None and started.id == waiting.name
    wait_for(lambda: tasks.get(tmp_path, waiting.name).status == tasks.DONE)


def test_pruning_never_drops_a_queued_task(tmp_path):
    slow = tasks.start(tmp_path, "slow", [py("import time; time.sleep(30)")], {})
    wait_for(lambda: tasks.get(tmp_path, slow.id).pid)
    try:
        waiting = tasks.start(tmp_path, "waiting", [py("pass")], {})
        for n in range(tasks.KEEP + 3):  # more than KEEP finished-looking folders
            folder = tmp_path / tasks.TASKS_DIR / f"20200101-0000{n:02d}-00000{n % 10}"
            folder.mkdir()
            tasks._write(folder, {"title": "old", "status": "done", "started": "", "finished": "",
                                  "commands": [], "exit_codes": [0], "pid": None, "meta": {}})
        tasks.start(tmp_path, "newest", [py("pass")], {})  # prunes
        assert tasks.get(tmp_path, waiting.id) is not None and tasks.get(tmp_path, slow.id).active
    finally:
        tasks.stop(tasks.get(tmp_path, slow.id))
        wait_end(tmp_path, slow)


@POSIX_ONLY
def test_stop_reaches_a_process_in_a_session_of_its_own(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    command = py("import subprocess, sys, time\n"
                 "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
                 " start_new_session=True)\n"
                 f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
                 "time.sleep(60)\n")
    task = tasks.start(tmp_path / "course", "stop me", [command, py("print('never')")], {})
    wait_for(lambda: pid_file.is_file() and pid_file.read_text(encoding="utf-8"))

    assert tasks.stop(tasks.get(tmp_path / "course", task.id))
    done = wait_end(tmp_path / "course", task)

    assert done.status == tasks.STOPPED and done.exit_codes == [None]
    assert gone(int(pid_file.read_text(encoding="utf-8")))
    assert "print('never')" not in tasks.tail(done)[0]  # the next command never started


@POSIX_ONLY
def test_a_process_that_ignores_ctrl_c_is_killed_after_the_grace_time(tmp_path):
    pid_file = tmp_path / "stubborn.pid"
    command = py("import os, signal, time\n"
                 "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                 f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                 "time.sleep(60)\n")
    task = tasks.start(tmp_path / "course", "stubborn", [command], {}, grace_s=1.0)
    wait_for(lambda: pid_file.is_file() and pid_file.read_text(encoding="utf-8"))
    started = time.monotonic()

    tasks.stop(tasks.get(tmp_path / "course", task.id))
    done = wait_end(tmp_path / "course", task)

    assert done.status == tasks.STOPPED and time.monotonic() - started < 10
    assert gone(int(pid_file.read_text(encoding="utf-8")))


def test_a_runner_that_died_shows_as_lost_and_frees_the_course(tmp_path):
    folder = tmp_path / tasks.TASKS_DIR / "20260101-000000-abcdef"
    folder.mkdir(parents=True)
    dead = os.getpid() + 10_000_000  # not a real process
    (folder / tasks.TASK_FILE).write_text(json.dumps(
        {"title": "x", "status": "running", "started": "2026-01-01 00:00:00", "pid": dead}),
        encoding="utf-8")

    assert tasks.get(tmp_path, folder.name).status == tasks.LOST
    assert tasks.running(tmp_path) is None


def test_a_runner_that_is_alive_stays_running(tmp_path):
    # The other half of the check above, on each platform's own way to ask (Windows: an exit
    # code through ctypes; elsewhere a signal 0): this process is surely alive.
    folder = tmp_path / tasks.TASKS_DIR / "20260101-000000-abcdef"
    folder.mkdir(parents=True)
    (folder / tasks.TASK_FILE).write_text(json.dumps(
        {"title": "x", "status": "running", "started": "2026-01-01 00:00:00",
         "pid": os.getpid()}), encoding="utf-8")

    assert tasks.get(tmp_path, folder.name).status == tasks.RUNNING


def test_a_runner_that_never_started_is_lost_after_30_s(tmp_path):
    folder = tmp_path / tasks.TASKS_DIR / "20260101-000000-abcdef"
    folder.mkdir(parents=True)
    (folder / tasks.TASK_FILE).write_text(json.dumps(
        {"title": "x", "status": "running", "started": "2026-01-01 00:00:00", "pid": None}),
        encoding="utf-8")

    assert tasks.get(tmp_path, folder.name).status == tasks.LOST


def test_old_tasks_are_pruned_to_the_newest(tmp_path):
    base = tmp_path / tasks.TASKS_DIR
    for i in range(tasks.KEEP + 5):
        folder = base / f"20260101-{i:06d}-abcdef"
        folder.mkdir(parents=True)
        (folder / tasks.TASK_FILE).write_text(json.dumps(
            {"title": str(i), "status": "done", "started": "2026-01-01 00:00:00"}),
            encoding="utf-8")

    task = tasks.start(tmp_path, "new", [py("pass")], {})
    wait_end(tmp_path, task)

    names = [t.id for t in tasks.list_tasks(tmp_path)]
    assert len(names) == tasks.KEEP and names[0] == task.id
    assert "20260101-000000-abcdef" not in names  # the oldest went first


def test_tasks_started_in_one_second_list_newest_first(tmp_path):
    """The ids sort by start time within a second too: a plan's page reads the newest task
    for its label, and a random part let an older one come first."""
    started = []
    for n in range(3):
        task = tasks.start(tmp_path, str(n), [py("pass")], {})
        started.append(task.id)
        wait_end(tmp_path, task)

    assert [t.id for t in tasks.list_tasks(tmp_path)] == started[::-1]


def test_only_task_ids_of_the_right_shape_are_read(tmp_path):
    for bad in ["..", "../x", "20260101-000000-ABCDEF", "x"]:
        assert tasks.get(tmp_path, bad) is None


def test_the_tail_is_cut_from_the_front(tmp_path):
    task = tasks.start(tmp_path, "long", [py("print('x' * 100_000); print('the end')")], {})
    log, cut = tasks.tail(wait_end(tmp_path, task))

    assert cut and log.rstrip().endswith("[exit 0]") and "the end" in log


def test_the_vendored_htmx_is_the_file_checked_against_npm():
    """npm's sha512 for htmx.org 2.0.11 matched on 2026-11-06; this is the extracted file's
    sha256, so any change to it shows here."""
    path = Path(tasks.__file__).parent / "static" / "htmx-2.0.11.min.js"

    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717")


@POSIX_ONLY
def test_stop_works_when_the_gui_was_started_with_ctrl_c_ignored(tmp_path):
    """A process started in the background has SIGINT ignored and passes that on; the runner
    must set its own handler, or Stop does nothing (the first real click showed it)."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)  # the runner inherits this
    try:
        task = tasks.start(tmp_path, "slow", [py("import time; time.sleep(60)")], {})
    finally:
        signal.signal(signal.SIGINT, previous)
    wait_for(lambda: tasks.get(tmp_path, task.id).pid)
    time.sleep(0.3)  # the handler is set first thing, but give the import a moment

    tasks.stop(tasks.get(tmp_path, task.id))

    assert wait_end(tmp_path, task, within_s=15).status == tasks.STOPPED


def test_the_log_shows_course_paths_relative_to_the_course(tmp_path):
    inside = tmp_path / "batches" / "b1"
    task = tasks.start(tmp_path, "paths", [py("pass") + [str(inside), str(tmp_path)]],
                       {"WORKSPACE_DIR": str(tmp_path)})
    log = tasks.tail(wait_end(tmp_path, task))[0]

    # The course itself is "."; a path inside it keeps the platform's separator (Windows' "\\"
    # makes shlex quote it).
    shown = shlex.join([os.path.join("batches", "b1"), "."])
    assert f" {shown}" in log and str(tmp_path) not in log


def test_a_stored_time_is_shown_local_to_the_second_like_a_task_time():
    from datetime import datetime

    stored = "2026-01-02T03:04:05.678901+00:00"  # as an image record keeps it
    shown = tasks.local_time(stored)
    assert shown == datetime.fromisoformat(stored).astimezone().strftime(tasks.TIME_FORMAT)
    assert datetime.strptime(shown, tasks.TIME_FORMAT)  # the form a task's "started" has
    assert tasks.local_time("2026-01-02T03:04:05") == "2026-01-02 03:04:05"  # no zone: local
    # Not a time: shown as it is, never an error on the page.
    assert tasks.local_time("unknown") == "unknown" and tasks.local_time(None) == "None"
