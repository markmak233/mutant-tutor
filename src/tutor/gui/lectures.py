"""Lectures and new courses (GUI step 5): what is read, uploads, and the commands that read.

A lecture is a PDF, or notes saved as a web page (.html, .htm; owner 2026-11-07), in the
course's lecture folder; reading it fills the cache
(`python -m tutor.materials`, the existing command, run as a GUI task). Reading the text
layer (or a web page's text) costs nothing and starts after every upload; the image pages
it cannot read wait for
the vision model, which runs only on the owner's click, as it costs money (owner,
2026-11-06).

Uploads (owner, 2026-11-06): PDFs and web pages only, checked by their first bytes and not
only by name; at most MAX_LECTURE_BYTES each; the name kept as given (any letters, spaces,
brackets) unless it holds a character no file name should, with the extension in lower case,
as the cache and the plan look lectures up by that name; never over an existing file, since
exercises cite its slides, nor beside one of the same name and another extension (L4.pdf and
L4.html would be one lecture). A batch of files is saved all or nothing: each is written to a
hidden part file first, and only when all are complete are they linked into place (a link
fails rather than replace a file).

A whole folder (owner, 2026-11-07): the browser sends every file in it, so only its PDFs and
web pages are kept, outside what a browser saves beside a page (`Name_files/`) and hidden
files. They wait in the course's STAGING_DIR, not yet lectures, while the owner unticks what
is none (exams, solutions): a lecture cannot be deleted from the GUI, and exercises cite it.
A file with no number in its name takes the one its subfolder starts with ("10 Matrices/
Quiz.htm" -> "10 Quiz.htm"), so its sections are numbered like its topic. The ticked files
then go through save_lectures like any upload. One folder waits at a time: choosing another
drops the one before.

A new course is `python -m tutor.workspace init` run as a task, with no lectures (owner,
2026-11-07: the Lectures page adds them, files or a folder); until init writes
workspace.toml, the folder is "being created", and only its task page is shown.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
import tomllib
import unicodedata
from collections import Counter
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from tutor import languages
from tutor.config import WORKSPACE_FILE, Workspace, _enclosing_git_repo
from tutor.gui import tasks
from tutor.gui.courses import Course, _child
from tutor.materials import cache
from tutor.materials.html import lecture_number, looks_like_html
from tutor.workspace import venv_python

MAX_LECTURE_BYTES = 100 * 1024 * 1024
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
    name: str  # the lecture's file name, e.g. "L4.pdf" or "notes 4.html"
    pages: int  # pages in the cache (a web page's sections); 0: not read yet
    waiting: int  # image pages waiting for the vision model

    @property
    def read(self) -> bool:
        return self.pages > 0

    @property
    def web_page(self) -> bool:
        """Notes saved as a web page: cut into sections, not pages, and no image read."""
        return not self.name.lower().endswith(".pdf")


def lecture_rows(course: Course) -> list[LectureRow]:
    """Every lecture in the lecture folder, with what the cache holds of it."""
    folder = course.workspace.lecture_dir
    files = sorted(p for p in folder.iterdir() if p.is_file() and cache.is_lecture(p)) \
        if folder.is_dir() else []
    rows = []
    for path in files:
        entries = cache.load(cache.cache_path(course.workspace.root, path, folder)).values()
        rows.append(LectureRow(path.name, len(entries),
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


def read_command(files: Iterable[Path], vision: bool = False) -> list[str]:
    """`python -m tutor.materials` on these lectures: the text, or with `vision` the image
    pages too. Absolute paths, so none can pass for the command's own options."""
    return [sys.executable, "-m", "tutor.materials", *(["--vision"] if vision else []),
            *(str(p.resolve()) for p in files)]


