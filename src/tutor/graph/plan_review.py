"""The CLI's owner review: preserve plan edits, show full notes, then take a decision.

Separate from command setup so approval checks can grow without obscuring resume behavior.
No prompt text is defined here; these helpers operate on an already proposed plan.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tutor.graph.batch import blocking_problems, existing_ids
from tutor.graph.nodes import Deps
from tutor.graph.plan_selection import digest, validate_selection


def approval_decision(snapshot, deps: Deps, workspace: Path, flag: dict) -> dict:
    """Read reviewed bytes once; validate before any graph work or settings mutation."""
    data = (deps.drafts / "plan.json").read_bytes()
    fingerprint = digest(data)
    if flag.get("plan_digest") is not None and flag["plan_digest"] != fingerprint:
        raise ValueError("The plan changed after review. Refresh and review the full plan again.")
    plan = json.loads(data.decode("utf-8"))
    selected = validate_selection(plan, flag.get("selected_ids"), snapshot.values["lectures"],
                                  existing_ids(workspace), deps.profile)
    return {"action": "approve", "plan": plan, "selected_ids": selected,
            "plan_digest": fingerprint}


def _owner_decision(snapshot, deps: Deps, workspace: Path, flag: dict | None, parallel: int):
    """The batch waits for the owner at the plan: show it as plan.json holds it (with the
    owner's edits), with what its check found, then take the answer: `flag`, else asked in
    the terminal. Returns the Command that resumes the graph, or None to pause. A plan.json
    with a blocking problem is not approved (ValueError)."""
    from langgraph.types import Command

    plan_file = deps.drafts / "plan.json"
    plan, edited = _owner_plan(deps.drafts, snapshot.values["plan"])
    # The model's plan was checked when it was made; an edited one is checked here.
    # Display full-plan warnings without forcing unchecked edited rows through a schema.
    problems = snapshot.values.get("plan_problems", [])
    if edited:
        try:
            problems = blocking_problems(
                deps.profile.batch_plan.model_validate({"items": plan}).items,
                snapshot.values["lectures"], existing_ids(workspace))
        except ValueError as exc:
            problems = [str(exc)]
    _show_plan(plan, problems, plan_file, workspace)
    if edited:
        print(f"{plan_file.name} differs from the model's latest plan; approving uses "
              f"{plan_file.name} (delete it to take the model's plan)", flush=True)
    answer = flag or _ask()
    if answer is None:
        return None
    if not answer.get("approve"):
        return Command(resume={"action": "revise", "feedback": answer["feedback"]})
    decision = approval_decision(snapshot, deps, workspace, answer)
    print(f"generating {len(decision['selected_ids'])} items, {parallel} at a time", flush=True)
    return Command(resume=decision)


def _ask() -> dict | None:
    if not sys.stdin.isatty():
        return None
    try:
        answer = input("Approve? [y] / type feedback to send it back / Enter to pause: ").strip()
    except EOFError:
        # Windows counts NUL (stdin closed) as a terminal, so isatty() lets it through.
        return None
    if not answer:
        return None
    return {"approve": True} if answer.lower() in ("y", "yes") else {"feedback": answer}


def _owner_plan(drafts: Path, model_plan: list[dict]) -> tuple[list[dict], bool]:
    """The plan to show and approve, and whether the owner edited it. plan.json is written
    when the model's plan is new (the first pause, or a plan made again after feedback);
    otherwise it is kept, so a resume does not undo the owner's edits. plan.model.json
    holds the model's plan last written, to tell the two apart."""
    plan_file, model_file = drafts / "plan.json", drafts / "plan.model.json"
    text = json.dumps(model_plan, indent=1) + "\n"
    if plan_file.is_file() and not model_file.is_file():
        # Paused before plan.model.json existed: plan.json may hold the owner's edits (or
        # the plan before their feedback), so it is kept; the caller says it differs.
        model_file.write_text(text, encoding="utf-8")
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        return plan, plan != model_plan
    if not plan_file.is_file() or model_file.read_text(encoding="utf-8") != text:
        plan_file.write_text(text, encoding="utf-8")
        model_file.write_text(text, encoding="utf-8")
        return model_plan, False
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    return plan, plan != model_plan


def _show_plan(plan: list[dict], problems: list[str], plan_file: Path, workspace: Path) -> None:
    print()
    # Malformed deferred rows must remain visible and fixable rather than crash review.
    for n, p in enumerate(plan if isinstance(plan, list) else [], 1):
        if not isinstance(p, dict):
            print(f"{n}. {p!r}")
            continue
        print(f"{n}. {p.get('id', '?')}  [{p.get('type', '?')}, {p.get('interface', '?')}, "
              f"{p.get('framework', '?')}]  {p.get('lecture', '?')} slides {p.get('slides', '?')}")
        print(f"   {p.get('knowledge_point', '')}")
        if p.get("notes"):
            print(f"   checks: {p['notes']}")
        if p.get("reason"):
            print(f"   why: {p['reason']}")
    for problem in problems:
        print(f"   ! {problem}")
    print(f"\nThe plan is in {plan_file.relative_to(workspace)}; edit it before approving "
          "if you like.", flush=True)
