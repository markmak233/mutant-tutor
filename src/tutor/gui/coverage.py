"""A course's Coverage page, on coverage.json (tutor.materials.coverage_store; owner,
2026-11-06 the page, 2026-11-10 the data).

Three states, by what the course has:
- Nothing yet: a model drafts the lectures (`python -m tutor.materials.coverage`, a task
  from the page's button, which names the model and the input tokens); "Use as coverage"
  makes coverage.json from the draft, and coverage.md with it.
- coverage.md alone (a course from before the data, or a file written by hand): the page
  shows it as it is, with what a conversion would read from it (lectures, rows, the lines
  that need a look) and one button that converts it, keeping the file as coverage.md.bak.
  Until then the plan reads the file as written.
- coverage.json: each lecture with its points, the ones a later draft no longer found
  listed for the owner to confirm; a point is edited on its own page (status, check, note,
  the covering exercises, text and slides), added, or removed; a lecture's title and notes
  too. "Draft" asks the model only for lectures not drafted yet or changed since (the hash
  of their text), merging each answer in; "Draft every lecture again" asks for all of them
  (the regeneration the owner planned), the owner's points and marks kept. coverage.md is
  rendered on every save; edited by hand it does not flow back, and the page says so.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from markupsafe import Markup

from tutor.gui import rendering
from tutor.gui.courses import Course
from tutor.gui.environment import setup_model
from tutor.materials import cache
from tutor.materials import coverage as coverage_draft
from tutor.materials import coverage_store as store_module
from tutor.materials.coverage_store import Lecture, Point, Store

COVERAGE_FILE = store_module.COVERAGE_FILE
STORE_FILE = store_module.STORE_FILE
DRAFT_FILE = coverage_draft.DRAFT_FILE
STATUSES = store_module.STATUSES


@dataclass(frozen=True)
class LectureView:
    name: str
    lecture: Lecture
    changed: bool  # the lecture's text differs from what it was drafted from (or never was)

    @property
    def gap_code(self) -> int:
        return sum(p.status == "gap-code" and not p.covered_by for p in self.lecture.live)

    @property
    def removed(self) -> list[Point]:
        return [p for p in self.lecture.points if p.removed]


@dataclass(frozen=True)
class Conversion:
    """What converting a hand-kept coverage.md would read from it (a dry run)."""
    lectures: int
    points: int
    report: list[str]


@dataclass(frozen=True)
class CoverageView:
    store: Store | None  # coverage.json, when the course has it
    lectures: list[LectureView] = field(default_factory=list)  # from the data, course order
    todo: list[str] = field(default_factory=list)  # lectures a draft run would ask for
    tokens_k: int = 0  # about how many thousand input tokens those would send
    model: str | None = None  # the model a draft run would ask; None: no model
    file_exists: bool = False  # coverage.md is there
    file_html: Markup | None = None  # coverage.md rendered, when there is no data yet
    conversion: Conversion | None = None  # for a file without data: the dry run
    stale: bool = False  # coverage.md was edited after the last save
    draft: coverage_draft.Draft | None = None  # stored answers, for a course with no data

    @property
    def points(self) -> int:
        return sum(len(lv.lecture.live) for lv in self.lectures)


def view(course: Course) -> CoverageView:
    root = course.workspace.root
    model = setup_model(course)
    store = store_module.load(root)
    path = root / COVERAGE_FILE
    if store is None:
        text = path.read_text(encoding="utf-8") if path.is_file() else None
        found = coverage_draft.draft(root)
        conversion = None
        if text is not None:
            read, report = store_module.from_markdown(text, store_module.known_lectures(root))
            conversion = Conversion(len(read.lectures),
                                    sum(len(x.points) for x in read.lectures.values()), report)
        tokens = sum(chars for _, chars in found.todo) // 4  # about 4 characters a token
        return CoverageView(None, todo=[name for name, _ in found.todo],
                            tokens_k=round(tokens / 1000), model=model,
                            file_exists=text is not None,
                            file_html=rendering.question_html(text) if text is not None else None,
                            conversion=conversion, draft=found)
    lectures, todo, chars = [], [], 0
    seen = set()
    for file in coverage_draft.lecture_files(root):
        name = cache.lecture_name(file) or file.name
        entries = cache.load(file)
        current = coverage_draft.lecture_hash(entries)
        lecture = store.lectures.get(name)
        changed = lecture is None or lecture.source_hash != current
        if changed:
            todo.append(name)
            chars += len(coverage_draft.lecture_text(entries))
        if lecture is not None:
            lectures.append(LectureView(name, lecture, changed))
            seen.add(name)
    # Lectures in the data whose cache is gone (renamed, deleted): still shown.
    lectures += [LectureView(name, lecture, True) for name, lecture in store.lectures.items()
                 if name not in seen]
    return CoverageView(store, lectures, todo, round(chars / 4000), model,
                        file_exists=path.is_file(),
                        stale=store_module.rendered_is_stale(root))


# --- actions -----------------------------------------------------------------------------------


def draft_command(every: bool = False) -> list[str]:
    """The task that asks the model and merges the answers into the data; for a course
    with no data yet, the drafting module, whose answers "Use as coverage" then takes."""
    command = [sys.executable, "-m", "tutor.materials.coverage_store", "redraft"]
    return [*command, "--all"] if every else command


def first_draft_command() -> list[str]:
    return [sys.executable, "-m", "tutor.materials.coverage"]


def use_draft(course: Course) -> list[str]:
    """The stored answers become coverage.json (and coverage.md), for a course with
    neither; the problems, else []."""
    root = course.workspace.root
    if store_module.load(root) is not None:
        return [f"{STORE_FILE} exists already: draft again from the lectures instead."]
    if (root / COVERAGE_FILE).is_file():
        return [f"{COVERAGE_FILE} exists already: convert it, then draft the lectures it lacks."]
    found = coverage_draft.draft(root)
    if not found.sections:
        return ["There is no draft yet: draft it from the lectures first."]
    store = Store()
    for file in coverage_draft.lecture_files(root):
        name = cache.lecture_name(file) or file.name
        section = next((s for s in found.sections
                        if s.lecture == file.name.removesuffix(".md")), None)
        if section is None:
            continue
        entries = cache.load(file)
        data = coverage_draft.stored(root, file, entries) or {}
        lecture = Lecture(file=file.name.removesuffix(".md"))
        drafted = [Point(id="", slides=r.slides, point=r.point, first_page=r.first_page,
                         last_page=r.last_page, **store_module.parse_status(r.status))
                   for r in section.rows]
        store_module.merge_draft(name, lecture, drafted, coverage_draft.lecture_hash(entries),
                                 str(data.get("model", "")), section.uncovered)
        store.lectures[name] = lecture
    store_module.save(root, store)
    return []


def convert(course: Course) -> list[str]:
    """coverage.md -> coverage.json, once, the file before kept as coverage.md.bak."""
    lines: list[str] = []
    code = store_module.migrate(course.workspace.root, out=lines.append)
    return lines if code else []


def render_again(course: Course) -> list[str]:
    """coverage.md written again from the data (after a hand edit of the file)."""
    store = store_module.load(course.workspace.root)
    if store is None:
        return [f"There is no {STORE_FILE} to render from."]
    store_module.save(course.workspace.root, store)
    return []


def save_lecture(course: Course, name: str, title: str, notes: str) -> list[str]:
    """A lecture's title and notes, from the page."""
    store = store_module.load(course.workspace.root)
    if store is None or name not in store.lectures:
        return ["No such lecture in the data; reload the page."]
    lecture = store.lectures[name]
    lecture.title, lecture.notes = " ".join(title.split()), notes.strip()
    store_module.save(course.workspace.root, store)
    return []


