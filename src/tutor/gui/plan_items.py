"""What each exercise of an approved plan came to, shown next to it on the plan page, and
trying one that did not come out again (owner, 2026-11-06), with a note for that try
(owner, 2026-11-07).

A status is read from files only: the batch's manifest and each exercise's validation.json
(saved: valid, invalid or not validated yet), else generation.jsonl's last line for it
(discarded or failed), else it is not generated yet. Trying again runs the graph's own `item`
command with what the batch was planned with (run.json), then validates the exercise; the
plan's state and commands are generation.py's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from tutor.gui import generation
from tutor.gui.courses import Course, valid_verdict
from tutor.gui.generation import ASSISTANT, CONTINUING, GENERATED, GENERATING, STOPPED, PlanView

# What each exercise of an approved plan came to, shown next to it on the plan page.
ITEM_VALID, ITEM_INVALID, ITEM_SAVED = "valid", "invalid", "saved, not validated"
ITEM_DISCARDED, ITEM_ERROR, ITEM_PENDING = "discarded", "error", "not generated yet"
ITEM_LEFT_OUT = "Left out by you"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")  # a plan id, safe as a file name
DETAIL_CHARS = 1500  # of the last round's failure shown, enough for pytest's first assertion
NOTE_PREFIX = "The owner adds, for this try: "  # how a retry's note joins the item's notes


@dataclass(frozen=True)
class ItemStatus:
    state: str
    reason: str = ""  # why it did not succeed: the rule that failed, or the error
    # The last round's failure as the check printed it, so the owner can write a note for
    # the next try; "" when the batch hides answers, as it can quote expected values.
    detail: str = ""
    note: str = ""  # the note the last "Try again with a note" used, to start the next from

    @property
    def can_retry(self) -> bool:
        return self.state in (ITEM_DISCARDED, ITEM_ERROR)

    @property
    def can_generate(self) -> bool:
        return self.state == ITEM_LEFT_OUT and not self.reason


def item_statuses(course: Course, view: PlanView) -> dict[str, ItemStatus]:
    """id -> what the exercise came to, once the plan is approved: saved (with its
    validation.json verdict), discarded or failed with an error (generation.jsonl's last line
    for it, so a later try counts), or not generated yet. {} before approval."""
    if view.batch is None or view.state not in (GENERATING, CONTINUING, STOPPED, ASSISTANT,
                                                GENERATED):
        return {}
    batch_dir = course.workspace.root / "batches" / view.batch
    try:
        saved = set(json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
                    .get("exercises", []))
    except (OSError, ValueError, AttributeError):
        saved = set()
    outcomes: dict[str, dict] = {}
    try:
        for line in (batch_dir / "generation.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("exercise"), str):
                outcomes[data["exercise"]] = data  # the last line wins
    except OSError:
        pass
    statuses = {}
    for item in view.items:
        exercise = item.get("id", "")
        if exercise in saved:
            valid = valid_verdict(batch_dir, exercise)
            statuses[exercise] = ItemStatus(ITEM_SAVED if valid is None
                                            else ITEM_VALID if valid else ITEM_INVALID)
        elif exercise in outcomes and outcomes[exercise].get("outcome") == "saved":
            # Saved, but the manifest is written only by `collect`, once every item has
            # finished: until then (or for good, if the run stopped) it is not in it. Shown as
            # discarded before, with "Try again" on a stopped run, which would pay for it twice.
            statuses[exercise] = ItemStatus(ITEM_SAVED)
        elif exercise in outcomes and outcomes[exercise].get("outcome") == "error":
            error = str(outcomes[exercise].get("error", ""))
            statuses[exercise] = ItemStatus(ITEM_ERROR, error[:200],
                                            note=last_note(view, exercise))
        elif exercise in outcomes:
            detail = "" if view.hides_answers else _failure_text(outcomes[exercise])
            statuses[exercise] = ItemStatus(ITEM_DISCARDED, _failure(outcomes[exercise]),
                                            detail[:DETAIL_CHARS], last_note(view, exercise))
        elif view.selected_ids is not None and exercise not in view.selected_ids:
            from tutor.gui.plan_selection import deferred_problem

            statuses[exercise] = ItemStatus(ITEM_LEFT_OUT,
                                            deferred_problem(course, view, exercise))
        else:
            statuses[exercise] = ItemStatus(ITEM_PENDING)
    return statuses


def _failure_text(line: dict) -> str:
    """The last round's failure as generation.jsonl keeps it (the check and its output)."""
    attempts = [a for a in line.get("attempts", []) if isinstance(a, dict)]
    return str(attempts[-1].get("failure") or "") if attempts else ""


