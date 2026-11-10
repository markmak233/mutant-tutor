"""coverage.json: a course's knowledge-point coverage as data, with coverage.md rendered
from it (owner, 2026-11-09, built 2026-11-10; docs/design.md, "coverage as JSON").

Before, coverage.md was the source of truth: a Markdown table the owner edited by hand, the
plan read for its gaps (graph/batch.py) and a batch only *proposed* changes to. A lecture
that changed had no safe way to new rows without losing the owner's edits, and whether a
lecture had rows at all could only be guessed from headings. Now:

- `coverage.json` holds, per lecture (keyed by the lecture's name as a plan names it, e.g.
  "Lecture_9.1-MapReduce_V"): the hash of the lecture text it was drafted from, when and by
  which model, the owner's title and notes, and its points. A point has a stable id (a hash
  of the lecture, its slides and its text, never its position: adding a row moves nothing),
  slides, text, status (the five statuses coverage.md always had), the check an exercise
  could make, a free note, the exercises that cover it (`covered_by`, the `ex:<id> (batch)`
  marks), whether the owner edited it, and whether a later draft no longer found it
  (`removed`, for the owner to confirm, never deleted silently).
- `coverage.md` is rendered from it on every save, in the shape the file always had, so the
  plan prompt receives the same table for the same data (the prompt's text is pinned and
  unchanged; see `planner_text`). Edits to the rendered file do not flow back: the Coverage
  page says so when it finds the file newer than the data.
- `from_markdown` reads an existing coverage.md once into the data, both shapes the files
  had (a draft's `## <lecture file>` headings; a hand-written file's `## Week N` / `### L8.1
  Title` headings, mapped to lectures by their numbers), keeping every line: a row that
  cannot be read stays as a note. `migrate` keeps the file before as coverage.md.bak.
- `merge_draft` takes a new model draft of one lecture into the data: a point the owner
  edited is kept as is, a new point is added, a point the draft no longer has is marked
  removed. A batch marks the points its saved exercises cover (`mark_covered`) when the
  item's lecture and slides match one point exactly; otherwise it stays a proposal.

Command line (the drafting itself stays `python -m tutor.materials.coverage`):
    python -m tutor.materials.coverage_store migrate    coverage.md -> coverage.json (+ .bak)
    python -m tutor.materials.coverage_store render     coverage.json -> coverage.md
    python -m tutor.materials.coverage_store redraft [--all] [--profile P]
        ask the model for the lectures not drafted yet or changed since (--all: every
        lecture), and merge the answers in
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tutor.materials import cache
from tutor.materials.slides import lecture_number

STORE_FILE = "coverage.json"
COVERAGE_FILE = "coverage.md"
VERSION = 1
Status = Literal["gap-code", "gap-tracing", "gap-design", "concept", "n/a"]
STATUSES = ("gap-code", "gap-tracing", "gap-design", "concept", "n/a")

# A row's status cell, as the files wrote it: `ex:<id>` / `example:<id>` marks with the
# batch in brackets after them, a status word with a check in square brackets, and any
# note in round brackets.
_MARK = re.compile(r"\b(ex|example):([A-Za-z0-9_.-]+)")
_BATCH = re.compile(r"\(([^()]*)\)\s*$")
_CHECK = re.compile(r"\[(.*)\]", re.DOTALL)
# Table cells split on a | that is not escaped (a cell may hold `\|`).
_CELL = re.compile(r"(?<!\\)\|")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_UNCOVERED = re.compile(r"^Pages no point covers:\s*(.*?)\.?\s*$")
_PAGE_RUN = re.compile(r"p\.(\d+)(?:-(\d+))?")


# --- the data ----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Covered(_Strict):
    """One exercise (or repository example) that practises a point."""
    mark: str  # "ex:<id>" or "example:<id>", as the status cell writes it
    batch: str = ""  # the batch's label, "" when the file did not say


class Point(_Strict):
    id: str
    slides: str  # slide footers, "4.15-4.24", or "p.N" where a page has none
    point: str
    status: Status = "gap-code"
    check: str = ""  # what an exercise could check (the text in square brackets)
    note: str = ""  # any other text of the status cell, kept as written
    covered_by: list[Covered] = Field(default_factory=list)
    owner_edited: bool = False  # the owner changed it: a later draft never overwrites it
    removed: bool = False  # a later draft no longer found it; the owner confirms deletion
    first_page: int | None = None  # the pages the model gave (None for a migrated row)
    last_page: int | None = None


class Lecture(_Strict):
    file: str = ""  # the cached lecture's file name, e.g. "Lecture_9.1-MapReduce_V.pdf"
    source_hash: str = ""  # coverage.lecture_hash of the text drafted from; "" if never
    drafted: str = ""  # the date of the last draft merged in
    model: str = ""  # "profile: model" of that draft
    title: str = ""  # the owner's heading for it ("L8.1 Finding Similar Items I")
    notes: str = ""  # free text above its table
    points: list[Point] = Field(default_factory=list)
    uncovered: list[int] = Field(default_factory=list)  # pages no point covers

    @property
    def live(self) -> list[Point]:
        return [p for p in self.points if not p.removed]


class Store(_Strict):
    version: int = VERSION
    course_notes: str = ""  # free text before the first lecture (a legend, say)
    lectures: dict[str, Lecture] = Field(default_factory=dict)  # in course order
    appendix: str = ""  # text after the last lecture (a hand-written file's labs table, say)


def point_id(lecture: str, slides: str, point: str) -> str:
    """A point's id: from what it is, never from where it stands in the list."""
    text = json.dumps([lecture, " ".join(slides.split()), " ".join(point.split())])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