def point_fields(form) -> tuple[dict, list[str]]:
    """A point's editable fields from a form, checked: the problems, else the values."""
    problems = []
    slides = " ".join(form.get("slides", "").split())
    point = " ".join(form.get("point", "").split())
    status = form.get("status", "")
    if not slides:
        problems.append("Slides: the slide range, e.g. 4.15-4.24.")
    if not point:
        problems.append("Point: what the slides teach, in a few words.")
    if status not in STATUSES:
        problems.append(f"Status: one of {', '.join(STATUSES)}.")
    covered = store_module.parse_status(form.get("covered", "")).get("covered_by", [])
    if form.get("covered", "").strip() and not covered:
        problems.append("Covered by: marks such as ex:some-id (batch), separated by commas, "
                        "or nothing.")
    for name in ("check", "note"):
        if "|" in form.get(name, "") or "\n" in form.get(name, "").strip():
            problems.append(f"{name.capitalize()}: one line, with no |.")
    values = {"slides": slides, "point": point, "status": status,
              "check": " ".join(form.get("check", "").split()),
              "note": " ".join(form.get("note", "").split()), "covered_by": covered}
    return values, problems


def save_point(course: Course, name: str, point_id: str, values: dict) -> list[str]:
    """An edited point: its fields replaced, marked as the owner's (a later draft keeps it).
    The id follows the text and slides, so a renamed point gets a new one."""
    store = store_module.load(course.workspace.root)
    lecture = store.lectures.get(name) if store else None
    if lecture is None:
        return ["No such lecture in the data; reload the page."]
    found = next((p for p in lecture.points if p.id == point_id), None)
    if found is None:
        return ["No such point in the data; reload the page."]
    new_id = store_module.point_id(name, values["slides"], values["point"])
    if new_id != point_id and any(p.id == new_id for p in lecture.points):
        return ["The lecture has that point at those slides already."]
    index = lecture.points.index(found)
    lecture.points[index] = found.model_copy(update={**values, "id": new_id,
                                                     "owner_edited": True, "removed": False})
    store_module.save(course.workspace.root, store)
    return []


