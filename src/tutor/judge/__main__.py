"""Judge a batch's teaching quality, or calibrate the judge against the owner's ratings.

    python -m tutor.judge <batch_dir> [<exercise_id> ...] [--force] [--profile NAME]
    python -m tutor.judge --calibrate [--blind DIR] [--defects DIR] [--out DIR] [--profile NAME]

The model is the workspace's `judge` role ([llm.roles] in workspace.toml) unless --profile
names another. A batch's verdicts go to solutions/<id>/quality.json; a calibration's to
--out (default experiments/<today>_judge/ in the workspace): one file per rated item, and
results.json with the scores.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from tutor import llm
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.judge.calibrate import (
    blind_items,
    daily_items,
    defect_items,
    judged,
    read_reviews,
    report,
    results_to_dict,
)
from tutor.judge.teaching import Judge, JudgeError, model_name, write


def main(argv: list[str] | None = None) -> int:
    """Exit code 0: done; 1: some exercise got no verdict; 2: wrong usage, or no workspace
    or model."""
    parser = argparse.ArgumentParser(prog="python -m tutor.judge")
    parser.add_argument("batch_dir", nargs="?", type=Path)
    parser.add_argument("exercise_ids", nargs="*", help="default: every exercise")
    parser.add_argument("--calibrate", action="store_true",
                        help="compare with the owner's ratings instead of judging a batch")
    parser.add_argument("--blind", type=Path, help="a blind comparison's folder to add")
    parser.add_argument("--defects", type=Path, help="a folder of planted defects to add")
    parser.add_argument("--out", type=Path, help="where a calibration's files go")
    parser.add_argument("--force", action="store_true", help="judge even if inputs are unchanged")
    parser.add_argument("--profile", help="a workspace.toml profile instead of the `judge` role")
    parser.add_argument("--parallel", type=int, default=4, help="calls at once")
    parser.add_argument("--all", action="store_true",
                        help="show minor flags too, not only the serious ones")
    args = parser.parse_args(argv)
    if args.calibrate == (args.batch_dir is not None):
        parser.print_usage()
        return 2
    load_env()
    try:
        root = load_workspace().root
        model = Judge(llm.config_for("judge", args.profile, workspace=root),
                      log_path=root / "logs" / "llm_calls.jsonl")
    except (WorkspaceError, llm.LLMError) as e:
        print(f"judge: {e}", file=sys.stderr)
        return 2
    if args.calibrate:
        return _calibrate(args, root, model)
    return _batch(args, root, model)


def _calibrate(args, root: Path, model: Judge) -> int:
    items = (daily_items(root) + (blind_items(root, args.blind) if args.blind else [])
             + (defect_items(root, args.defects) if args.defects else []))
    out = args.out or root / "experiments" / f"{date.today()}_judge"
    results = judged(items, root, model, out / "verdicts", args.parallel)
    reviews = read_reviews(out)  # the owner's answers on earlier flags, if any
    print(f"{len(items)} rated items, judged by {model_name(model)}\n")
    print(report(results, reviews))
    path = out / "results.json"
    path.write_text(json.dumps(results_to_dict(results, model_name(model), reviews),
                               indent=2, ensure_ascii=False) + "\n")
    print(f"\nsaved to {path}")
    return 0


def _batch(args, root: Path, model: Judge) -> int:
    batch = args.batch_dir
    ids = args.exercise_ids or sorted(p.name for p in (batch / "solutions").iterdir()
                                      if p.is_dir() and not p.name.startswith("."))

    def one(exercise_id: str) -> bool:
        try:
            letters, verdict = write(batch, exercise_id, root, model, args.force)
        except (JudgeError, llm.LLMError, OSError, ValueError) as e:
            # One exercise's failure never stops the others.
            print(f"{exercise_id}: failed: {e}", flush=True)
            return False
        lines = [f"{exercise_id}: {letters}"]
        if verdict is not None:  # the evidence for each flag, so it can be checked at once
            shown = verdict.flags(serious_only=not args.all)
            lines += [f"    {p}{'' if getattr(verdict, p).serious else ' (minor)'}: "
                      f"{getattr(verdict, p).evidence}" for p in shown]
            hidden = len(verdict.flags()) - len(shown)
            if hidden:
                lines.append(f"    (+{hidden} minor; --all shows them)")
        print("\n".join(lines), flush=True)
        return True

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        ok = list(pool.map(one, ids))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
