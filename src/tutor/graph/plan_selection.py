"""Selection identity and read-only checkpoint access, independent of model clients.

The full plan describes proposed work; selected_ids freezes the authorized subset.
Keep identity checks separate from item schemas so an invalid deferred row cannot
block unrelated work, while duplicate IDs still make the whole selection ambiguous.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path


def choose(plan: object, requested: list[str] | None = None) -> list[str]:
    """Validate every row's identity, then return a nonempty subset in plan order.

    None is the legacy CLI's all-items approval; an explicit empty list is an error.
    IDs are also directory names, so malformed identities are rejected even if deferred.
    """
    if not isinstance(plan, list) or not plan:
        raise ValueError("The plan must be a nonempty list of items.")
    ids = []
    for row in plan:
        ident = row.get("id") if isinstance(row, dict) else None
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", ident):
            raise ValueError("Every plan item needs an identifiable lowercase, hyphenated ID.")
        if ident in ids:
            raise ValueError(f"Plan id {ident!r} is used twice; edit the plan or send it back.")
        ids.append(ident)
    wanted = ids if requested is None else requested
    if not isinstance(wanted, list) or not all(isinstance(ident, str) for ident in wanted):
        raise ValueError("Selected IDs must be a list of text IDs.")
    if not wanted:
        raise ValueError("Select at least one item to generate.")
    if len(set(wanted)) != len(wanted):
        raise ValueError("Selected IDs must not be repeated.")
    if unknown := set(wanted) - set(ids):
        raise ValueError("Unknown selected IDs: " + ", ".join(sorted(unknown)))
    return [ident for ident in ids if ident in wanted]


def selected_rows(state: dict) -> list[dict]:
    """Legacy checkpoints lack selected_ids and therefore authorize their entire plan."""
    ids = choose(state["plan"], state.get("selected_ids"))
    return [row for row in state["plan"] if row["id"] in ids]


def validate_selection(plan, requested, lectures, existing, profile) -> list[str]:
    """Recheck only selected item schemas and deterministic blocking rules at approval."""
    # Import locally: batch's apply_decision uses this helper too.
    from tutor.graph.batch import blocking_problems

    ids = choose(plan, requested)
    items = profile.batch_plan.model_validate(
        {"items": [row for row in plan if row["id"] in ids]}).items
    if found := blocking_problems(items, lectures, existing):
        raise ValueError("The selected plan cannot run: " + "; ".join(found))
    return ids


def digest(data: bytes) -> str:
    """Fingerprint the exact reviewed bytes, including whitespace and complete notes."""
    return hashlib.sha256(data).hexdigest()


def checkpoint_values(draft: Path) -> dict:
    """Read the root checkpoint without clients, environment loading or database writes.

    Missing/corrupt/busy stores are not evidence of legacy all-items approval. Surface
    the error so a page can offer Refresh or dependency repair instead of guessing.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise ValueError('Reading plan approval needs the gen extra: install ".[gen]", '
                         "then refresh this page.") from exc
    path = draft / "checkpoint.sqlite"
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2,
                                     check_same_thread=False)) as connection:
            connection.execute("PRAGMA query_only=ON")
            saved = SqliteSaver(connection).get_tuple(
                {"configurable": {"thread_id": draft.name, "checkpoint_ns": ""}})
            if saved is None:
                raise ValueError("The plan checkpoint is not ready. "
                                 "Refresh after planning finishes.")
            values = saved.checkpoint["channel_values"]
            if not isinstance(values, dict) or not isinstance(values.get("approved", False), bool):
                raise TypeError("Malformed checkpoint state")
            if ("generation_complete" in values
                    and not isinstance(values["generation_complete"], bool)):
                raise TypeError("Malformed checkpoint completion")
            if values.get("approved"):
                choose(values["plan"], values.get("selected_ids"))
            return values
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Cannot read the plan checkpoint. Refresh after the running task "
                         "finishes; if it persists, continue this batch in a terminal "
                         "to inspect its checkpoint.") from exc