# --- files -------------------------------------------------------------------------------------


def store_path(workspace: Path) -> Path:
    return workspace / STORE_FILE


def load(workspace: Path) -> Store | None:
    """The course's data, or None when it has none yet (coverage.md alone, or nothing)."""
    try:
        return Store.model_validate_json(store_path(workspace).read_text(encoding="utf-8"))
    except OSError:
        return None


def save(workspace: Path, store: Store) -> None:
    """Write coverage.json and render coverage.md from it, both atomically enough for a
    file: the whole text at once. The rendered file is a view: nothing reads edits to it."""
    store_path(workspace).write_text(store.model_dump_json(indent=1) + "\n", encoding="utf-8")
    (workspace / COVERAGE_FILE).write_text(render(store), encoding="utf-8")


def planner_text(workspace: Path) -> str:
    """The coverage table the plan prompt receives: rendered from coverage.json when the
    course has one (the same data, the same bytes), else coverage.md as it is (a course not
    migrated, or written by hand), else "(none)". The prompt's own text does not change."""
    store = load(workspace)
    if store is not None:
        return render(store)
    path = workspace / COVERAGE_FILE
    return path.read_text(encoding="utf-8") if path.is_file() else "(none)"


def missing_lectures(store: Store, lectures: list[str]) -> list[str]:
    """The lectures among `lectures` with no live point in the data, in their order."""
    return [name for name in lectures
            if name not in store.lectures or not store.lectures[name].live]


def rendered_is_stale(workspace: Path) -> bool:
    """Whether coverage.md differs from what the data renders (edited by hand after a save:
    those edits do not flow back, and the page says so)."""
    store = load(workspace)
    path = workspace / COVERAGE_FILE
    if store is None or not path.is_file():
        return False
    return path.read_text(encoding="utf-8") != render(store)


# --- rendering ---------------------------------------------------------------------------------


def _cell(text: str) -> str:
    """Text a table cell can hold: one line, and no bare | to end the cell early."""
    return " ".join(text.split()).replace("|", "\\|")


def status_cell(p: Point) -> str:
    """The status column as coverage.md always wrote it: the covering exercises when there
    are any ("ex:a, ex:b (batch)"), else the status with its check in square brackets and
    its note in round ones."""
    if p.covered_by:
        parts, group, batch = [], [], None
        for c in p.covered_by:
            if group and c.batch != batch:
                parts.append(", ".join(group) + (f" ({batch})" if batch else ""))
                group = []
            group.append(c.mark)
            batch = c.batch
        parts.append(", ".join(group) + (f" ({batch})" if batch else ""))
        return ", ".join(parts)
    text = p.status
    if p.check:
        text += f" [{p.check}]"
    if p.note:
        text += f" ({p.note})"
    return text


def pages_text(numbers: list[int]) -> str:
    """[1, 2, 3, 7] -> "p.1-3, p.7"."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return ", ".join(f"p.{r[0]}" if len(r) == 1 else f"p.{r[0]}-{r[-1]}" for r in runs)


HEAD = """# Knowledge-point coverage

