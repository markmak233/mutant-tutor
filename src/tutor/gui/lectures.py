"""Lectures and new courses (GUI step 5): what is read, uploads, and the commands that read.

A lecture is a PDF in the course's lecture folder; reading it fills the cache
(`python -m tutor.materials`, the existing command, run as a GUI task). Reading the text
layer costs nothing and starts after every upload; the image pages it cannot read wait for
the vision model, which runs only on the owner's click, as it costs money (owner,
2026-11-06).

Uploads (owner, 2026-11-06): PDFs only, checked by their first bytes and not only by name;
at most MAX_PDF_BYTES each; the name kept as given (any letters, spaces, brackets) unless it
holds a character no file name should, with ".pdf" in lower case, as the cache and the
plan look lectures up by that name; never over an existing file, since exercises cite its
slides. A batch of files is saved all or nothing: each is written to a hidden part file
first, and only when all are complete are they linked into place (a link fails rather than
replace a file).

A new course is `python -m tutor.workspace init` run as a task, after its PDFs are saved
into its lectures/; until init writes workspace.toml, the folder is "being created", and
only its task page is shown.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import tomllib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from tutor.config import WORKSPACE_FILE, Workspace, _enclosing_git_repo
from tutor.gui import tasks
from tutor.gui.courses import Course, _child
from tutor.materials import cache
from tutor.workspace import venv_python

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_NAME = 120  # characters of a lecture's file name
_BAD_CHARS = set('/\\:*?"<>|[]')  # separators, Windows' reserved ones, and glob's
FOLDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")  # a new course's folder
_COURSE_ID = re.compile(r"^[\w.-]+$")  # as `tutor.workspace init` checks it
_CHUNK = 1024 * 1024


class Upload(Protocol):
    """What an uploaded file offers (werkzeug's FileStorage)."""

    filename: str | None
    stream: BinaryIO


@dataclass(frozen=True)
class LectureRow:
    name: str  # the PDF's file name, e.g. "L4.pdf"
    pages: int  # pages in the cache; 0: not read yet
    waiting: int  # image pages waiting for the vision model

    @property
    def read(self) -> bool:
        return self.pages > 0


def lecture_rows(course: Course) -> list[LectureRow]:
    """Every PDF in the lecture folder, with what the cache holds of it."""
    folder = course.workspace.lecture_dir
    pdfs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
                  and not p.name.startswith(".")) if folder.is_dir() else []
    rows = []
    for pdf in pdfs:
        entries = cache.load(cache.cache_path(course.workspace.root, pdf, folder)).values()
        rows.append(LectureRow(pdf.name, len(entries),
                               sum(e.read in ("pending", "partial") for e in entries)))
    return rows


def vision_model(course: Course) -> str | None:
    """The model that reads image pages, as the form shows it; None when none is set."""
    llm = _toml(course.workspace.root).get("llm", {})
    if not llm:
        return "the LLM_* variables in .env"
    name = llm.get("roles", {}).get("vision")
    data = llm.get("profiles", {}).get(name) if name else None
    if not isinstance(data, dict):
        return None
    return f"{name} ({data.get('provider', '?')}: {data.get('model') or data.get('provider')})"


def read_command(pdfs: Iterable[Path], vision: bool = False) -> list[str]:
    """`python -m tutor.materials` on these PDFs: the text layer, or with `vision` the image
    pages too. Absolute paths, so none can pass for the command's own options."""
    return [sys.executable, "-m", "tutor.materials", *(["--vision"] if vision else []),
            *(str(p.resolve()) for p in pdfs)]