def save_lectures(folder: Path, files: Iterable[Upload]) -> tuple[list[Path], list[str]]:
    """Save the uploaded lectures (PDFs, web pages) into `folder`, all or none. Returns the
    saved paths, or the problems (each naming its file) when none was saved."""
    chosen = [(f, _name(f.filename or "")) for f in files if f.filename]
    if not chosen:
        return [], ["Choose at least one lecture file."]
    taken = _lecture_stems(folder)
    problems, names = [], []
    for upload, name in chosen:
        if problem := (_name_problem(upload.filename or "", name)
                       or _clash(folder, name, taken)):
            problems.append(problem)
        else:
            taken[Path(name).stem.casefold()] = "another file of this upload"
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
    in one Unicode form (macOS file names can arrive decomposed), a lecture's extension in
    lower case (".pdf", ".html", ".htm")."""
    name = unicodedata.normalize("NFC", given.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    stem, dot, extension = name.rpartition(".")
    lower = f".{extension.lower()}"
    return f"{stem}{lower}" if dot and lower in cache.LECTURE_SUFFIXES else name


def _lecture_stems(folder: Path) -> dict[str, str]:
    """The lectures in `folder` by their name before the extension, folded: a lecture is
    named by that, so L4.pdf and L4.html would be one lecture."""
    return {p.stem.casefold(): p.name for p in folder.iterdir() if cache.is_lecture(p)} \
        if folder.is_dir() else {}


def _clash(folder: Path, name: str, taken: dict[str, str]) -> str | None:
    """Why `name` cannot be saved beside the lectures in `folder`: one of that name exists,
    in any case (L1.pdf for l1.pdf: one file on macOS and Windows, two on Linux), or `taken`
    (_lecture_stems, plus what this upload saves before it) has its stem."""
    other = taken.get(Path(name).stem.casefold(), "")
    if (folder / name).exists() or other.casefold() == name.casefold():
        return (f"{name}: a lecture with this name exists. To replace it, delete the old "
                "file first; exercises may cite its slides.")
    if other:
        return (f"{name}: {other} has the same name before its extension, and a lecture is "
                "named by that: rename one of them.")
    return None


def _name_problem(given: str, name: str) -> str | None:
    if not name.endswith(cache.LECTURE_SUFFIXES):
        return (f"{name or given}: only PDF files and web pages saved from a browser (.html, "
                ".htm) are read.")
    if name.startswith((".", "-")) or len(name) > MAX_NAME or any(
            c in _BAD_CHARS or ord(c) < 32 for c in name):
        return (f"{name}: rename it first: at most {MAX_NAME} characters, not starting with "
                "'.' or '-', and none of / \\ : * ? \" < > | [ ].")
    return None


def _write(stream: BinaryIO, part: Path, name: str) -> str | None:
    """Copy one upload to `part`; a problem if it is too large, or its content is not what
    its name says (a PDF, or an HTML page)."""
    size = 0
    head = b""
    with part.open("wb") as out:
        while chunk := stream.read(_CHUNK):
            if len(head) < 4096:
                head += chunk[:4096]
            size += len(chunk)
            if size > MAX_LECTURE_BYTES:
                return f"{name}: larger than {MAX_LECTURE_BYTES // (1024 * 1024)} MB."
            out.write(chunk)
    if not name.endswith(".pdf"):
        if not looks_like_html(head):
            return f"{name}: not a web page (its content does not start like one)."
        return None
    # A PDF starts with "%PDF-", which the format lets sit anywhere in the first 1024 bytes.
    if b"%PDF-" not in head[:1024]:
        return f"{name}: not a PDF file (its content does not start like one)."
    return None


# --- a whole folder (owner, 2026-11-07) ---------------------------------------------------------

STAGING_DIR = ".lecture-uploads"  # in the course folder; a chosen folder's lectures wait here
MAX_FOLDER_FILES = 5000  # files one folder upload may send, lectures or not
_TOKEN = re.compile(r"^[0-9a-f]{16}$")  # a staging folder's name, so no path can pass for one
_LEADING_NUMBER = re.compile(r"^\d+(?:\.\d+)*(?=\D|$)")  # "10 Matrices" -> "10"


@dataclass(frozen=True)
class FolderFile:
    """One PDF or web page of a chosen folder: staged as <index>.part, not yet a lecture."""

    index: int
    folder: str  # the subfolders it was in, "a/b"; "" at the top of the chosen folder
    given: str  # its name there
    name: str  # the lecture's name it is saved under (_numbered)
    size: int  # bytes staged; 0 when it was not (it has a problem)
    problem: str | None  # why it cannot be saved, without its name; None: it can

    @property
    def size_text(self) -> str:
        if not self.size:
            return ""
        if self.size < 1024 * 1024:
            return f"{max(1, round(self.size / 1024))} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"


@dataclass(frozen=True)
class StagedFolder:
    token: str  # the staging folder's name
    folder: str  # the chosen folder's name, "" if the browser sent none
    files: list[FolderFile]
    skipped: dict[str, int]  # the files left out, by why: "hidden", "in _files folders", ".mp4"

    @property
    def groups(self) -> list[tuple[str, list[FolderFile]]]:
        """The files by subfolder, in order: stage_folder sorts them by subfolder first, so
        each subfolder's files come together."""
        groups: list[tuple[str, list[FolderFile]]] = []
        for file in self.files:
            if not groups or groups[-1][0] != file.folder:
                groups.append((file.folder, []))
            groups[-1][1].append(file)
        return groups


