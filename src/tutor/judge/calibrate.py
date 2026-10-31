"""Check the judge against the owner's ratings before trusting it (roadmap 3b step 3).

Labels come from two places:
- the workspace's ratings.jsonl: one line per solved exercise (batch, exercise, the four
  points as true/false); the exercise is judged with its current question.md;
- with --blind DIR, a blind comparison (experiments/<date>_blind/): its ratings.jsonl rows
  name a folder ("1-dgim-bucket-update"), a version ("X") and the version's arm; X.md is
  the text the owner rated, and arms.json maps each arm to the batch (relative to the
  workspace) that holds that version's skeleton and reference.
A label line may list "skip_points", points it must not be compared on: the owner noted
three week 5 "clear" ratings were about the docstring's layout, not the content.

Per point: of the owner's n, how many the judge gives too (found); of the owner's y, how
many it marks n (false n). Trusted when found >= 80% and false n <= 20%. A point without
any n label cannot be checked, so it is "uncalibrated" whatever the judge says.

With --defects DIR, planted defects (experiments/<date>_defects/): each folder holds an
owner-rated yyyy exercise's question.md with one defect planted, maybe its own reference,
and defect.json naming the batch, the exercise and the point it breaks. Only that point is
labelled (n, by construction); the report scores these apart from the real ratings, since
a planted defect is easier to spot than a real one.

Agreement is not the whole story: on 2026-10-31 most of gpt-terra's "false n" named real
defects the owner's y had missed (a wrong claim about count-min, a missing start in
Boyer-Moore that had caused the owner's own bug). So each judge flag (an n where the owner
said y or did not rate the point) is also a claim the owner can check: their answers go to
flag-reviews.jsonl in the calibration folder, and the report gives, per point, how many
reviewed flags were right. The judge is a reminder of what to look at, not a rating.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from tutor import llm
from tutor.judge.teaching import POINTS, Judge, JudgeError, Verdict, gather, judge, model_name

TRUST_FOUND = 0.8  # the judge must find at least this share of the owner's n
TRUST_FALSE_N = 0.2  # and mark at most this share of the owner's y as n
# The owner's answers on the judge's flags, one line each: {"name", "point",
# "owner_says": "right" or "wrong", "date", "note"}; in the calibration's --out folder.
REVIEWS_FILE = "flag-reviews.jsonl"


@dataclass(frozen=True)
class Item:
    """One rated exercise to judge."""

    name: str  # how the report names it: "<batch>/<id>", "blind 1-dgim-bucket-update X (C)"
    batch_dir: Path
    exercise_id: str
    labels: dict[str, bool]  # point -> the owner's y (True) or n (False); skipped points left out
    question: str | None = None  # the text the owner rated, when not the current question.md
    reference: str | None = None  # a planted defect's own reference, when it has one
    planted: bool = False  # a synthetic defect, scored apart from the real ratings


def _labels(row: dict) -> dict[str, bool]:
    skip = set(row.get("skip_points", []))
    return {p: bool(row[p]) for p in POINTS if p in row and p not in skip}


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def daily_items(workspace: Path) -> list[Item]:
    """ratings.jsonl, without exercises whose batch is no longer in the workspace."""
    items = []
    for row in _rows(workspace / "ratings.jsonl"):
        batch = workspace / "batches" / row["batch"]
        if (batch / "solutions" / row["exercise"]).is_dir():
            items.append(Item(f"{row['batch']}/{row['exercise']}", batch, row["exercise"],
                              _labels(row)))
    return items


def blind_items(workspace: Path, blind_dir: Path) -> list[Item]:
    arms = json.loads((blind_dir / "arms.json").read_text())  # arm -> batch, from workspace
    items = []
    for row in _rows(blind_dir / "ratings.jsonl"):
        exercise = row["folder"].split("-", 1)[1]  # "1-dgim-bucket-update" -> the id
        question = (blind_dir / row["folder"] / f"{row['version']}.md").read_text()
        items.append(Item(f"blind {row['folder']} {row['version']} ({row['arm']})",
                          workspace / arms[row["arm"]], exercise, _labels(row), question))
    return items


def defect_items(workspace: Path, folder: Path) -> list[Item]:
    """One item per planted defect: <folder>/<variant>/{question.md, defect.json,
    reference.py (optional)}; defect.json gives "batch", "exercise" and "point"."""
    items = []
    for variant in sorted(p for p in folder.iterdir() if (p / "defect.json").is_file()):
        defect = json.loads((variant / "defect.json").read_text())
        reference = variant / "reference.py"
        items.append(Item(f"planted {variant.name}", workspace / "batches" / defect["batch"],
                          defect["exercise"], {defect["point"]: False},
                          (variant / "question.md").read_text(),
                          reference.read_text() if reference.is_file() else None, True))
    return items


def judged(items: list[Item], workspace: Path, model: Judge, store: Path,
           parallel: int = 4) -> list[tuple[Item, Verdict | None, str]]:
    """Each item's verdict (None and why, if the model gave none). Verdicts are kept in
    `store`, one file per item, and reused while the model and the inputs are the same, so
    a rerun only pays for what changed."""
    store.mkdir(parents=True, exist_ok=True)

    def one(item: Item) -> tuple[Item, Verdict | None, str]:
        inputs = gather(item.batch_dir, item.exercise_id, workspace, item.question,
                        item.reference)
        path = store / (re.sub(r"[^\w.-]+", "_", item.name) + ".json")
        if path.is_file():
            old = json.loads(path.read_text())
            if old["inputs"] == inputs.key() and old["model"] == model_name(model):
                return item, Verdict.model_validate(old["verdict"]), ""
        try:
            verdict, tokens = judge(inputs, model)
        except (JudgeError, llm.LLMError) as e:
            return item, None, str(e)
        path.write_text(json.dumps({"name": item.name, "inputs": inputs.key(),
                                    "model": model_name(model),
                                    "verdict": verdict.model_dump(), "tokens": tokens},
                                   indent=2, ensure_ascii=False) + "\n")
        return item, verdict, ""

    # The calls wait on the network, so a few go at once.
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        return list(pool.map(one, items))


@dataclass(frozen=True)
class Score:
    """How the judge did on one point."""

    point: str
    n_labels: int  # the owner's n
    found: int  # of those, the judge's n too
    y_labels: int  # the owner's y
    false_n: int  # of those, the judge's n

    @property
    def status(self) -> str:
        if self.n_labels == 0:
            return "uncalibrated"  # nothing to show it can find a problem
        found_ok = self.found / self.n_labels >= TRUST_FOUND
        false_ok = self.y_labels == 0 or self.false_n / self.y_labels <= TRUST_FALSE_N
        return "trusted" if found_ok and false_ok else "not trusted"


def scores(pairs: list[tuple[Item, Verdict]]) -> list[Score]:
    result = []
    for p in POINTS:
        # (owner's label, judge's) for every item the owner rated on this point
        both = [(item.labels[p], getattr(v, p).ok) for item, v in pairs if p in item.labels]
        result.append(Score(
            p,
            n_labels=sum(not owner for owner, _ in both),
            found=sum(not owner and not ok for owner, ok in both),
            y_labels=sum(owner for owner, _ in both),
            false_n=sum(owner and not ok for owner, ok in both),
        ))
    return result


def owner_letters(item: Item) -> str:
    """The owner's labels as "nyyy", "-" for a skipped point."""
    return "".join("-" if p not in item.labels else "y" if item.labels[p] else "n"
                   for p in POINTS)