def save_pdfs(folder: Path, files: Iterable[Upload]) -> tuple[list[Path], list[str]]:
    """Save the uploaded PDFs into `folder`, all or none. Returns the saved paths, or the
    problems (each naming its file) when none was saved."""
    chosen = [(f, _name(f.filename or "")) for f in files if f.filename]
    if not chosen:
        return [], ["Choose at least one PDF."]
    problems, names = [], []
    for upload, name in chosen:
        problem = _name_problem(upload.filename or "", name)
        if problem is None and ((folder / name).exists()
                                or name.casefold() in (n.casefold() for n in names)):
            problem = (f"{name}: a lecture with this name exists. To replace it, delete the "
                       "old file first; exercises may cite its slides.")
        if problem:
            problems.append(problem)
        names.append(name)
    if problems:
        return [], problems
    folder.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    linked: list[Path] = []
    try:
        for upload, name in chosen:
            part = folder / f".upload-{secrets.token_hex(4)}.part"
            staged.append(part)
            if problem := _write(upload.stream, part, name):
                problems.append(problem)
        if not problems:
            for part, name in zip(staged, names, strict=True):
                os.link(part, folder / name)  # fails, never replaces, if one appeared since
                linked.append(folder / name)
    except FileExistsError as e:
        # os.link's error names the target second
        problems.append(f"{Path(e.filename2 or '').name}: a file with this name appeared "
                        "meanwhile; nothing was saved.")
    finally:
        for part in staged:
            part.unlink(missing_ok=True)
    if problems:
        for path in linked:
            path.unlink(missing_ok=True)
        return [], problems
    return linked, []


def _name(given: str) -> str:
    """The name to save under: the last part of what the browser sent (some send a path),
    in one Unicode form (macOS file names can arrive decomposed), ".pdf" in lower case."""
    name = unicodedata.normalize("NFC", given.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    stem, dot, extension = name.rpartition(".")
    return f"{stem}.pdf" if dot and extension.lower() == "pdf" else name


def _name_problem(given: str, name: str) -> str | None:
    if not name.endswith(".pdf"):
        return f"{name or given}: only PDF files are read for now."
    if name.startswith((".", "-")) or len(name) > MAX_NAME or any(
            c in _BAD_CHARS or ord(c) < 32 for c in name):
        return (f"{name}: rename it first: at most {MAX_NAME} characters, not starting with "
                "'.' or '-', and none of / \\ : * ? \" < > | [ ].")
    return None


def _write(stream: BinaryIO, part: Path, name: str) -> str | None:
    """Copy one upload to `part`; a problem if it is too large or not a PDF."""
    size = 0
    head = b""
    with part.open("wb") as out:
        while chunk := stream.read(_CHUNK):
            if len(head) < 1024:
                head += chunk[:1024]
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                return f"{name}: larger than {MAX_PDF_BYTES // (1024 * 1024)} MB."
            out.write(chunk)
    # A PDF starts with "%PDF-", which the format lets sit anywhere in the first 1024 bytes.
    if b"%PDF-" not in head[:1024]:
        return f"{name}: not a PDF file (its content does not start like one)."
    return None


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
    if len(task.commands) > 1:
        labels.append("Reading the lecture PDFs (the text, free)")
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


def remove_new(folder: Path) -> None:
    """Undo a new course folder this request made, when its PDFs could not be saved: it
    holds only lectures/, empty, as save_pdfs leaves nothing behind."""
    lectures = folder / "lectures"
    if lectures.is_dir() and not any(lectures.iterdir()):
        lectures.rmdir()
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


# --- what a course still lacks ------------------------------------------------------------------


def setup_gaps(course: Course, rows: list[LectureRow]) -> list[str]:
    """What stands between this course and generating, one line each; [] when nothing."""
    data = _toml(course.workspace.root)
    gaps = []
    if not rows:
        gaps.append("No lectures yet: upload the lecture PDFs.")
    elif unread := [r for r in rows if not r.read]:
        gaps.append(f"{len(unread)} lecture{'s' if len(unread) > 1 else ''} not read yet.")
    if not data.get("llm", {}).get("roles", {}).get("generate"):
        gaps.append("No model writes exercises yet: choose one for the generate role on "
                    "the Models page, or copy another course's models there.")
    runner = data.get("isolation", {}).get("runner")
    if runner is None:
        gaps.append("Where tests run is not chosen, so they run on this machine with a "
                    "warning. For a container: `python -m tutor.container build`, then "
                    "[isolation] runner = \"container\" in workspace.toml (README, \"Where "
                    "exercise code runs\").")
    if not (course.workspace.root / "coverage.md").is_file():
        gaps.append("No coverage.md (optional): plans choose knowledge points from the "
                    "slides' titles alone, without knowing which are covered already.")
    return gaps


def _toml(root: Path) -> dict:
    try:
        with (root / WORKSPACE_FILE).open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}

