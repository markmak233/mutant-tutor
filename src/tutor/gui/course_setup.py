"""A new course, and what a course still lacks before it can generate (GUI step 5).

A new course is `python -m tutor.workspace init` run as a task, with no lectures (owner,
2026-11-07: the Lectures page adds them, files or a folder); until init writes
workspace.toml, the folder is "being created", and only its task page is shown, with the
stages init has finished. Once it exists, the course page lists what stands between it and
generating (setup_gaps), each line linked to the page that fixes it (owner, 2026-11-06).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from tutor import languages
from tutor.config import WORKSPACE_FILE, Workspace, _enclosing_git_repo
from tutor.gui import tasks, toml_edit
from tutor.gui.courses import Course, _child
from tutor.gui.lectures import LectureRow
from tutor.workspace import venv_python

FOLDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")  # a new course's folder
_COURSE_ID = re.compile(r"^[\w.-]+$")  # as `tutor.workspace init` checks it


# --- a new course -----------------------------------------------------------------------------


def check_new_course(root: Path, folder: str, course_id: str) -> tuple[Path | None, str,
                                                                      list[str]]:
    """The new course's folder and id, or the problems. The folder must not exist yet."""
    folder, course_id = folder.strip(), course_id.strip() or folder.strip()
    problems = []
    if not FOLDER.match(folder):
        problems.append("The folder name takes lowercase letters, digits, '_' and '-', 40 at "
                        "most, e.g. comp1234 or deep-learning.")
    elif (root / folder).exists():
        problems.append(f"{folder} exists already in {root.name}: choose another name.")
    if not _COURSE_ID.match(course_id):
        problems.append("The course id takes letters, digits, '.', '_' and '-'.")
    if (repo := _enclosing_git_repo(root)) is not None:
        problems.append(f"The course folder would be inside the git repository {repo.name}, "
                        "where course material could be committed. Start the GUI with "
                        "--root pointing elsewhere.")
    return (None if problems else root / folder), course_id, problems


def init_command(folder: Path, course_id: str) -> list[str]:
    return [sys.executable, "-m", "tutor.workspace", "init", str(folder),
            f"--course={course_id}"]


def being_created(root: Path, name: str) -> Course | None:
    """A folder whose `workspace init` task the GUI started and which has no workspace.toml
    yet, as a course whose task pages can be shown; None otherwise."""
    folder = _child(root, name)
    if folder is None or (folder / WORKSPACE_FILE).exists() or not (
            folder / tasks.TASKS_DIR).is_dir():
        return None
    return Course(name, Workspace(folder, name, {}, folder / "lectures"))


@dataclass(frozen=True)
class Stage:
    label: str
    state: str  # "done", "running", "waiting", or "failed" (the task ended before it)


def create_progress(course: Course, task: tasks.Task) -> list[Stage]:
    """The stages of a "create" task, for its page: from the files init has made and the
    commands that have ended, not from the log's words. The install is one stage with no
    measure inside it, so the page shows how long it has run instead."""
    root = course.workspace.root
    codes = task.exit_codes
    # The venv's python appears first and pip last, once ensurepip has put it there.
    pip = venv_python(root / ".venv").with_stem("pip")
    finished = [(root / WORKSPACE_FILE).is_file() and (root / ".vscode" / "settings.json")
                .is_file(), pip.exists(), len(codes) >= 1 and codes[0] == 0]
    labels = ["Course files: workspace.toml and VS Code's settings",
              "Python environment (.venv)",
              "Installing this tool into it (a few minutes the first time)"]
    if len(task.commands) > 1:  # a course created before 2026-11-07 read its lectures too
        labels.append("Reading the lectures (the text, free)")
        finished.append(len(codes) >= 2 and codes[1] == 0)
    stages, current = [], True
    for label, done in zip(labels, finished, strict=True):
        if done:
            stages.append(Stage(label, "done"))
        elif current:
            stages.append(Stage(label, "running" if task.active else "failed"))
            current = False
        else:
            stages.append(Stage(label, "waiting"))
    return stages


def elapsed(task: tasks.Task) -> str:
    """How long the task has run, e.g. "2 min 05 s"."""
    seconds = int(tasks._age_s(task.started)) if task.started else 0
    if seconds < 0 or seconds > 86_400:
        return ""
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


# --- what a course still lacks ------------------------------------------------------------------


def setup_gaps(course: Course, rows: list[LectureRow]) -> list[tuple[str, str | None, str]]:
    """What stands between this course and generating, one (text, the page that fixes it
    as a route name or None, the link's words) each; [] when nothing. The course page links
    each line to its page (owner, 2026-11-06)."""
    data = toml_edit.read(course.workspace.root)
    gaps: list[tuple[str, str | None, str]] = []
    if not rows:
        gaps.append(("No lectures yet: upload the lecture PDFs or web pages, or a whole folder "
                     "of them, and tick which to keep.", "lecture_list",
                     "Lectures"))
    elif unread := [r for r in rows if not r.read]:
        gaps.append((f"{len(unread)} lecture{'s' if len(unread) > 1 else ''} not read yet.",
                     "lecture_list", "Lectures"))
    if not data.get("llm", {}).get("roles", {}).get("generate"):
        gaps.append(("No model writes exercises yet: choose one for the generate role, or "
                     "copy another course's models.", "models", "Models"))
    runner = data.get("isolation", {}).get("runner")
    if languages.is_c(data):
        # C exercises are validated only in a container (docs/design.md, "C").
        if runner != "container":
            gaps.append(("A C course's tests run only in a container: choose it, and build "
                         "the test image.", "environment_view", "Environment"))
    elif runner is None:
        gaps.append(("Where tests run is not chosen, so they run on this machine with a "
                     "warning.", "environment_view", "Environment"))
    gaps += [(f"{problem}.", "environment_view", "Environment")
             for problem in languages.problems(data)]
    if not (course.workspace.root / "coverage.md").is_file():
        gaps.append(("No coverage.md (optional): plans choose knowledge points from the "
                     "slides' titles alone, without knowing which are covered already.",
                     "coverage_view", "Coverage"))
    return gaps
