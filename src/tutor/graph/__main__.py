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
from tutor.graph.nodes import Deps, write_manifest
from tutor.graph.state import BatchPlan, PlanItem
from tutor.llm import LLMError, config_for

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
    item = sub.add_parser("item", help="generate one item from a JSON file")
    item.add_argument("item_file", type=Path)
    for p in (plan, resume, item):
        p.add_argument("--batch", required=True, help="batch label, e.g. w4-spark")
    for p in (plan, item):
        p.add_argument("--profile", help="profile for generating (default: generate role)")
        p.add_argument("--thinking", choices=["low", "on"], default="low",
                       help="on: the model reasons at length (slower; GLM gets cut off)")
        p.add_argument("--mode", choices=["learn", "review"], default="learn")
    plan.add_argument("--parallel", type=int, default=default_parallel())
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
    try:
        plan_config = config_for("plan", None, workspace)
    except LLMError:
        plan_config = None
    return Deps(workspace=workspace, batch_dir=batch_dir, config=config,
                plan_config=plan_config, thinking=settings.get("thinking") == "on",
                mode=settings.get("mode", "learn"), on_update=_progress)


def _item(args, workspace: Path, build) -> int:
    item = PlanItem.model_validate_json(args.item_file.read_text())
    existing = sorted((workspace / "batches").glob(f"*_p3-{args.batch}"))
    batch_dir = (existing[-1] if existing
                 else workspace / "batches" / f"{date.today()}_p3-{args.batch}")
    deps = _deps(workspace, batch_dir, vars(args))
    print(f"{item.id}: {deps.config.name} ({deps.config.model}, thinking {args.thinking}) -> "
          f"{batch_dir.relative_to(workspace)}", flush=True)
    final: dict = {}
    for update in build.build_item(deps).stream({"item": item}, stream_mode="updates"):
        for node, change in update.items():
            final.update(change or {})
            _progress(item.id, node, change or {})
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
        settings = {k: getattr(args, k) for k in ("profile", "thinking", "mode", "parallel")}
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
        if args.profile:  # e.g. finish a batch with another model; kept for later resumes
            settings["profile"] = args.profile
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
            plan_file.write_text(json.dumps(snapshot.values["plan"], indent=1) + "\n")
            _show_plan(snapshot.values["plan"], snapshot.values.get("plan_problems", []),
                       plan_file, workspace)
            answer = decision or _ask()
            decision = None  # a flag answers once
            if answer is None:
                print(f"Paused. Continue with: python -m tutor.graph resume --batch "
                      f"{args.batch} --approve   (or --feedback \"...\")")
                return 0
            if answer.get("approve"):
                plan = json.loads(plan_file.read_text())  # with the owner's edits
                BatchPlan.model_validate({"items": plan})
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
        text = f"{node} (round {change['round']}): {state}, {change['tokens']:,} tokens so far"
    elif node == "validate":
        failure = change.get("failure")
        text = ("validate: VALID" if failure is None
                else "validate: " + failure.splitlines()[0][:160])
    else:
        text = node
    print(f"  [{exercise}] {text}", flush=True)


def _summary(results: list[dict], batch_dir: Path, workspace: Path) -> int:
    print(f"\n{batch_dir.relative_to(workspace)}:")
    for r in results:
        why = f"  ({r['last_failure'].splitlines()[0][:100]})" if r["outcome"] != "saved" else ""
        print(f"  {r['outcome']:<9} {r['id']}: {r['rounds'] + 1} round(s), "
              f"{r['tokens']:,} tokens{why}")
    if (batch_dir / "coverage-proposal.md").is_file():
        proposal = (batch_dir / "coverage-proposal.md").relative_to(workspace)
        print(f"coverage.md changes proposed in {proposal}")
    return 0 if any(r["outcome"] == "saved" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