def add_point(course: Course, name: str, values: dict) -> list[str]:
    store = store_module.load(course.workspace.root)
    lecture = store.lectures.get(name) if store else None
    if lecture is None:
        return ["No such lecture in the data; reload the page."]
    new_id = store_module.point_id(name, values["slides"], values["point"])
    if any(p.id == new_id for p in lecture.points):
        return ["The lecture has that point at those slides already."]
    lecture.points.append(Point(id=new_id, owner_edited=True, **values))
    store_module.save(course.workspace.root, store)
    return []


def drop_point(course: Course, name: str, point_id: str) -> list[str]:
    """The point leaves the data: the owner's confirmation of a removal, or a deletion."""
    store = store_module.load(course.workspace.root)
    lecture = store.lectures.get(name) if store else None
    if lecture is None or not any(p.id == point_id for p in lecture.points):
        return ["No such point in the data; reload the page."]
    lecture.points = [p for p in lecture.points if p.id != point_id]
    store_module.save(course.workspace.root, store)
    return []


def keep_point(course: Course, name: str, point_id: str) -> list[str]:
    """A point a draft no longer found stays, as the owner's."""
    store = store_module.load(course.workspace.root)
    lecture = store.lectures.get(name) if store else None
    found = next((p for p in (lecture.points if lecture else []) if p.id == point_id), None)
    if found is None:
        return ["No such point in the data; reload the page."]
    found.removed, found.owner_edited = False, True
    store_module.save(course.workspace.root, store)
    return []


def find_point(course: Course, name: str, point_id: str) -> tuple[Lecture, Point] | None:
    store = store_module.load(course.workspace.root)
    lecture = store.lectures.get(name) if store else None
    found = next((p for p in (lecture.points if lecture else []) if p.id == point_id), None)
    return (lecture, found) if found is not None else None
