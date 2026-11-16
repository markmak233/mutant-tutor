"""The owner's teaching-quality rating of an exercise, given on its page once it has passed
(owner, 2026-11-15; docs/design/validation.md, "Validity vs teaching quality").

The rating is the one fixed on 2026-10-28: yes or no on four points (report.RATING_POINTS:
clear, fits, taught, targets) and an optional free-text remark, one JSON line per exercise
in the course's `ratings.jsonl`, which the course page counts as done, the report sums and
the judge is calibrated against. So an exercise keeps one line: rating it again replaces its
line in place, and keys the page does not set (a hand-written `note`, `skip_points`) stay.

The page shows the stored rating back in its form (`find`), and `save` writes the file whole
or not at all, the file before kept as `ratings.jsonl.bak`, as the GUI does with
workspace.toml. This module only reads and writes that one file; courses.py stays read-only.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import threading
from collections.abc import Mapping
from pathlib import Path

from tutor.eval.report import PASSED, RATING_POINTS
from tutor.gui.i18n import N_

FILE = "ratings.jsonl"
# Each point as the form asks it, in RATING_POINTS' order, shortened from
# docs/design/validation.md's
# definitions so every rating is given against the same words (shown through _()).
POINTS = (
    ("clear", N_("Clear"), N_("Someone new to the content can learn it from the question and "
                              "the hints' \"Useful functions\", and has an idea how to start.")),
    ("fits", N_("Fits the material"), N_("It matches how the lectures and labs present the "
                                         "idea.")),
    ("taught", N_("Within what has been taught"), N_("It needs no API or idea from later "
                                                     "weeks.")),
    ("targets", N_("Targets the concept"), N_("Solving it needs the knowledge point it is "
                                              "filed under, not a workaround.")),
)
REMARK_MAX = 500  # characters: a remark is a few words ("too easy"), not an essay
# The form's answer per point; anything else is no answer, and the rating is refused.
_ANSWERS = {"y": True, "n": False}
# The server answers requests in threads: two saves at once must not both read the old file.
_lock = threading.Lock()


def can_rate(status: str, stored: dict | None) -> bool:
    """Whether the page offers the form: once a logged run has passed (report.PASSED), or
    when a rating is already stored, so one given before the log existed can be changed."""
    return status == PASSED or stored is not None


def _row(line: str) -> dict | None:
    """One line of the file as a rating, or None: a blank or broken line is no rating (it is
    still written back as it was, since it is the owner's file)."""
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def find(root: Path, batch: str, exercise: str) -> dict | None:
    """The stored rating of this exercise, as its line holds it, or None when it has none.
    If an older hand-written file has two lines for it, the first: the one `save` replaces."""
    for line in _lines(root / FILE):
        row = _row(line)
        if row is not None and (row.get("batch"), row.get("exercise")) == (batch, exercise):
            return row
    return None


def parse(form: Mapping[str, str]) -> tuple[dict[str, bool], str] | None:
    """The four points and the remark from the page's form, or None if a point has no answer:
    a half rating would read as "no" on the points left out, so it is refused instead. The
    remark's whitespace is folded into single spaces, so the line stays one line."""
    points = {}
    for point in RATING_POINTS:
        answer = _ANSWERS.get(form.get(point, ""))
        if answer is None:
            return None
        points[point] = answer
    remark = " ".join(form.get("remark", "").split())[:REMARK_MAX]
    return points, remark


def save(root: Path, batch: str, exercise: str, points: dict[str, bool], remark: str,
         today: datetime.date | None = None) -> None:
    """Write this exercise's rating: its line replaced in place, or a new line at the end.
    `date` is the day of this rating, on this machine's clock, as the lines before it.
    Raises OSError when the file cannot be written; the file is then as it was."""
    rated = {"batch": batch, "exercise": exercise,
             "date": (today or datetime.date.today()).isoformat(),
             **{point: points[point] for point in RATING_POINTS}, "remark": remark}
    path = root / FILE
    with _lock:
        lines, placed = [], False
        for line in _lines(path):
            row = _row(line)
            if row is not None and (row.get("batch"), row.get("exercise")) == (batch, exercise):
                if not placed:
                    # Merged into the old line: its other keys and their order stay.
                    lines.append(json.dumps({**row, **rated}, ensure_ascii=False))
                    placed = True
                continue  # a second line for it would count it twice in the report
            if line.strip():
                lines.append(line)
        if not placed:
            lines.append(json.dumps(rated, ensure_ascii=False))  # remarks stay readable
        if path.is_file():
            shutil.copyfile(path, path.with_name(FILE + ".bak"))
        # A temporary file beside it, then one rename: a crash leaves the old file or the
        # new one, never half of each.
        tmp = path.with_name(FILE + ".tmp")
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, path)  # on Windows this fails while another program has it open
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
