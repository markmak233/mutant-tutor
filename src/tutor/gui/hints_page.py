"""The exercise page's Hints tab (owner, 2026-11-18; docs/design/gui.md): the exercise's own
hints.md, and a hint on the learner's code from `python -m tutor.hint`, run as a task.

hints.md sits in the exercise folder beside the question, so the learner can open it
anyway; the tab shows its "Useful functions" part and keeps each hint closed until it is
opened. The hint on the code is what the command prints (a rule, a known mistake, or a
model's hint when the course names a `hint` model): nothing here reads `solutions/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markupsafe import Markup

from tutor.gui import courses, rendering, tasks
from tutor.gui.courses import Course

HINT = "hint"  # the task's kind (task.json's meta)
# A part of hints.md: "## Useful functions", "## Hint 1", ...
_PART = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
# A numbered hint, which stays closed until asked for; any other part is shown.
_NUMBERED = re.compile(r"hint\s*\d+", re.IGNORECASE)


@dataclass(frozen=True)
class Part:
    title: str  # the heading as written: generated text, shown escaped
    html: Markup  # its body, rendered as the question is (raw HTML off)
    closed: bool  # a numbered hint: behind a click


@dataclass(frozen=True)
class Given:
    """A hint the command gave on the learner's code."""

    task: tasks.Task
    text: str  # what it printed, without the command line and the exit line


def parts(course: Course, batch: str, exercise: str) -> list[Part] | None:
    """hints.md in its parts, in the file's order; None when the exercise has none. Text
    before the first "## " heading (the file's title) is left out."""
    folder = courses.exercise_dir(course, batch, exercise)
    path = folder / "hints.md" if folder else None
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    found = list(_PART.finditer(text))
    out = []
    for heading, following in zip(found, [*found[1:], None], strict=True):
        body = text[heading.end():following.start() if following else len(text)].strip()
        title = heading.group(1)
        out.append(Part(title, rendering.question_html(body), bool(_NUMBERED.fullmatch(title))))
    return out


def given(course: Course, batch: str, exercise: str) -> list[Given]:
    """The hints asked for from this page, newest first, each with what the command said."""
    out = []
    for task in tasks.list_tasks(course.workspace.root):
        meta = task.meta
        if (meta.get("kind"), meta.get("batch"), meta.get("exercise")) != (HINT, batch, exercise):
            continue
        log, _cut = tasks.tail(task)
        # The log starts with the command ("$ python -m tutor.hint ...") and ends with its
        # exit line: what is between them is what the learner would read in a terminal.
        lines = [line for line in log.splitlines()
                 if not line.startswith("$ ") and not re.fullmatch(r"\[exit -?\d+\]", line)]
        out.append(Given(task, "\n".join(lines).strip()))
    return out
