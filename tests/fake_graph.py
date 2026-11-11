"""A stand-in for `python -m tutor.graph` in GUI tests: the same arguments, and the files the
real command leaves in the course (WORKSPACE_DIR), without calling any model.

plan: the draft folder with checkpoint.sqlite, run.json, plan.json and plan.model.json, the
plan printed as the real command prints it (with a "!" line when the preferences say
"problem"), then the pause. resume --feedback: a new plan, then the pause. resume --approve,
or resume after approval: each item's draft folder, its exercise in the batch, then
the manifest. FAKE_GRAPH_SLOW_S makes each item take that long (to try Stop). Every call is
appended to <course>/fake-graph-calls.jsonl. Each item's outcome goes to the batch's
generation.jsonl, as the real command logs it; FAKE_GRAPH_FAIL names ids (comma-separated)
that end "discarded" instead of saved. item ITEM_FILE: that one item, added to the batch.
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

from fake_checkpoint import write_checkpoint

from tutor.graph.plan_selection import checkpoint_values, choose, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--lectures", nargs="+", required=True)
    plan.add_argument("--prefs", default="")
    plan.add_argument("--count", type=int)
    plan.add_argument("--max-count", type=int)
    plan.add_argument("--mode")
    plan.add_argument("--set", action="append")
    resume = sub.add_parser("resume")
    resume.add_argument("--approve", action="store_true")
    resume.add_argument("--feedback")
    resume.add_argument("--only")
    resume.add_argument("--plan-digest")
    item = sub.add_parser("item")
    item.add_argument("item_file", type=Path)
    item.add_argument("--set", action="append")
    for flag in ("--profile", "--oracle-profile", "--repair-profile", "--mode", "--thinking",
                 "--repair-thinking"):
        item.add_argument(flag)
    for p in (plan, resume, item):
        p.add_argument("--batch", required=True)
    args = parser.parse_args()
    root = Path(os.environ["WORKSPACE_DIR"])
    with (root / "fake-graph-calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\n")
    drafts = root / "batches" / ".drafts"
    if args.command == "plan":
        draft = drafts / f"{date.today()}_p3-{args.batch}"
        draft.mkdir(parents=True)
        settings = dict(s.split("=", 1) for s in args.set or [])
        (draft / "run.json").write_text(json.dumps({"profiles": {}, "settings": {
            "mode": args.mode, "hide_answers": settings.get("hide_answers") == "true"}}),
            encoding="utf-8")
        items = [_item(f"{args.batch}-ex{n}", args.lectures[0]) for n in range(args.count or 2)]
        return _pause(draft, items, ["2 items, 3 asked for"] if "problem" in args.prefs else [],
                      args.batch)
    if args.command == "item":
        one = json.loads(args.item_file.read_text(encoding="utf-8"))
        draft = sorted(drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite"))[-1].parent
        return _generate(root, draft, [one], add=True)
    found = sorted(drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite"))
    if not found:
        print(f"stopped: no planned batch labelled {args.batch}", file=sys.stderr)
        return 1
    draft = found[-1].parent
    items = json.loads((draft / "plan.json").read_text(encoding="utf-8"))
    if args.feedback is not None:
        items = items + [_item(f"{args.batch}-extra", items[0]["lecture"])]
        return _pause(draft, items, [], args.batch)
    state = checkpoint_values(draft)
    if args.approve:
        if args.plan_digest and args.plan_digest != digest((draft / "plan.json").read_bytes()):
            print("stopped: plan changed after review", file=sys.stderr)
            return 1
        selected = choose(items, args.only.split(",") if args.only is not None else None)
        state = {"plan": items, "lectures": state["lectures"], "approved": True,
                 "selected_ids": selected, "generation_complete": False}
        write_checkpoint(draft, state)
    if state.get("approved"):
        result = _generate(root, draft, [i for i in state["plan"]
                                        if i["id"] in state["selected_ids"]])
        write_checkpoint(draft, {**state, "generation_complete": True})
        return result
    return _pause(draft, items, [], args.batch)


def _item(item_id: str, lecture: str) -> dict:
    return {"id": item_id, "type": "implementation", "interface": "function", "framework": "python",
            "lecture": lecture, "slides": "1.2-1.4", "knowledge_point": f"what {item_id} practises",
            "notes": "a check", "reason": "a gap"}


def _pause(draft: Path, items: list[dict], problems: list[str], label: str) -> int:
    text = json.dumps(items, indent=1) + "\n"
    (draft / "plan.json").write_text(text, encoding="utf-8")
    (draft / "plan.model.json").write_text(text, encoding="utf-8")
    write_checkpoint(draft, {"plan": items, "approved": False,
                             "lectures": list(dict.fromkeys(i["lecture"] for i in items))})
    print(f"plan: {len(items)} items\n")
    for n, p in enumerate(items, 1):
        print(f"{n}. {p['id']}  [{p['type']}, {p['interface']}, {p['framework']}]  "
              f"{p['lecture']} slides {p['slides']}")
        print(f"   {p['knowledge_point']}")
    for problem in problems:
        print(f"   ! {problem}")
    print(f"\nThe plan is in batches/.drafts/{draft.name}/plan.json; edit it before approving "
          "if you like.")
    print(f"Paused. Continue with: python -m tutor.graph resume --batch {label} --approve")
    return 0


def _generate(root: Path, draft: Path, items: list[dict], add: bool = False) -> int:
    batch = root / "batches" / draft.name
    batch.mkdir(parents=True, exist_ok=True)
    print(f"generating {len(items)} items, 1 at a time", flush=True)
    failing = set(filter(None, os.environ.get("FAKE_GRAPH_FAIL", "").split(",")))
    saved = []
    for item in items:
        (draft / item["id"] / "round-0").mkdir(parents=True, exist_ok=True)
        time.sleep(float(os.environ.get("FAKE_GRAPH_SLOW_S", "0")))
        if item["id"] in failing and not add:
            _log(batch, item["id"], "discarded", [{"failure": "rule 1 (reference passes) FAIL"}])
            print(f"  [{item['id']}] validate: rule 1 (reference passes) FAIL", flush=True)
            continue
        exercise = batch / "exercises" / item["id"]
        exercise.mkdir(parents=True, exist_ok=True)
        (exercise / "question.md").write_text(f"# {item['id']}\n", encoding="utf-8")
        (batch / "solutions" / item["id"]).mkdir(parents=True, exist_ok=True)
        _log(batch, item["id"], "saved", [])
        print(f"  [{item['id']}] validate: VALID", flush=True)
        saved.append(item["id"])
    manifest = batch / "manifest.json"
    known = (json.loads(manifest.read_text(encoding="utf-8"))["exercises"]
             if manifest.is_file() else [])
    if saved:
        manifest.write_text(json.dumps({"mode": "learn", "exercises":
                                         list(dict.fromkeys([*known, *saved]))}),
                            encoding="utf-8")
    print(f"\nbatches/{batch.name}: {len(saved)} saved")
    return 0


def _log(batch: Path, exercise: str, outcome: str, attempts: list[dict]) -> None:
    with (batch / "generation.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"exercise": exercise, "outcome": outcome, "rounds": len(attempts),
                            "attempts": attempts}) + "\n")


if __name__ == "__main__":
    sys.exit(main())