def judge_flags(results: list[tuple[Item, Verdict | None, str]]) -> list[tuple[Item, str, str]]:
    """(item, point, evidence) for each n of the judge where the owner did not say n
    (they said y, or skipped the point): a claim the owner can check."""
    return [(item, p, getattr(v, p).evidence) for item, v, _ in results if v is not None
            for p in POINTS if not getattr(v, p).ok and item.labels.get(p, True)]


def read_reviews(folder: Path) -> dict[tuple[str, str], str]:
    """(item name, point) -> "right" or "wrong", from REVIEWS_FILE; {} if none yet."""
    path = folder / REVIEWS_FILE
    return ({(r["name"], r["point"]): r["owner_says"] for r in _rows(path)}
            if path.is_file() else {})


def precision(results: list[tuple[Item, Verdict | None, str]],
              reviews: dict[tuple[str, str], str]) -> dict[str, tuple[int, int]]:
    """point -> (flags the owner found right, flags reviewed)."""
    found = {p: [0, 0] for p in POINTS}
    for item, p, _ in judge_flags(results):
        answer = reviews.get((item.name, p))
        if answer is not None:
            found[p][1] += 1
            found[p][0] += answer == "right"
    return {p: (right, reviewed) for p, (right, reviewed) in found.items()}


def report(results: list[tuple[Item, Verdict | None, str]],
           reviews: dict[tuple[str, str], str] | None = None) -> str:
    reviews = reviews or {}
    pairs = [(item, v) for item, v, _ in results if v is not None]
    real = [(item, v) for item, v in pairs if not item.planted]
    planted = [(item, v) for item, v in pairs if item.planted]
    lines = ["real ratings:",
             f"{'point':<8} {'owner n':>7} {'found':>5} {'owner y':>7} {'false n':>7}  status"]
    for s in scores(real):
        lines.append(f"{s.point:<8} {s.n_labels:>7} {s.found:>5} {s.y_labels:>7} "
                     f"{s.false_n:>7}  {s.status}")
    if planted:
        # Only the planted point is labelled, so there is no false n to count here.
        lines += ["", "planted defects (found / planted):"]
        lines += [f"{s.point:<8} {s.found}/{s.n_labels}" for s in scores(planted) if s.n_labels]
    lines.append("")
    lines.append("missed (owner n, judge y):")
    for item, v in pairs:
        for p in POINTS:
            if item.labels.get(p) is False and getattr(v, p).ok:
                lines.append(f"  {item.name}: {p}: {getattr(v, p).evidence}")
    flags = judge_flags(results)
    lines.append("")
    lines.append(f"judge flags (judge n, owner y or not rated), {len(flags)}; the owner's "
                 f"answers in {REVIEWS_FILE}:")
    verdicts = {item.name: v for item, v, _ in results if v is not None}
    for number, (item, p, evidence) in enumerate(flags, start=1):
        answer = reviews.get((item.name, p), "to review")
        weight = "serious" if getattr(verdicts[item.name], p).serious else "minor"
        lines.append(f"  {number:>2}. [{answer}] [{weight}] {item.name}: {p}: {evidence}")
    reviewed = precision(results, reviews)
    if any(n for _, n in reviewed.values()):
        lines.append("")
        lines.append("flags the owner found right: " + ", ".join(
            f"{p} {right}/{n}" for p, (right, n) in reviewed.items() if n))
    failed = [(item, why) for item, v, why in results if v is None]
    if failed:
        lines.append("")
        lines.extend(f"no verdict: {item.name}: {why}" for item, why in failed)
    return "\n".join(lines)


def results_to_dict(results: list[tuple[Item, Verdict | None, str]], model: str,
                    reviews: dict[tuple[str, str], str] | None = None) -> dict:
    pairs = [(item, v) for item, v, _ in results if v is not None]
    return {
        "model": model,
        "scores": [{"point": s.point, "n_labels": s.n_labels, "found": s.found,
                    "y_labels": s.y_labels, "false_n": s.false_n, "status": s.status}
                   for s in scores([(i, v) for i, v in pairs if not i.planted])],
        "planted": [{"point": s.point, "planted": s.n_labels, "found": s.found}
                    for s in scores([(i, v) for i, v in pairs if i.planted]) if s.n_labels],
        "flags_right": {p: {"right": right, "reviewed": n}
                        for p, (right, n) in precision(results, reviews or {}).items()},
        "items": [{"name": item.name, "owner": owner_letters(item),
                   "judge": v.letters() if v else None, "error": why or None}
                  for item, v, why in results],
    }
