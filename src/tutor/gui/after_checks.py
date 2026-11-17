"""The checks after generation, for the batch page: the judge of teaching quality and the
held-out check (docs/design/validation.md, "Independent checks"; docs/design/gui.md).

Both exist as commands (`python -m tutor.judge`, `tutor.heldout_writer`,
`tutor.validation.heldout`); the page starts them as tasks and shows each exercise's verdict.
They only report: validation.json and VALID never change. Nothing here calls a model or
reads a report's reasons, which stay in the task's log.
"""

from __future__ import annotations

from dataclasses import dataclass

from tutor.gui import courses, llm_settings, ratings, tasks
from tutor.gui.courses import Course

# The tasks' kinds (task.json's meta), by which a finished task finds its way back.
JUDGE, WRITE, CHECK = "judge", "heldout-write", "heldout-check"
# The judge's four points in the order of its letters (judge/teaching.POINTS; named here so
# a page needs no import of the judge).
POINTS = ("clear", "fits", "taught", "targets")


@dataclass(frozen=True)
class Verdicts:
    """One exercise's row in the batch table's two columns."""

    quality: str | None  # the judge's letters, e.g. "yyny"; None when it was not judged
    rated: bool  # the owner has rated the exercise: only then are the letters shown
    held_out: int | None  # checks the held-out run flagged; None when it has not run

    @property
    def failed_points(self) -> list[str]:
        """The points the judge answered n, by name ("clear"): still a verdict, no reason."""
        # strict=False: no letters when it was not judged (quality_letters gives four or None).
        return [name for name, letter in zip(POINTS, self.quality or "", strict=False)
                if letter == "n"]


def role_model(course: Course, role: str) -> str | None:
    """The model [llm.roles] names for `role`, as "profile (model)", or None when the
    course names none: the page then offers no button, since the GUI does not read .env and
    cannot tell whether a fallback model exists (docs/design/gui-settings.md). Never a key."""
    llm = llm_settings.section(course)
    name = llm.get("roles", {}).get(role)
    if not isinstance(name, str) or not name:
        return None
    profile = llm.get("profiles", {}).get(name)
    model = profile.get("model") if isinstance(profile, dict) else None
    return f"{name} ({model})" if model else name


def verdicts(course: Course, batch: str, exercises: list[str]) -> dict[str, Verdicts]:
    """Each exercise's verdicts. The judge's letters come with `rated`: the page shows them
    only for an exercise the owner has rated, because the owner's ratings are what the judge
    is checked against (judge/calibrate.py), and a rating given after reading the judge's
    would only agree with it."""
    folder = courses.batch_dir(course, batch)
    if folder is None:
        return {}
    root = course.workspace.root
    return {name: Verdicts(courses.quality_letters(folder, name),
                           ratings.find(root, batch, name) is not None,
                           courses.heldout_flagged(folder, name))
            for name in exercises}


def last_task(course: Course, batch: str, kind: str) -> str | None:
    """The id of the newest task of `kind` on this batch, so the page can link to its log,
    where the reasons are; None when there was none."""
    for task in tasks.list_tasks(course.workspace.root):  # newest first
        if task.meta.get("kind") == kind and task.meta.get("batch") == batch:
            return task.id
    return None


def counts(found: dict[str, Verdicts]) -> dict[str, int]:
    """What the batch has on file, for the line under each button (owner, 2026-11-17: a
    click's effect was not plain to see): how many exercises were judged and how many of
    those verdicts are shown; how many the held-out check ran on and what it flagged."""
    rows = list(found.values())
    return {"exercises": len(rows),
            "judged": sum(r.quality is not None for r in rows),
            "judged_shown": sum(r.quality is not None and r.rated for r in rows),
            "checked": sum(r.held_out is not None for r in rows),
            "flagged": sum(r.held_out or 0 for r in rows)}
