"""Generate exercises (docs/design.md, "Generation pipeline"). Needs the `gen` extra:
pip install -e ".[gen]".

    python -m tutor.graph plan --lectures L4.1 L4.2 --batch w4 [--prefs "..."] [--count N]
    python -m tutor.graph resume --batch w4 [--approve | --feedback "..."]
    python -m tutor.graph item ITEM.json --batch w4-exp      (one item, for experiments)

`plan` asks the `plan` role's model ([llm.roles] in workspace.toml; the `generate` role if
there is none) for a batch plan from coverage.md's gaps, shows it, and waits for the owner:
approve it (after editing batches/.drafts/<batch>/plan.json if wished), or send it back with
feedback. The state is saved, so `resume` continues later, in another terminal. Approved
items are generated in parallel (--parallel, default from the CPU count) into
`batches/<today>_p3-<LABEL>/`; each round's files and report stay in
`batches/.drafts/<batch>/<id>/round-N/`; outcomes go to the batch's generation.jsonl, and
coverage-proposal.md lists the coverage.md changes for the owner to apply.

With an `oracle` role (or --oracle-profile), that model writes each exercise's oracle.py
from the question alone; it must be another provider than the generating model. With a
`repair` role (or --repair-profile), a larger model takes over from the second repair.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.graph.nodes import Deps, blocking_problems, existing_ids, write_manifest
from tutor.graph.state import BatchPlan, PlanItem
from tutor.llm import LLMError, config_for, roles

_CONTINUE = object()  # resume a run that stopped midway (nothing new to send)


def default_parallel() -> int:
    """Items generated at once: each validation runs Spark with every core, so about one
    per four cores (3 on a 12-core M2 Pro), between 1 and 4."""
    return max(1, min(4, (os.cpu_count() or 4) // 4))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.graph")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="plan a batch, then generate it once approved")
    plan.add_argument("--lectures", nargs="+", required=True,
                      help="lecture file names without .pdf, as in lectures/")
    plan.add_argument("--prefs", default="", help="your preferences, in plain words")
    plan.add_argument("--count", type=int,
                      help="exactly this many (default: one per knowledge point)")
    plan.add_argument("--max-count", type=int, default=6,
                      help="at most this many when --count is not given")
    resume = sub.add_parser("resume", help="continue a planned batch")
    resume.add_argument("--approve", action="store_true")
    resume.add_argument("--feedback")
    resume.add_argument("--profile", help="generate the remaining items with this profile")
    resume.add_argument("--oracle-profile", help="write their oracles with this profile")
    resume.add_argument("--repair-profile", help="repair them with this profile")
    item = sub.add_parser("item", help="generate one item from a JSON file")
    item.add_argument("item_file", type=Path)
    for p in (plan, resume, item):
        p.add_argument("--batch", required=True, help="batch label, e.g. w4-spark")
    for p in (plan, item):
        p.add_argument("--profile", help="profile for generating (default: generate role)")
        p.add_argument("--oracle-profile",
                       help="profile writing the oracle (default: oracle role, if set)")
        p.add_argument("--repair-profile",
                       help="profile for repairs from the second on (default: repair role)")
        p.add_argument("--repair-thinking", choices=["low", "on"], default="on",
                       help="whether that repair model reasons first (default: on)")
        p.add_argument("--thinking", choices=["low", "on"], default="low",
                       help="on: the model reasons at length (slower; GLM gets cut off)")
        p.add_argument("--mode", choices=["learn", "review"], default="learn")
    plan.add_argument("--parallel", type=int, default=default_parallel())
    plan.add_argument("--plan-profile", help="profile that plans (default: plan role)")
    plan.add_argument("--plan-thinking", choices=["low", "on"], default="on",
                      help="whether the planning model reasons first (default: on)")
    args = parser.parse_args(argv)
    try:
        from tutor.graph import build
    except ImportError:
        print('langgraph is not installed: pip install -e ".[gen]"', file=sys.stderr)
        return 1
    load_env()
    try:
        workspace = load_workspace().root
        if args.command == "item":
            return _item(args, workspace, build)
        return _batch(args, workspace, build)
    except (WorkspaceError, LLMError, OSError, ValidationError, ValueError) as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1


def _deps(workspace: Path, batch_dir: Path, settings: dict) -> Deps:
    config = config_for("generate", settings.get("profile"), workspace)
    plan_config = _optional_role("plan", settings.get("plan_profile"), workspace)
    oracle_config = _optional_role("oracle", settings.get("oracle_profile"), workspace)
    repair_config = _optional_role("repair", settings.get("repair_profile"), workspace)
    for writer in (config, repair_config):
        if oracle_config and writer and (oracle_config.provider, oracle_config.base_url) == (
                writer.provider, writer.base_url):
            # One provider's models share training data and habits: not an independent
            # reading.
            raise ValueError(f"the oracle ({oracle_config.name}) and {writer.name}, which "
                             "writes the exercise, come from one provider; choose another "
                             "oracle")
    return Deps(workspace=workspace, batch_dir=batch_dir, config=config,
                plan_config=plan_config, oracle_config=oracle_config,
                repair_config=repair_config,
                repair_thinking=settings.get("repair_thinking", "on") == "on",
                plan_thinking=settings.get("plan_thinking", "on") == "on",
                thinking=settings.get("thinking") == "on",
                mode=settings.get("mode", "learn"), on_update=_progress,
                on_stream=_receiving)


def _optional_role(role: str, profile: str | None, workspace: Path):
    """The role's model, or None when [llm.roles] does not name it and no profile is given
    (config_for would fall back to the LLM_* variables, i.e. the generating model)."""
    if not profile and role not in roles(workspace):
        return None
    return config_for(role, profile, workspace)


def _item(args, workspace: Path, build) -> int:
    item = PlanItem.model_validate_json(args.item_file.read_text())
    existing = sorted((workspace / "batches").glob(f"*_p3-{args.batch}"))
    batch_dir = (existing[-1] if existing
                 else workspace / "batches" / f"{date.today()}_p3-{args.batch}")
    deps = _deps(workspace, batch_dir, vars(args))
    oracle = f", oracle {deps.oracle_config.name}" if deps.oracle_config else ""
    oracle += f", repair {deps.repair_config.name}" if deps.repair_config else ""
    print(f"{item.id}: {deps.config.name} ({deps.config.model}, thinking {args.thinking}"
          f"{oracle}) -> {batch_dir.relative_to(workspace)}", flush=True)
    final: dict = {}
    for update in build.build_item(deps).stream({"item": item}, stream_mode="updates"):
        for node, change in update.items():
            final.update(change or {})
            _progress(item.id, node, change or {})
    if final.get("usage"):
        print(f"  {_usage_text(final['usage'])}")
    if final.get("outcome") == "saved":
        write_manifest(deps, [item])
        return 0
    return 1


def _batch(args, workspace: Path, build) -> int:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command

    drafts = workspace / "batches" / ".drafts"
    if args.command == "plan":
        if list((workspace / "batches").glob(f"*_p3-{args.batch}")) or list(
                drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite")):
            raise ValueError(f"a batch labelled {args.batch} exists: resume it or choose "
                             "another label")
        batch_dir = workspace / "batches" / f"{date.today()}_p3-{args.batch}"
        settings = {k: getattr(args, k) for k in (
            "profile", "plan_profile", "plan_thinking", "oracle_profile", "repair_profile",
            "repair_thinking", "thinking", "mode", "parallel")}
        (drafts / batch_dir.name).mkdir(parents=True)
        (drafts / batch_dir.name / "run.json").write_text(json.dumps(settings, indent=1))
        pending = {"lectures": args.lectures, "preferences": args.prefs,
                   "count": args.count, "max_count": args.max_count}
    else:
        found = sorted(drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite"))
        if not found:
            raise ValueError(f"no planned batch labelled {args.batch}")
        batch_dir = workspace / "batches" / found[-1].parent.name
        settings = json.loads((found[-1].parent / "run.json").read_text())
        # e.g. finish a batch with another model; kept for later resumes
        changed = {k: getattr(args, k) for k in ("profile", "oracle_profile", "repair_profile")
                   if getattr(args, k)}
        if changed:
            settings.update(changed)
            (found[-1].parent / "run.json").write_text(json.dumps(settings, indent=1))
        pending = None  # set by the owner's decision below
    deps = _deps(workspace, batch_dir, settings)
    config = {"configurable": {"thread_id": batch_dir.name},
              "max_concurrency": settings["parallel"]}
    with SqliteSaver.from_conn_string(str(deps.drafts / "checkpoint.sqlite")) as saver:
        app = build.build_batch(deps, saver)
        decision = ({"approve": True} if getattr(args, "approve", False)
                    else {"feedback": args.feedback} if getattr(args, "feedback", None) else None)
        while True:
            if pending is not None and pending is not _CONTINUE:
                for update in app.stream(pending, config, stream_mode="updates"):
                    if "plan" in update:
                        print(f"plan: {len(update['plan']['plan'])} items", flush=True)
            snapshot = app.get_state(config)
            if not snapshot.next:
                return _summary(snapshot.values.get("results", []), batch_dir, workspace)
            if not any(task.interrupts for task in snapshot.tasks):
                # Not waiting for the owner: an earlier run stopped midway. Continue it;
                # items that finished keep their results and are not run again.
                print(f"continuing the stopped run at: {', '.join(snapshot.next)}", flush=True)
                pending = _CONTINUE
                for _ in app.stream(None, config, stream_mode="updates"):
                    pass
                continue
            plan_file = deps.drafts / "plan.json"
            plan, edited = _owner_plan(deps.drafts, snapshot.values["plan"])
            problems = (blocking_problems(BatchPlan.model_validate({"items": plan}).items,
                                          snapshot.values["lectures"], existing_ids(workspace))
                        if edited else snapshot.values.get("plan_problems", []))
            _show_plan(plan, problems, plan_file, workspace)
            if edited:
                print(f"{plan_file.name} differs from the model's latest plan; approving uses "
                      f"{plan_file.name} (delete it to take the model's plan)", flush=True)
            answer = decision or _ask()
            decision = None  # a flag answers once
            if answer is None:
                print(f"Paused. Continue with: python -m tutor.graph resume --batch "
                      f"{args.batch} --approve   (or --feedback \"...\")")
                return 0
            if answer.get("approve"):
                plan = json.loads(plan_file.read_text())  # with the owner's edits
                items = BatchPlan.model_validate({"items": plan}).items
                if found := blocking_problems(items, snapshot.values["lectures"],
                                              existing_ids(workspace)):
                    raise ValueError(f"{plan_file.name} cannot be approved: {'; '.join(found)}."
                                     " Fix it, then resume with --approve again")
                pending = Command(resume={"action": "approve", "plan": plan})
                print(f"generating {len(plan)} items, {settings['parallel']} at a time",
                      flush=True)
            else:
                pending = Command(resume={"action": "revise", "feedback": answer["feedback"]})


def _ask() -> dict | None:
    if not sys.stdin.isatty():
        return None
    answer = input("Approve? [y] / type feedback to send it back / Enter to pause: ").strip()
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
        model_file.write_text(text)
        plan = json.loads(plan_file.read_text())
        return plan, plan != model_plan
    if not plan_file.is_file() or model_file.read_text() != text:
        plan_file.write_text(text)
        model_file.write_text(text)
        return model_plan, False
    plan = json.loads(plan_file.read_text())
    return plan, plan != model_plan


def _show_plan(plan: list[dict], problems: list[str], plan_file: Path, workspace: Path) -> None:
    print()
    for n, p in enumerate(plan, 1):
        print(f"{n}. {p['id']}  [{p['type']}, {p['interface']}, {p['framework']}]  "
              f"{p['lecture']} slides {p['slides']}")
        print(f"   {p['knowledge_point']}")
        if p.get("notes"):
            print(f"   checks: {p['notes']}")
        if p.get("reason"):
            print(f"   why: {p['reason']}")
    for problem in problems:
        print(f"   ! {problem}")
    print(f"\nThe plan is in {plan_file.relative_to(workspace)}; edit it before approving "
          "if you like.", flush=True)


def _progress(exercise: str, node: str, change: dict) -> None:
    if node == "retrieve_context":
        text = f"context: {len(change['context']):,} characters of course pages"
    elif node in ("generate", "repair"):
        state = "format error" if change.get("format_error") else "draft ready"
        by = f", {change['model']}" if change.get("model") else ""
        text = (f"{node} (round {change['round']}{by}): {state}, "
                f"{change['tokens']:,} tokens so far")
    elif node == "oracle":
        if not change:
            return  # no oracle role: the generating model wrote it
        text = f"oracle: {change['oracle_status']}" + (
            " (" + change["failure"].removeprefix("format: ")[:140] + ")"
            if change.get("oracle_status") == "failed" else "")
    elif node == "validate":
        failure = change.get("failure")
        text = ("validate: VALID" if failure is None
                else "validate: " + failure.splitlines()[0][:160])
        dropped = change["attempts"][-1].get("dropped") if change.get("attempts") else None
        if dropped:
            text += f" (left out, could not be applied: {', '.join(dropped)})"
    else:
        text = node
    print(f"  [{exercise}] {text}", flush=True)


_RECEIVED: dict[str, int] = {}  # characters of the reply arriving for each exercise
RECEIVING_STEP = 20_000


def _receiving(exercise: str, chars: int) -> None:
    """A line each time a streamed reply passes another RECEIVING_STEP characters."""
    if chars // RECEIVING_STEP > _RECEIVED.get(exercise, 0) // RECEIVING_STEP:
        print(f"  [{exercise}] receiving: {chars:,} characters", flush=True)
    _RECEIVED[exercise] = chars


def _usage_text(usage: dict[str, int]) -> str:
    """e.g. "input 45,277 tokens (13,440 from the provider's cache, 30%), output 9,809"."""
    share = usage["cached"] / usage["input"] if usage["input"] else 0.0
    return (f"input {usage['input']:,} tokens ({usage['cached']:,} from the provider's "
            f"cache, {share:.0%}), output {usage['output']:,}")


def _summary(results: list[dict], batch_dir: Path, workspace: Path) -> int:
    print(f"\n{batch_dir.relative_to(workspace)}:")
    total = {"input": 0, "cached": 0, "output": 0}
    for r in results:
        why = f"  ({r['last_failure'].splitlines()[0][:100]})" if r["outcome"] != "saved" else ""
        print(f"  {r['outcome']:<9} {r['id']}: {r['rounds'] + 1} round(s), "
              f"{r['tokens']:,} tokens{why}")
        for key in total:
            total[key] += r.get("usage", {}).get(key, 0)
    if total["input"]:
        print(f"  all items: {_usage_text(total)}")
    if (batch_dir / "coverage-proposal.md").is_file():
        proposal = (batch_dir / "coverage-proposal.md").relative_to(workspace)
        print(f"coverage.md changes proposed in {proposal}")
    return 0 if any(r["outcome"] == "saved" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