def _failure(line: dict) -> str:
    """Which check the last round failed, and after how many rounds, in a line; the check's
    output, which can quote expected values, is ItemStatus.detail, shown only when asked."""
    text = _failure_text(line)
    rule = text.split(": ")[0] if text else "it did not pass the checks"
    if text.startswith("format"):
        rule = "the reply did not fit the exercise's format"
    rounds = line.get("rounds", 0)
    return f"{rule}, after {rounds} repair{'s' if rounds != 1 else ''}"


def _note_file(draft: Path, exercise: str) -> Path:
    """Where a retry's note is kept: beside the item file the retry writes."""
    return draft / f"retry-{exercise}.note.txt"


def last_note(view: PlanView, exercise: str) -> str:
    """The note the last "Try again with a note" for this exercise used ("" if none)."""
    if view.draft is None or not _ID.match(exercise):
        return ""
    try:
        return _note_file(view.draft, exercise).read_text(encoding="utf-8")
    except OSError:
        return ""


def not_succeeded(course: Course, batch: str) -> tuple[PlanView, list[str]] | None:
    """For a batch page: the plan it was generated from and its exercises that did not come
    out (discarded or failed), so the page can point to the plan's "Try again" (owner,
    2026-11-06); None when every one came out, or the batch was not made from a plan."""
    label = generation.label_of(batch)
    view = generation.plan_view(course, label) if label else None
    if view is None or view.batch != batch:
        return None
    missing = [k for k, v in item_statuses(course, view).items() if v.can_retry]
    return (view, missing) if missing else None


def prepare_retry(course: Course, view: PlanView, exercise: str,
                  python: str | None = None, note: str = "") -> list[list[str]] | None:
    """The commands that generate one exercise of the plan again and validate it: the graph's
    `item` command, which adds a saved exercise to the batch's manifest, run with what the
    batch was planned with (run.json's models and settings), so it matches the others.
    Writes the plan's item as the command reads it; None when the plan has no such item.

    A note (already checked by clean_text) is added to the item's notes for this try only:
    plan.json keeps the plan as approved. The notes are the generate prompt's last line, so
    the cached part before them (the lecture's pages, the example) is unchanged. The note is
    kept beside the item, so the page can start the next note from it."""
    if view.draft is None or view.batch is None or not _ID.match(exercise):
        return None
    try:
        # New approvals own a frozen full snapshot; later file edits cannot replace it.
        plan = (view.approved_plan if view.approved_plan is not None else
                json.loads((view.draft / "plan.json").read_text(encoding="utf-8")))
        run = json.loads((view.draft / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    item = next((i for i in plan if isinstance(i, dict) and i.get("id") == exercise), None)
    if item is None:
        return None
    if note:
        notes = str(item.get("notes") or "")
        item = {**item, "notes": (notes + "\n" if notes else "") + NOTE_PREFIX + note}
        _note_file(view.draft, exercise).write_text(note, encoding="utf-8")
    path = view.draft / f"retry-{exercise}.json"
    path.write_text(json.dumps(item, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    flags = [f"--{name.replace('_', '-')}={value}"
             for name, value in run.get("profiles", {}).items()
             if name in ("profile", "oracle_profile", "repair_profile") and value]
    for name, value in run.get("settings", {}).items():
        if value is not None:
            flags.append(f"--set={name}={str(value).lower() if isinstance(value, bool) else value}")
    batch_dir = course.workspace.root / "batches" / view.batch
    return [[*generation.graph_command(python), "item", str(path), f"--batch={view.label}", *flags],
            validate_one_command(python, batch_dir / "exercises" / exercise,
                                 batch_dir / "solutions" / exercise)]


def validate_one_command(python: str | None, exercise: Path, solution: Path) -> list[str]:
    """`python -m tutor.validation` on one exercise, which writes its validation.json."""
    return [python or generation.GRAPH[0], "-m", "tutor.validation", str(exercise), str(solution)]
