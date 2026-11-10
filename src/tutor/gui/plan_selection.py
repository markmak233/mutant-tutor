"""Adapt saved graph approval to a page, and validate paid selection actions.

No GET starts work or loads credentials. Frozen approved material wins over later
plan.json edits; legacy checkpoints retain the old all-items interpretation.
"""

from __future__ import annotations

import json
from dataclasses import replace

from tutor.graph.plan_selection import checkpoint_values, choose, digest, validate_selection
from tutor.gui.i18n import gettext_


def adapt(view):
    """Add checkpoint evidence without losing task state or legacy file evidence."""
    from tutor.gui import generation as gen

    if view.draft is None:
        return view
    try:
        state = checkpoint_values(view.draft)
    except ValueError as exc:
        return replace(view, selection_error=str(exc))
    approved = state.get("approved", False)
    manifest = (view.draft.parent.parent / view.draft.name / "manifest.json").is_file()
    changes = {"has_manifest": manifest, "source_lectures": state.get("lectures", [])}
    if approved:
        plan = state["plan"]
        changes.update(approved_plan=plan, selected_ids=state.get("selected_ids"),
                       edited=False, plan_error=None,
                       generation_complete=state.get("generation_complete", manifest))
        # Approval may be checkpointed before the first item folder/log exists.
        if not (view.task and view.task.pending):
            latest = {row["id"]: row for row in state.get("results", [])}
            if changes["generation_complete"]:
                changes["state"] = gen.GENERATED
            elif any(row.get("outcome") == "waiting" for row in latest.values()):
                changes["state"] = gen.ASSISTANT
            elif view.state != gen.ASSISTANT:
                changes["state"] = gen.STOPPED
    else:
        if not (view.draft / "plan.json").is_file():
            return replace(view, **changes)
        try:
            data = (view.draft / "plan.json").read_bytes()
            plan = json.loads(data.decode("utf-8"))
            changes["plan_digest"] = digest(data)
        except (OSError, ValueError) as exc:
            return replace(view, **changes, plan_error=f"Cannot read plan.json: {exc}")
    # Preserve complete notes, safely representing malformed fields in deferred rows.
    if isinstance(plan, list) and all(isinstance(row, dict) for row in plan):
        changes["items"] = [{k: v if isinstance(v, str) else json.dumps(v)
                             for k, v in row.items()} for row in plan]
    try:
        choose(plan, state.get("selected_ids") if approved else None)
    except ValueError as exc:
        changes["plan_error"] = str(exc)
    return replace(view, **changes)


def posted_selection(course, view, form) -> list[str]:
    """Check the page fingerprint and selected-only rules before enqueueing any task."""
    from tutor.graph.batch import existing_ids
    from tutor.graph.languages import profile_of

    if not view.can_approve or view.draft is None:
        raise ValueError("This plan is not available for approval. Refresh to see its state.")
    data = (view.draft / "plan.json").read_bytes()
    if form.get("plan_digest") != digest(data) or view.plan_digest != digest(data):
        raise ValueError("The plan changed after review. Review the full plan below again.")
    if form.get("selection_form") != "1":
        raise ValueError("The selection form is missing. Refresh and choose items again.")
    return validate_selection(json.loads(data.decode("utf-8")), form.getlist("item"),
                              view.source_lectures, existing_ids(course.workspace.root),
                              profile_of(course.workspace.root))


def deferred_problem(course, view, exercise: str) -> str:
    """A deferred row may have become blocked; surface its current fix before spending."""
    from tutor.graph.batch import existing_ids
    from tutor.graph.languages import profile_of
    from tutor.gui import generation

    try:
        validate_selection(view.approved_plan, [exercise], view.source_lectures,
                           existing_ids(course.workspace.root), profile_of(course.workspace.root))
        row = next(row for row in view.approved_plan if row["id"] == exercise)
        if row["lecture"] not in generation.lectures(course)[0]:
            return gettext_("The source is no longer available. Read it on the course page first.")
    except (ValueError, OSError, StopIteration) as exc:
        return str(exc)
    return ""


def effective_models(course, view):
    """Saved profile overrides plus current course definitions, as the item CLI resolves."""
    from tutor.gui import generation

    if view.draft is None:
        return generation.models(course)
    try:
        run = json.loads((view.draft / "run.json").read_text(encoding="utf-8"))
        return generation.models(course, run.get("profiles", {}))
    except (OSError, ValueError, AttributeError):
        return generation.models(course)
