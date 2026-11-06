"""A course's Coverage page: its coverage.md, and a model's draft of one from the lectures
(tutor.materials.coverage; owner, 2026-11-06).

The draft is never written over coverage.md. "Use as coverage.md" creates the file only when
there is none (the open fails rather than replace). For a course that has one, the owner
asked for adding to it as lectures come (2026-11-06): the sections of the lectures it lacks
are appended at its end, with a line saying where they came from; nothing already in it
changes (checked: the old text must be the start of the new), and the file before is kept as
coverage.md.bak. Which lectures it lacks is read from the headings a draft writes, the
lecture's file name; a file written by hand that names its lectures its own way cannot be
read so, and then nothing is ticked for the owner, who ticks the ones it lacks.
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass

from markupsafe import Markup

from tutor.gui import rendering
from tutor.gui.courses import Course
from tutor.gui.environment import setup_model
from tutor.materials import coverage as coverage_draft

COVERAGE_FILE = coverage_draft.COVERAGE_FILE
DRAFT_FILE = coverage_draft.DRAFT_FILE


@dataclass(frozen=True)
class CoverageView:
    exists: bool  # coverage.md is there
    html: Markup | None  # coverage.md rendered, raw HTML off (rendering.py)
    draft: coverage_draft.Draft
    draft_file: bool  # coverage-draft.md is there, to open in VS Code
    model: str | None  # the model a draft run would ask; None: no model
    tokens_k: int  # about how many thousand input tokens the lectures to do would send


def view(course: Course) -> CoverageView:
    root = course.workspace.root
    path = root / coverage_draft.COVERAGE_FILE
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    found = coverage_draft.draft(root)
    tokens = sum(chars for _, chars in found.todo) // 4  # about 4 characters a token
    return CoverageView(text is not None,
                        rendering.question_html(text) if text is not None else None,
                        found, (root / coverage_draft.DRAFT_FILE).is_file(),
                        setup_model(course), round(tokens / 1000))


def draft_command() -> list[str]:
    return [sys.executable, "-m", "tutor.materials.coverage"]


def use_draft(course: Course) -> list[str]:
    """Write the draft as coverage.md; refused when coverage.md exists already."""
    root = course.workspace.root
    found = coverage_draft.draft(root)
    if not found.sections:
        return ["There is no draft yet: draft it from the lectures first."]
    try:
        with (root / coverage_draft.COVERAGE_FILE).open("x", encoding="utf-8") as f:
            f.write(coverage_draft.render(found, final=True))
    except FileExistsError:
        return ["coverage.md exists already, and it is never written over: add the lectures "
                "it lacks below."]
    return []


@dataclass(frozen=True)
class Missing:
    lecture: str
    ticked: bool  # ticked for the owner: the file names its lectures as drafts do


def missing(course: Course, found: CoverageView) -> list[Missing]:
    """The drafted lectures coverage.md has no section for; [] without a coverage.md."""
    path = course.workspace.root / COVERAGE_FILE
    if not found.exists:
        return []
    text = path.read_text(encoding="utf-8")
    drafted = [s.lecture for s in found.draft.sections]
    named = [name for name in drafted if coverage_draft.has_section(text, name)]
    # No lecture named the drafts' way: a hand-made file, whose own names cannot be matched.
    return [Missing(name, bool(named)) for name in drafted if name not in named]


def add_sections(course: Course, lectures: list[str]) -> list[str]:
    """Append the chosen lectures' drafted sections to coverage.md, changing nothing in it."""
    root = course.workspace.root
    path = root / COVERAGE_FILE
    if not path.is_file():
        return ["There is no coverage.md yet: use the draft as coverage.md."]
    found = coverage_draft.draft(root)
    chosen = [s for s in found.sections if s.lecture in lectures]
    if not chosen:
        return ["Tick the lectures to add first."]
    old = path.read_text(encoding="utf-8")
    note = (f"Added from the draft by {', '.join(found.models) or 'a model'}: check each row "
            "against the slides.")
    lines = [line for s in chosen for line in coverage_draft.section_lines(s, note)]
    new = old.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    if not new.startswith(old.rstrip("\n")):  # append only, never an edit
        return ["coverage.md could not be added to safely, so it was left as it was."]
    path.with_name(COVERAGE_FILE + ".bak").write_text(old, encoding="utf-8")
    tmp = path.with_name(f".{COVERAGE_FILE}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return []