Rendered from coverage.json: edit on the course's Coverage page (or in coverage.json), not
here, as this file is written again on every save. What each lecture teaches and where it is
practised; the plan picks exercises from the `gap-code` rows of the lectures it is given.
Slide numbers are the footers printed on the slides (p.N where a page has none).

Status:
- `gap-code [what an exercise could check]`: not covered yet; a code exercise fits
- `gap-tracing` / `gap-design`: not covered yet; tracing by hand or design in words
- `concept`: short-answer material, revised outside this system
- `n/a`: course admin, or nothing to practise
- `ex:<id> (batch)`: an exercise exists; a batch marks the rows its exercises cover
"""


_HEAD_BODY = HEAD.split("\n", 1)[1].strip()  # the legend, after the title line


def render(store: Store) -> str:
    """coverage.md from the data, the same bytes for the same data. Removed points are
    listed under the table, not in it, so a plan does not pick them."""
    lines = [HEAD]
    if store.course_notes.strip():
        lines += [store.course_notes.strip(), ""]
    for name, lecture in store.lectures.items():
        lines += [f"## {name}", ""]
        if lecture.title:
            lines += [f"**{lecture.title}**", ""]
        if lecture.notes.strip():
            lines += [lecture.notes.strip(), ""]
        lines += ["| Slides | Point | Status |", "|---|---|---|"]
        lines += [f"| {_cell(p.slides)} | {_cell(p.point)} | {_cell(status_cell(p))} |"
                  for p in lecture.live]
        if lecture.uncovered:
            lines += ["", f"Pages no point covers: {pages_text(lecture.uncovered)}."]
        removed = [p for p in lecture.points if p.removed]
        if removed:
            lines += ["", "No longer found in the lecture (confirm on the Coverage page): "
                      + "; ".join(f"{p.point} ({p.slides})" for p in removed) + "."]
        lines.append("")
    if store.appendix.strip():
        lines += [store.appendix.strip(), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


# --- reading coverage.md ----------------------------------------------------------------------


def parse_status(cell: str) -> dict:
    """The fields of a status cell. Covering marks first: "ex:a, ex:b (p3-w8)" is two
    exercises of that batch, and the gap kind is not in the cell, so the status stays the
    default. Else the status word, its check in square brackets, and a note in round ones;
    a cell that starts with none of the five statuses keeps its whole text as the note."""
    text = cell.strip()
    marks = _MARK.findall(text)
    if marks:
        batch = _BATCH.search(text)
        return {"covered_by": [Covered(mark=f"{kind}:{ident}", batch=batch.group(1).strip()
                                       if batch else "") for kind, ident in marks]}
    status = next((s for s in STATUSES if text == s or text.startswith(s + " ")
                   or text.startswith(s + "[") or text.startswith(s + "(")), None)
    if status is None:
        return {"status": "n/a" if text.startswith("n/a") else "concept", "note": text}
    rest = text[len(status):].strip()
    check = _CHECK.search(rest)
    if check:
        rest = (rest[:check.start()] + rest[check.end():]).strip()
    note, leftover = _trailing_group(rest)
    return {"status": status, "check": check.group(1).strip() if check else "",
            "note": " ".join(filter(None, [note, leftover]))}


def _trailing_group(text: str) -> tuple[str, str]:
    """A note in round brackets at the end of a cell, brackets inside it included, and
    what stands before it: "x (a (b))" -> ("a (b)", "x"). No group: ("", text)."""
    if not text.endswith(")"):
        return "", text
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(text[i], 0)
        if depth == 0:
            return text[i + 1:-1].strip(), text[:i].strip()
    return "", text


def _lecture_for(heading: str, known: dict[str, str]) -> str | None:
    """Which lecture a heading names: the lecture's name itself, its cache file name (a
    draft's heading, "Lecture_9.1-MapReduce_V.pdf"), or a heading carrying its number
    ("L9.1 MapReduce V"); None for any other heading ("Week 9", the file's title)."""
    text = heading.strip().strip("`")
    if text in known:
        return text
    for name, file in known.items():
        if text == file:
            return name
    for suffix in cache.LECTURE_SUFFIXES:
        if text.endswith(suffix) and text.removesuffix(suffix) in known:
            return text.removesuffix(suffix)
    number = lecture_number(text)
    if number:
        matches = [name for name in known if lecture_number(name) == number]
        if len(matches) == 1:
            return matches[0]
    return None


def _drafts_headings(text: str, known: dict[str, str]) -> bool:
    """Whether the file names its lectures the way a draft writes them (a lecture's name
    or cache file name as a heading). A file that does not was written by hand, and its
    rows are the owner's: kept by every later draft (owner_edited)."""
    names = set(known) | set(known.values())
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading and heading.group(2).strip().strip("`") in names:
            return True
    return False


def from_markdown(text: str, known: dict[str, str]) -> tuple[Store, list[str]]:
    """coverage.md read into data, and a report of what needed a decision. `known`: the
    course's lectures, name -> cache file name, which headings are mapped to. A heading
    that names no lecture starts no lecture: the text under it waits for the next lecture
    heading (a hand-written "## Week 8" note goes to the week's first lecture). Table rows
    become points; a row that is not three cells, and any other text, is kept as notes."""
    store = Store()
    report: list[str] = []
    hand_kept = not _drafts_headings(text, known)
    current: str | None = None
    pending: list[str] = []  # free text not yet given to a lecture
    unknown_headings: list[str] = []

    def flush_pending(into: Lecture | None) -> None:
        body = "\n".join(pending).strip()
        pending.clear()
        if into is None and body.startswith(_HEAD_BODY):
            body = body[len(_HEAD_BODY):].strip()  # a rendered file's own legend: not notes
        if not body:
            return
        if into is None:
            store.course_notes = (store.course_notes + "\n\n" + body).strip()
        else:
            into.notes = (into.notes + "\n\n" + body).strip()

    for raw in text.splitlines():
        line = raw.rstrip()
        heading = _HEADING.match(line)
        if heading:
            level, title = heading.groups()
            if level == "#" and not store.lectures and current is None:
                continue  # the file's title
            name = _lecture_for(title, known)
            if name is None:
                # Not a lecture: it closes the lecture before it (its rows are not that
                # lecture's). A week heading is structure the data does without; any other
                # ("Labs") keeps its line, so its text reads the same where it lands.
                unknown_headings.append(title)
                if current is not None:
                    flush_pending(store.lectures[current])
                    current = None
                if not title.lower().startswith("week"):
                    pending.append(line)
                continue
            # Text under a lecture stays with it; text under a week heading belongs to the
            # week's first lecture, which comes next; text before any lecture is the
            # course's (the legend).
            if current is not None:
                flush_pending(store.lectures[current])
            opened = bool(store.lectures)  # a lecture came before this heading
            lecture = store.lectures.setdefault(name, Lecture(file=known.get(name, "")))
            flush_pending(lecture if opened else None)
            if title != name and title != lecture.file and not lecture.title:
                lecture.title = title
            current = name
            continue
        stripped = line.strip()
        if current is not None and stripped.startswith("|"):
            cells = [c.strip() for c in _CELL.split(stripped.strip("|"))]
            if len(cells) == 3 and cells[0].lower() == "slides" and cells[1].lower() == "point":
                continue  # the header row
            if len(cells) == 3 and all(set(c) <= set("-: ") for c in cells):
                continue  # the separator row
            if len(cells) != 3:
                pending.append(line)
                report.append(f"{current}: a row with {len(cells)} cell(s) kept as a note: "
                              f"{stripped[:80]}")
                continue
            slides, point, status = (c.replace("\\|", "|") for c in cells)
            lecture = store.lectures[current]
            lecture.points.append(Point(id=point_id(current, slides, point), slides=slides,
                                        point=point, owner_edited=hand_kept,
                                        **parse_status(status)))
            continue
        bold = re.fullmatch(r"\*\*(.+)\*\*", stripped) if current is not None else None
        if bold and not store.lectures[current].title and not store.lectures[current].points:
            store.lectures[current].title = bold.group(1).strip()  # as render() writes it
            continue
        uncovered = _UNCOVERED.match(stripped) if current is not None else None
        if uncovered:
            pages: list[int] = []
            for first, last in _PAGE_RUN.findall(uncovered.group(1)):
                pages += range(int(first), int(last or first) + 1)
            store.lectures[current].uncovered = pages
            continue
        if stripped or pending:
            pending.append(line)
    if current is not None:
        flush_pending(store.lectures[current])
    elif store.lectures:
        store.appendix = "\n".join(pending).strip()  # after the last lecture
        pending.clear()
    else:
        flush_pending(None)
    for name, lecture in store.lectures.items():
        covered = sum(1 for p in lecture.points if p.covered_by)
        if covered:
            report.append(f"{name}: {covered} covered row(s) had no gap kind in the file; "
                          "recorded as gap-code")
    if hand_kept and store.lectures:
        report.append("a hand-written file: its rows are kept as the owner's by every draft")
    if unknown_headings:
        report.append("headings that name no lecture (their text went to the next lecture's "
                      "notes, or to the end of the file): " + ", ".join(unknown_headings))
    for name in store.lectures:
        if name not in known:
            report.append(f"{name}: not a cached lecture of this course")
    return store, report


# --- drafts and batches ------------------------------------------------------------------------


def merge_draft(name: str, lecture: Lecture, drafted: list[Point], source_hash: str,
                model: str, uncovered: list[int], today: str | None = None,
                replace: bool = False) -> dict[str, int]:
    """A new draft of one lecture taken into its data, in place. By id: a point the draft
    has again is kept (the owner's fields untouched; its pages filled in); a point the
    owner edited is kept whatever the draft says; a new point is added where the draft
    puts it. Any other point the draft no longer has: marked removed, for the owner to
    confirm (the incremental draft), or with `replace` (a whole lecture drafted again on
    purpose, owner 2026-11-10) dropped, as it was the model's own earlier output, except
    a practised one, whose `covered_by` marks move to the one new point at the same slides
    when there is one, and which stays otherwise. Returns the counts, for the report."""
    by_id = {p.id: p for p in lecture.points}
    kept = added = removed = 0
    seen: set[str] = set()
    merged: list[Point] = []
    for new in drafted:
        new = new.model_copy(update={"id": point_id(name, new.slides, new.point)})
        seen.add(new.id)
        if new.id in by_id:
            old = by_id[new.id]
            old.first_page, old.last_page = new.first_page, new.last_page
            old.removed = False
            merged.append(old)
            kept += 1
        else:
            merged.append(new)
            added += 1
    for old in lecture.points:
        if old.id in seen:
            continue
        if old.owner_edited:
            merged.append(old)  # the owner's: never dropped by a draft
            kept += 1
        elif old.covered_by and replace:
            same = [p for p in merged if p.id not in by_id
                    and " ".join(p.slides.split()) == " ".join(old.slides.split())]
            if len(same) == 1 and not same[0].covered_by:
                same[0].covered_by = list(old.covered_by)  # the marks move to the new row
                removed += 1
            else:
                merged.append(old)
                kept += 1
        elif old.covered_by or not replace:
            if not old.covered_by:
                old.removed = True  # for the owner to confirm
            merged.append(old)
            kept += bool(old.covered_by)
            removed += not old.covered_by
        else:
            removed += 1  # the model's own earlier row, replaced
    lecture.points = merged
    lecture.source_hash, lecture.model = source_hash, model
    lecture.drafted = today or date.today().isoformat()
    lecture.uncovered = uncovered
    return {"kept": kept, "added": added, "removed": removed}


def mark_covered(store: Store, lecture: str, slides: str, mark: str, batch: str) -> bool:
    """Record that an exercise practises the one point of `lecture` at `slides`; False
    when no point or more than one matches (the batch then proposes it instead), or when
    the mark is there already."""
    found = store.lectures.get(lecture)
    if found is None:
        return False
    matches = [p for p in found.live if " ".join(p.slides.split()) == " ".join(slides.split())]
    if len(matches) != 1:
        return False
    if any(c.mark == mark for c in matches[0].covered_by):
        return True
    matches[0].covered_by.append(Covered(mark=mark, batch=batch))
    return True


def known_lectures(workspace: Path) -> dict[str, str]:
    """The course's cached lectures, name -> cache file name, in course order."""
    from tutor.materials.requires import lecture_files

    return {cache.lecture_name(p) or p.name: p.name.removesuffix(".md")
            for p in lecture_files(workspace)}


# --- command line ------------------------------------------------------------------------------


def migrate(workspace: Path, out=print) -> int:
    """coverage.md -> coverage.json, once: refused when the data exists; the file before is
    kept as coverage.md.bak (coverage.md.bak-<date> if that exists)."""
    if store_path(workspace).is_file():
        out(f"{STORE_FILE} exists already; nothing done")
        return 1
    path = workspace / COVERAGE_FILE
    if not path.is_file():
        out(f"no {COVERAGE_FILE} to migrate: draft one (python -m tutor.materials.coverage)")
        return 1
    store, report = from_markdown(path.read_text(encoding="utf-8"), known_lectures(workspace))
    # The file's rows describe the lectures as they are now: a lecture it covers counts as
    # drafted from the current text, so the next draft asks only for the lectures it lacks
    # (and, later, for a lecture whose text changes); `redraft --all` asks for every one.
    from tutor.materials import coverage
    from tutor.materials.requires import lecture_files

    for file in lecture_files(workspace):
        name = cache.lecture_name(file) or file.name
        if name in store.lectures:
            store.lectures[name].source_hash = coverage.lecture_hash(cache.load(file))
    backup = workspace / f"{COVERAGE_FILE}.bak"
    if backup.exists():
        backup = workspace / f"{COVERAGE_FILE}.bak-{date.today().isoformat()}"
    shutil.copy2(path, backup)
    save(workspace, store)
    points = sum(len(lecture.points) for lecture in store.lectures.values())
    out(f"{STORE_FILE}: {len(store.lectures)} lectures, {points} points; the file before is "
        f"{backup.name}")
    for line in report:
        out(f"  {line}")
    return 0


def redraft(workspace: Path, profile: str | None, every: bool, out=print) -> int:
    """Ask the model for the lectures not drafted yet or whose text changed (`every`: all
    of them), merging each answer in as it comes. Uses the drafting module's prompt, stored
    answers (a lecture asked before with the same text is not asked again unless `every`)
    and checks."""
    from tutor.exchange import AwaitingAnswer
    from tutor.llm import LLMError
    from tutor.materials import coverage
    from tutor.materials.requires import lecture_files, model

    store = load(workspace) or Store()
    try:
        config = model(workspace, profile)
    except LLMError as e:
        out(f"setup: {e}")
        return 1
    for path in lecture_files(workspace):
        name = cache.lecture_name(path) or path.name
        entries = cache.load(path)
        digest = coverage.lecture_hash(entries)
        lecture = store.lectures.setdefault(name, Lecture(file=path.name.removesuffix(".md")))
        if lecture.source_hash == digest and not every:
            out(f"  {name}: unchanged since its draft")
            continue
        data = None if every else coverage.stored(workspace, path, entries)
        if data is None:
            try:
                data = coverage.ask(config, workspace, path, entries)
            except AwaitingAnswer:
                out(f"  {name}: waiting for the assistant")
                continue
            except LLMError as e:
                out(f"model: {e}")
                return 1
            except ValueError as e:
                out(f"  {name}: {e}; not stored")
                continue
            coverage.result_path(workspace, path).parent.mkdir(parents=True, exist_ok=True)
            coverage.result_path(workspace, path).write_text(
                json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        section = coverage.section(name, [coverage.Point.model_validate(p)
                                          for p in data.get("points", [])], entries)
        drafted = [Point(id="", slides=row.slides, point=row.point, **parse_status(row.status),
                         first_page=row.first_page, last_page=row.last_page)
                   for row in section.rows]
        counts = merge_draft(name, lecture, drafted, digest, str(data.get("model", "")),
                             section.uncovered, replace=every)
        save(workspace, store)
        out(f"  {name}: kept {counts['kept']}, added {counts['added']}, "
            f"removed {counts['removed']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from tutor.config import WorkspaceError, load_env, load_workspace

    parser = argparse.ArgumentParser(prog="python -m tutor.materials.coverage_store",
                                     description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="coverage.md -> coverage.json, once")
    sub.add_parser("render", help="coverage.json -> coverage.md")
    ask = sub.add_parser("redraft", help="ask the model for new or changed lectures")
    ask.add_argument("--all", action="store_true", help="every lecture, changed or not")
    ask.add_argument("--profile", help="the model to ask (default: the setup role's)")
    args = parser.parse_args(argv)
    load_env()
    try:
        workspace = load_workspace().root
    except WorkspaceError as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    if args.command == "migrate":
        return migrate(workspace)
    if args.command == "render":
        store = load(workspace)
        if store is None:
            print(f"no {STORE_FILE}: migrate first", file=sys.stderr)
            return 1
        save(workspace, store)
        print(f"{COVERAGE_FILE} rendered from {STORE_FILE}")
        return 0
    return redraft(workspace, args.profile, args.all)


if __name__ == "__main__":
    sys.exit(main())
