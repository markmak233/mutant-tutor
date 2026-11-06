"""A stand-in for `python -m tutor.graph` in GUI tests: the same arguments, and the files the
real command leaves in the course (WORKSPACE_DIR), without calling any model.

plan: the draft folder with checkpoint.sqlite, run.json, plan.json and plan.model.json, the
plan printed as the real command prints it (with a "!" line when the preferences say
"problem"), then the pause. resume --feedback: a new plan, then the pause. resume --approve,
or resume after generation began: each item's draft folder, its exercise in the batch, then
the manifest. FAKE_GRAPH_SLOW_S makes each item take that long (to try Stop). Every call is
appended to <course>/fake-graph-calls.jsonl.
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path


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
    for p in (plan, resume):
        p.add_argument("--batch", required=True)
    args = parser.parse_args()
    root = Path(os.environ["WORKSPACE_DIR"])
    with (root / "fake-graph-calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\n")
    drafts = root / "batches" / ".drafts"
    if args.command == "plan":
        draft = drafts / f"{date.today()}_p3-{args.batch}"
        draft.mkdir(parents=True)
        (draft / "checkpoint.sqlite").touch()
        settings = dict(s.split("=", 1) for s in args.set or [])
        (draft / "run.json").write_text(json.dumps({"profiles": {}, "settings": {
            "mode": args.mode, "hide_answers": settings.get("hide_answers") == "true"}}),
            encoding="utf-8")
        items = [_item(f"{args.batch}-ex{n}", args.lectures[0]) for n in range(args.count or 2)]
        return _pause(draft, items, ["2 items, 3 asked for"] if "problem" in args.prefs else [],
                      args.batch)
    found = sorted(drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite"))
    if not found:
        print(f"stopped: no planned batch labelled {args.batch}", file=sys.stderr)
        return 1
    draft = found[-1].parent
    items = json.loads((draft / "plan.json").read_text(encoding="utf-8"))
    if args.feedback is not None:
        items = items + [_item(f"{args.batch}-extra", items[0]["lecture"])]
        return _pause(draft, items, [], args.batch)
    if args.approve or any((draft / i["id"]).is_dir() for i in items):
        return _generate(root, draft, items)
    return _pause(draft, items, [], args.batch)


def _item(item_id: str, lecture: str) -> dict:
    return {"id": item_id, "type": "implement", "interface": "rdd", "framework": "spark",
            "lecture": lecture, "slides": "1.2-1.4", "knowledge_point": f"what {item_id} practises",
            "notes": "a check", "reason": "a gap"}


def _pause(draft: Path, items: list[dict], problems: list[str], label: str) -> int:
    text = json.dumps(items, indent=1) + "\n"
    (draft / "plan.json").write_text(text, encoding="utf-8")
    (draft / "plan.model.json").write_text(text, encoding="utf-8")
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


def _generate(root: Path, draft: Path, items: list[dict]) -> int:
    batch = root / "batches" / draft.name
    print(f"generating {len(items)} items, 1 at a time", flush=True)
    for item in items:
        (draft / item["id"] / "round-0").mkdir(parents=True, exist_ok=True)
        time.sleep(float(os.environ.get("FAKE_GRAPH_SLOW_S", "0")))
        exercise = batch / "exercises" / item["id"]
        exercise.mkdir(parents=True, exist_ok=True)
        (exercise / "question.md").write_text(f"# {item['id']}\n", encoding="utf-8")
        (batch / "solutions" / item["id"]).mkdir(parents=True, exist_ok=True)
        print(f"  [{item['id']}] validate: VALID", flush=True)
    (batch / "manifest.json").write_text(json.dumps(
        {"mode": "learn", "exercises": [i["id"] for i in items]}), encoding="utf-8")
    print(f"\nbatches/{batch.name}: {len(items)} saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