@dataclass(frozen=True)
class _Staged:
    """A staged file handed to save_lectures as if uploaded under its lecture name."""

    filename: str
    stream: BinaryIO


def stage_folder(course_root: Path, lecture_dir: Path,
                 uploads: Iterable[Upload]) -> tuple[str | None, list[str]]:
    """Copy the PDFs and web pages of a chosen folder (each upload named by its path in it,
    "chosen/sub/file.pdf", as browsers send a folder) into a new staging folder, with what
    stops each one being saved. Returns its token, or the problems when it holds none."""
    staging = course_root / STAGING_DIR
    _discard_all(staging)  # one folder waits at a time; one never confirmed goes now
    found: list[tuple[list[str], str, Upload]] = []
    skipped: Counter[str] = Counter()
    top = ""
    for upload in uploads:
        parts = [unicodedata.normalize("NFC", p)
                 for p in (upload.filename or "").replace("\\", "/").split("/") if p]
        if not parts:
            continue
        if len(parts) > 1:
            top, parts = parts[0], parts[1:]
        if why := _left_out(parts):
            skipped[why] += 1
        else:
            found.append((parts[:-1], parts[-1], upload))
    if not found:
        where = top or "the chosen folder"
        return None, [f"No PDF or web page (.pdf, .html, .htm) was found in {where} or its "
                      f"subfolders ({sum(skipped.values())} other files were left out)."
                      if skipped else "Choose a folder."]
    token = secrets.token_hex(8)
    target = staging / token
    target.mkdir(parents=True)
    # By subfolder, then name: the top of the folder first, "2 Dot" before "10 Matrices".
    found.sort(key=lambda f: (_natural("/".join(f[0])), _natural(f[1])))
    taken = _lecture_stems(lecture_dir)
    files = []
    for index, (folders, given, upload) in enumerate(found):
        name = _numbered(folders, _name(given))
        problem = _name_problem(given, name) or _clash(lecture_dir, name, taken)
        part = target / f"{index}.part"
        if problem is None and (problem := _write(upload.stream, part, name)):
            part.unlink(missing_ok=True)
        if problem is None:
            where = "/".join([*folders, given])
            taken[Path(name).stem.casefold()] = f"{where}, listed above,"
        size = part.stat().st_size if problem is None else 0
        files.append(FolderFile(index, "/".join(folders), given, name, size,
                                problem and problem.removeprefix(f"{name}: ")))
    data = {"folder": top, "files": [asdict(f) for f in files],
            "skipped": dict(skipped.most_common())}
    (target / "staged.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return token, []


def staged_folder(course_root: Path, token: str) -> StagedFolder | None:
    """The folder staged under `token`; None if there is none (saved, or another chosen)."""
    if not _TOKEN.match(token):
        return None
    try:
        data = json.loads((course_root / STAGING_DIR / token / "staged.json")
                          .read_text(encoding="utf-8"))
        return StagedFolder(token, data["folder"], [FolderFile(**f) for f in data["files"]],
                            data["skipped"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ticks(staged: StagedFolder, picks: Iterable[str], change: str) -> set[int]:
    """The ticks after one of the page's tick buttons (owner, 2026-11-07). The GUI has no
    script of its own, so a button sends the ticks as they are (`picks`, the form's indexes)
    and the page comes back with `change` made: "all", "none", "invert", or "on:<subfolder>"
    / "off:<subfolder>" for the files directly in it. Only files that can be saved are ever
    ticked."""
    can = {f.index for f in staged.files if f.problem is None}
    now = {int(p) for p in picks if p.isascii() and p.isdigit()} & can
    if change == "all":
        return can
    if change == "none":
        return set()
    if change == "invert":
        return can - now
    action, _, folder = change.partition(":")
    group = {f.index for f in staged.files if f.folder == folder} & can
    if action == "on":
        return now | group
    if action == "off":
        return now - group
    return now


def save_staged(course_root: Path, lecture_dir: Path, staged: StagedFolder,
                picks: Iterable[str]) -> tuple[list[Path], list[str]]:
    """Save the ticked files (`picks`: their indexes, as the form sends them) as lectures,
    all or none, through save_lectures, which checks them again, as a lecture may have
    appeared since. The staging folder goes once they are saved, and stays otherwise, so the
    owner can untick what was refused."""
    ticked = set(picks)
    chosen = [f for f in staged.files if str(f.index) in ticked and f.problem is None]
    if not chosen:
        return [], ["Tick at least one lecture to save."]
    folder = course_root / STAGING_DIR / staged.token
    with ExitStack() as stack:
        try:
            uploads = [_Staged(f.name, stack.enter_context((folder / f"{f.index}.part")
                                                            .open("rb"))) for f in chosen]
        except OSError:
            return [], ["The folder's files are no longer kept: choose the folder again."]
        saved, problems = save_lectures(lecture_dir, uploads)
    if not problems:
        discard_staged(course_root, staged.token)
    return saved, problems


def discard_staged(course_root: Path, token: str) -> None:
    if _TOKEN.match(token):
        shutil.rmtree(course_root / STAGING_DIR / token, ignore_errors=True)


def _discard_all(staging: Path) -> None:
    for child in staging.iterdir() if staging.is_dir() else []:
        if _TOKEN.match(child.name):
            shutil.rmtree(child, ignore_errors=True)


def _left_out(parts: list[str]) -> str | None:
    """Why a file of a chosen folder (its path in it, split) is not offered as a lecture,
    as the page counts it; None if it is offered."""
    if any(p.startswith(".") for p in parts):  # .DS_Store, .vscode/ (and never "..")
        return "hidden"
    if any(p.lower().endswith("_files") for p in parts[:-1]):  # a saved page's images, styles
        return "in _files folders"
    return None if cache.is_lecture(Path(parts[-1])) else (
        Path(parts[-1]).suffix.lower() or "with no extension")


def _numbered(folders: list[str], name: str) -> str:
    """A lecture name with no number takes the number its nearest subfolder starts with:
    "10 Matrices/Quiz review.htm" -> "10 Quiz review.htm", so its sections are numbered 10.N
    and it sorts with its topic. A name with a number keeps it as it is."""
    if lecture_number(Path(name)) is not None:
        return name
    for folder in reversed(folders):
        if found := _LEADING_NUMBER.match(folder):
            return f"{found.group(0)} {name}"
    return name


def _natural(text: str) -> list[str | int]:
    """A sort key putting "2 Dot product" before "10 Matrices": re.split with a group gives
    text at even places and digits at odd ones, so like is always compared with like."""
    return [int(t) if i % 2 else t.casefold() for i, t in enumerate(re.split(r"(\d+)", text))]


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
    data = _toml(course.workspace.root)
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
        # C exercises: validated only in a container, and not generated yet (docs/design.md).
        if runner != "container":
            gaps.append(("A C course's tests run only in a container: choose it, and build "
                         "the test image.", "environment_view", "Environment"))
        gaps.append(("Generating C exercises is not built yet; C exercises written by hand "
                     "validate already.", None, ""))
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


def _toml(root: Path) -> dict:
    try:
        with (root / WORKSPACE_FILE).open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}

