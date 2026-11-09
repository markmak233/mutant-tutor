"""What the GUI shows, read from the course folders; nothing here writes.

The courses are the folders under one root that hold a workspace.toml, the way
`python -m tutor.workspace init` lays them out (owner, 2026-11-06). Each is checked like any
workspace (config.load_workspace: not inside a git repository, names a course).

Only the learner's side is read: `exercises/`, the manifest and the attempt log (each run's
time, exercise and result, never its `first_error`). Of
`solutions/`, only the yes or no in validation.json and, through the progress code shared with
the report, whether the student file still equals the skeleton; never the reference, tests, hints or
mutants, which matches the tutoring rule. Every name that comes from a URL goes through
`_child`, so a request cannot reach a folder outside its course or any hidden one.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tutor import container, image_recipe, workspace
from tutor.config import WORKSPACE_FILE, Workspace, WorkspaceError, load_workspace
from tutor.eval import report

# A folder name the GUI will follow: letters, digits, '.', '_', '-', and not hidden, so never
# '..', '.drafts' or a path with a separator in it.
_NAME = re.compile(r"^[\w-][\w.-]*$")
_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Course:
    folder: str  # its folder's name under the root; the URL uses this
    workspace: Workspace


@dataclass(frozen=True)
class ExerciseRow:
    id: str
    title: str
    priority: str  # must, optional or skip
    status: str  # report.PASSED, STARTED, UNLOGGED or NOT_STARTED
    runs_to_pass: int | None
    valid: bool | None  # validation.json's verdict; None when there is none


@dataclass(frozen=True)
class BatchView:
    name: str
    mode: str  # learn or review; "?" if the manifest does not say
    exercises: list[ExerciseRow]

    def count(self, status: str) -> int:
        return sum(row.status == status for row in self.exercises)


@dataclass(frozen=True)
class Run:
    """One logged test run, for the dashboard: when, which exercise and how it ended. Never
    `first_error`: a hand-calculation exercise's answer can sit in it."""

    time: str  # local time, "2026-11-04 12:09"
    batch: str
    exercise: str
    title: str
    result: str
    listed: bool  # the exercise is in a batch's manifest, so its page exists


@dataclass(frozen=True)
class CourseView:
    course: Course
    batches: list[BatchView]  # newest first
    recent: list[Run]  # newest first, at most RECENT_RUNS
    gaps: list[str]  # what could not be read, shown on the page rather than dropped

    @property
    def rows(self) -> list[ExerciseRow]:
        return [row for batch in self.batches for row in batch.exercises]

    def count(self, status: str | None = None, priority: str | None = None) -> int:
        return sum((status is None or r.status == status)
                   and (priority is None or r.priority == priority) for r in self.rows)

    @property
    def next(self) -> tuple[BatchView, ExerciseRow] | None:
        """What to do next: the first must exercise not yet passed, in the newest batch that
        has one, in the manifest's order (the lecture's order)."""
        for batch in self.batches:
            for row in batch.exercises:
                if row.priority == "must" and row.status != report.PASSED:
                    return batch, row
        return None


RECENT_RUNS = 5


def find_courses(root: Path) -> tuple[list[Course], list[str]]:
    """The course folders under `root`, and a line for each folder that holds a
    workspace.toml but is refused."""
    courses, problems = [], []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if not (_NAME.match(folder.name) and (folder / WORKSPACE_FILE).is_file()):
            continue
        try:
            workspace = load_workspace({"WORKSPACE_DIR": str(folder)})
        except (WorkspaceError, OSError, ValueError) as e:  # ValueError: a broken TOML file
            problems.append(f"{folder.name}: {e}")
            continue
        courses.append(Course(folder.name, workspace))
    return courses, problems


def get_course(root: Path, name: str) -> Course | None:
    """The course in folder `name`, or None if there is no such course."""
    return next((c for c in find_courses(root)[0] if c.folder == name), None)


def course_view(course: Course) -> CourseView:
    """Every batch of the course with its exercises' state, newest batch first."""
    records = report.collect(course.workspace.root)
    results = report.run_results(records.attempts)
    rated = {(r.get("batch"), r.get("exercise")) for r in records.ratings}
    batches = []
    for batch in reversed(records.batches):  # batch folders start with their date
        progress = report.exercise_progress(batch, results, rated)
        rows = [ExerciseRow(p.exercise, _title(batch.folder, p.exercise), p.priority, p.status,
                            p.runs_to_pass, _valid(batch.folder, p.exercise)) for p in progress]
        batches.append(BatchView(batch.name, _mode(batch.folder), rows))
    titles = {(b.name, r.id): r.title for b in batches for r in b.exercises}
    return CourseView(course, batches, _recent(records.attempts, titles), records.gaps)


def _recent(attempts: list[dict], titles: dict[tuple[str, str], str]) -> list[Run]:
    """The last RECENT_RUNS test runs. A hint request shares the log and has no result."""
    runs = [line for line in attempts if line.get("result") and isinstance(line.get("time"), str)]
    runs.sort(key=lambda line: line["time"], reverse=True)  # ISO times sort as text
    return [Run(line["time"][:16].replace("T", " "), str(line.get("batch")),
                str(line.get("exercise")),
                titles.get((line.get("batch"), line.get("exercise")), str(line.get("exercise"))),
                str(line["result"]), (line.get("batch"), line.get("exercise")) in titles)
            for line in runs[:RECENT_RUNS]]


def task_python(course: Course) -> str:
    """The interpreter a task that runs exercise code starts with (validation, generation).
    On this machine it is the course's own .venv (owner, 2026-11-06): the learner's tests run
    there and [requires] is installed there, so a test that imports a course package finds it,
    as it would not in this tool's venv; `tutor.workspace init` installs this tool into it.
    The test runs follow it, as they start `sys.executable`. When tests run in a container,
    the image holds the packages, so this tool's own interpreter; likewise for a course with
    no .venv yet."""
    try:
        with (course.workspace.root / WORKSPACE_FILE).open("rb") as f:
            runner = tomllib.load(f).get("isolation", {}).get("runner")
    except (OSError, tomllib.TOMLDecodeError):
        runner = None
    python = workspace.venv_python(course.workspace.root / ".venv")
    if runner == "container" or not python.is_file():
        return sys.executable
    return str(python)


def container_problem(course: Course) -> str | None:
    """Before a task that runs tests (validation, generation): None when they can run now,
    else why not, as a notice code: "docker-missing", "docker-stopped" or "image-missing".
    A course whose tests run on the host is not checked. The commands check again
    themselves; this only stops a task that would fail at once (owner, 2026-11-06)."""
    try:
        with (course.workspace.root / WORKSPACE_FILE).open("rb") as f:
            runner = tomllib.load(f).get("isolation", {}).get("runner")
    except (OSError, tomllib.TOMLDecodeError):
        return None  # the command reports it
    if runner != "container":
        return None
    image = image_recipe.image_name(course.workspace.course, course.workspace.effective_requires)
    if container.image_problem(image) is None:  # waits while a stopped VM wakes
        return None
    if shutil.which("docker") is None:
        return "docker-missing"
    return "image-missing" if container.docker_problem() is None else "docker-stopped"


def batch_view(course: Course, name: str) -> BatchView | None:
    if _child(course.workspace.root / "batches", name) is None:
        return None
    return next((b for b in course_view(course).batches if b.name == name), None)


def question(course: Course, batch: str, exercise: str) -> str | None:
    """The exercise's question.md as written, or None when it has none. The page shows it as
    text, escaped: it is generated, so it must never become markup."""
    folder = exercise_dir(course, batch, exercise)
    path = folder / "question.md" if folder else None
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def exercises_dir(course: Course, batch: str) -> Path | None:
    """The batch's exercises/ folder: what "Open folder" shows for a batch, rather than the
    batch folder, where solutions/ sits right beside it."""
    batch_dir = _child(course.workspace.root / "batches", batch)
    return _child(batch_dir, "exercises") if batch_dir else None


def exercise_dir(course: Course, batch: str, exercise: str) -> Path | None:
    folder = exercises_dir(course, batch)
    return _child(folder, exercise) if folder else None


def solution_dir(course: Course, batch: str, exercise: str) -> Path | None:
    """Only to hand to a command such as the validator; no page reads from it."""
    batch_dir = _child(course.workspace.root / "batches", batch)
    solutions = _child(batch_dir, "solutions") if batch_dir else None
    return _child(solutions, exercise) if solutions else None


def _child(parent: Path, name: str) -> Path | None:
    """parent/name if `name` is a plain visible folder name and the folder exists inside
    `parent` once links are followed; None otherwise."""
    if not _NAME.match(name):
        return None
    path = (parent / name).resolve()
    if not path.is_dir() or not path.is_relative_to(parent.resolve()):
        return None
    return path


def _title(batch_dir: Path, exercise: str) -> str:
    """The first heading of question.md, else the id. The id comes from the manifest, so it
    goes through _child like a name from a URL."""
    folder = _child(batch_dir / "exercises", exercise)
    try:
        text = (folder / "question.md").read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):  # TypeError: no such folder
        return exercise
    match = _HEADING.search(text)
    return match.group(1).strip() if match else exercise


def valid_verdict(batch_dir: Path, exercise: str) -> bool | None:
    """validation.json's verdict for one exercise of a batch; None when there is none."""
    return _valid(batch_dir, exercise)


def _valid(batch_dir: Path, exercise: str) -> bool | None:
    """Only the verdict: the report's details can quote expected values."""
    folder = _child(batch_dir / "solutions", exercise)
    data = _read_json(folder / "validation.json") if folder else None
    valid = data.get("valid") if data else None
    return valid if isinstance(valid, bool) else None


def _mode(batch_dir: Path) -> str:
    data = _read_json(batch_dir / "manifest.json")
    mode = data.get("mode") if data else None
    return mode if isinstance(mode, str) else "?"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
