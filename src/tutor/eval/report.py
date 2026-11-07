"""Read a workspace's records and write its report (docs/design.md, "Evaluation metrics").

No model is called and only the report is written. Every number comes from files that the
pipeline and the owner's test runs already left behind:

    batches/<batch>/manifest.json                     the exercises and their priority marks
    batches/<batch>/generation.jsonl                  one line per generation run
    batches/<batch>/exercises/<id>/student.py (.c)    compared with skeleton.py (.c), never shown
    batches/<batch>/solutions/<id>/quality.json       the judge's verdict
    batches/<batch>/solutions/<id>/heldout.json       the held-out checks
    batches/.drafts/<batch>/<id>/round-0/report.txt   how a first draft validated
    attempts/attempts.jsonl                           one line per test run
    ratings.jsonl                                     the owner's rating of a solved exercise
    logs/llm_calls.jsonl                              one line per model call

The report is private, as it names exercises. It still holds nothing that gives an answer
away: a hand-calculation exercise's answer can sit in a run's `first_error` or in a failure's
detail, so neither is copied, and test files are not read at all.

Only the standard library and small modules are imported: this may run after a test run, so
tutor.judge and tutor.llm, which pull in the model clients, stay out.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tutor.attempts import ATTEMPTS_FILE
from tutor.languages import student_path

REPORT_DIR = "reports"
REPORT_FILE = "report.md"
RATING_POINTS = ("clear", "fits", "taught", "targets")  # the owner's four questions

# An exercise's state, from its test runs and from whether its student file was changed.
PASSED = "passed"  # a logged run passed
STARTED = "started"  # logged runs, none passed yet
UNLOGGED = "changed, no run logged"  # edited, but no run reached the attempt log
NOT_STARTED = "not started"  # no logged run and the student file as it was handed out

# The start of a failed attempt's text: "rule 1 (reference passes) FAIL: <detail>". Only the
# rule's name is kept, as the detail can quote expected values.
_RULE = re.compile(r"rule (\S+) \(([^)]*)\)")
# Rule 3's line in a draft's report.txt: "   3 ok    mutants killed: 5/5 killed". When an
# earlier rule failed the line says "skip" with no count, so it does not match.
_KILLED = re.compile(r"^\s*3\s+\S+\s+mutants killed:\s*(\d+)/(\d+)", re.MULTILINE)

# What these numbers cannot show, printed with every report so a reader sees it beside them.
LIMITS = (
    "These are records of real runs, not a controlled experiment: batches differ in lecture, "
    "model and settings, so a difference between models is a lead and no more.",
    "Tokens per saved exercise count the generate, repair and oracle calls of every run in the "
    "batch, failed ones too. The call log names no exercise, so vision, judge and held-out "
    "calls appear only as totals by purpose, and those totals include experiments.",
    "Rule 3 passes on every saved exercise by construction, and the same model writes the tests "
    "and the mutants, so a high first-draft kill count does not prove strong tests.",
    "Judge verdicts are hints: a plain rerun of the same prompt changed the letters of 3 of 13 "
    "exercises, and a judge is calibrated only on the course its ratings came from.",
    "A held-out flag is a finding to read, not a proven defect.",
    "Ratings come from one person, and only from exercises they solved.",
    "Runs up to the first pass count logged runs only, so the real number can only be higher.",
)


# --- reading the records ----------------------------------------------------------------


def _read_jsonl(path: Path, gaps: list[str]) -> list[dict]:
    """A JSONL file's lines. A missing file is no lines; a bad line is counted in `gaps`, so
    one damaged line neither stops the report nor disappears without a word."""
    if not path.is_file():
        return []
    rows, bad = [], 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    if bad:
        gaps.append(f"{path.parent.name}/{path.name}: {bad} unreadable line(s) left out.")
    return rows


def _read_json(path: Path) -> dict | None:
    """A JSON object, or None if the file is missing or damaged."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError covers bad JSON and bad UTF-8
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Batch:
    """One batch as its files describe it."""

    name: str
    folder: Path
    exercises: list[str]
    priority: dict
    runs: list[dict]  # its generation.jsonl; none when the batch was written by hand


@dataclass
class Records:
    """Everything a report is built from."""

    root: Path
    batches: list[Batch]
    attempts: list[dict]
    ratings: list[dict]
    calls: list[dict]
    gaps: list[str]  # what could not be counted; printed at the end of the report


def collect(root: Path) -> Records:
    """Read the workspace's records."""
    gaps: list[str] = []
    batches = []
    folder = root / "batches"
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        # .drafts and .experiments hold unfinished and trial work, not batches to solve.
        if path.name.startswith(".") or not path.is_dir():
            continue
        manifest = _read_json(path / "manifest.json")
        if manifest is None:
            gaps.append(f"{path.name}: no readable manifest.json, so the batch is left out.")
            continue
        priority = manifest.get("priority")
        batches.append(Batch(path.name, path, list(manifest.get("exercises") or []),
                             priority if isinstance(priority, dict) else {},
                             _read_jsonl(path / "generation.jsonl", gaps)))
    return Records(root, batches, _read_jsonl(root / "attempts" / ATTEMPTS_FILE, gaps),
                   _read_jsonl(root / "ratings.jsonl", gaps),
                   _read_jsonl(root / "logs" / "llm_calls.jsonl", gaps), gaps)


# --- progress: what the owner has solved ------------------------------------------------


@dataclass
class ExerciseProgress:
    """One exercise's state."""

    exercise: str
    priority: str  # must, optional or skip
    status: str
    rated: bool
    runs_to_pass: int | None  # logged runs up to and including the first pass


def _priority(batch: Batch, exercise: str) -> str:
    """Its mark in the manifest; unlisted ones count as must (schema.Priority)."""
    for mark in ("optional", "skip"):
        if exercise in (batch.priority.get(mark) or []):
            return mark
    return "must"


def _changed(batch: Batch, exercise: str) -> bool:
    """Whether the student file (student.py, or student.c in a C exercise) differs from the
    skeleton the batch handed out. Bytes, not file times, as a copy or a rename changes a
    time; only the comparison's result leaves this function. The pipeline hands out the
    skeleton as it is, but a batch written by hand has a student.py of its own that differs
    from the start, so there an edit cannot be seen."""
    if not batch.runs:
        return False
    student = student_path(batch.folder / "exercises" / exercise)
    skeleton = batch.folder / "solutions" / exercise / f"skeleton{student.suffix}"
    if not (student.is_file() and skeleton.is_file()):
        return False
    return student.read_bytes() != skeleton.read_bytes()


def run_results(attempts: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Each exercise's results in the order they ran. A hint request shares the log, as a line
    with an `event` and no `result`, and is not a run."""
    results: dict[tuple[str, str], list[str]] = {}
    for line in attempts:
        if result := line.get("result"):
            results.setdefault((line.get("batch"), line.get("exercise")), []).append(result)
    return results


def exercise_progress(batch: Batch, results: dict[tuple[str, str], list[str]],
                      rated: set[tuple[str, str]]) -> list[ExerciseProgress]:
    """Every exercise of the batch with its state."""
    progress = []
    for exercise in batch.exercises:
        runs = results.get((batch.name, exercise), [])
        runs_to_pass = runs.index("passed") + 1 if "passed" in runs else None
        if runs_to_pass:
            status = PASSED
        elif runs:
            status = STARTED
        else:
            status = UNLOGGED if _changed(batch, exercise) else NOT_STARTED
        progress.append(ExerciseProgress(exercise, _priority(batch, exercise), status,
                                         (batch.name, exercise) in rated, runs_to_pass))
    return progress


# --- generation: how the pipeline did ---------------------------------------------------


@dataclass
class GenerationStats:
    """Generation runs added up. Each line of generation.jsonl is one run, so an exercise
    generated twice counts twice."""

    runs: int = 0
    saved: int = 0
    errors: int = 0  # stopped by something other than validation, e.g. a dropped connection
    first_pass: int = 0  # saved with no repair round
    tokens: int = 0
    input: int = 0
    cached: int = 0
    no_usage: int = 0  # older lines that do not split their tokens

    @property
    def discarded(self) -> int:
        return self.runs - self.saved - self.errors

    @property
    def validated(self) -> int:
        """Runs the validator had the last word on: the base of both pass rates. An error says
        nothing about a draft, as there may be none."""
        return self.runs - self.errors


def generation_stats(runs: list[dict]) -> GenerationStats:
    stats = GenerationStats(runs=len(runs))
    for run in runs:
        outcome = run.get("outcome")
        stats.saved += outcome == "saved"
        stats.errors += outcome == "error"
        stats.first_pass += outcome == "saved" and not run.get("rounds")
        stats.tokens += run.get("tokens") or 0
        usage = run.get("usage")
        if usage:
            stats.input += usage.get("input") or 0
            stats.cached += usage.get("cached") or 0
        else:
            stats.no_usage += 1
    return stats


def first_failure(run: dict) -> str | None:
    """What stopped a run's first draft, None if nothing did. Only the check's name."""
    attempts = run.get("attempts") or []
    if not attempts:
        return None
    first = attempts[0]
    if first.get("format_error"):
        return "format (reply did not parse or could not be written out)"
    failure = first.get("failure")
    if not failure:
        return None
    match = _RULE.match(failure)
    return f"rule {match[1]} ({match[2]})" if match else "other"


@dataclass
class DraftKills:
    """Rule 3 on first drafts, from the draft reports still on disk."""

    drafts: int = 0
    reached: int = 0  # drafts whose earlier rules passed, so rule 3 ran
    killed: int = 0
    mutants: int = 0


def draft_kills(root: Path, batches: list[Batch]) -> DraftKills:
    kills = DraftKills()
    for batch in batches:
        # One folder level: an exercise generated again keeps its old drafts deeper, in
        # replaced/, and those are not this exercise's first draft.
        for report in (root / "batches" / ".drafts" / batch.name).glob("*/round-0/report.txt"):
            kills.drafts += 1
            found = _KILLED.search(report.read_text(encoding="utf-8", errors="replace"))
            if found:
                kills.reached += 1
                kills.killed += int(found[1])
                kills.mutants += int(found[2])
    return kills


# --- quality: checks on saved exercises -------------------------------------------------


@dataclass
class QualityStats:
    """The judge, the held-out checks and the owner's ratings over some batches."""

    judged: int = 0
    serious: int = 0  # the judge raised a serious point
    heldout: int = 0
    flagged: int = 0  # at least one held-out check failed
    rated: int = 0
    points: Counter = field(default_factory=Counter)  # ratings answered yes, per point


def quality_stats(batches: list[Batch], ratings: list[dict]) -> QualityStats:
    stats = QualityStats()
    names = {batch.name for batch in batches}
    for batch in batches:
        for exercise in batch.exercises:
            solution = batch.folder / "solutions" / exercise
            if (quality := _read_json(solution / "quality.json")) is not None:
                stats.judged += 1
                stats.serious += bool(quality.get("serious"))
            if (heldout := _read_json(solution / "heldout.json")) is not None:
                stats.heldout += 1
                stats.flagged += bool(heldout.get("flagged"))
    for rating in ratings:
        if rating.get("batch") in names:
            stats.rated += 1
            stats.points.update(point for point in RATING_POINTS if rating.get(point) is True)
    return stats


# --- rendering --------------------------------------------------------------------------


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "-"


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def _progress_section(records: Records) -> list[str]:
    """One row per batch, then what is left to solve. Skip-marked exercises count only in their
    own column, so the other columns add up to the work the owner took on."""
    results = run_results(records.attempts)
    rated = {(r.get("batch"), r.get("exercise")) for r in records.ratings}
    rows, todo, runs_to_pass = [], [], []
    unlogged = unlogged_rated = 0
    listed = set()
    for batch in records.batches:
        listed.update((batch.name, exercise) for exercise in batch.exercises)
        progress = exercise_progress(batch, results, rated)
        solving = [p for p in progress if p.priority != "skip"]
        count = Counter(p.status for p in solving)
        rows.append([batch.name, len(progress), len(progress) - len(solving), count[PASSED],
                     count[STARTED], count[UNLOGGED], count[NOT_STARTED],
                     sum(p.rated for p in solving)])
        runs_to_pass += [p.runs_to_pass for p in solving if p.runs_to_pass]
        unlogged += count[UNLOGGED]
        unlogged_rated += sum(p.rated for p in solving if p.status == UNLOGGED)
        # A rating is written once an exercise is solved, so a rated one is done even when its
        # runs never reached the log.
        left = [p for p in solving if p.status != PASSED and not p.rated]
        parts = []
        for mark in ("must", "optional"):
            names = [p.exercise if p.status == NOT_STARTED else f"{p.exercise} ({p.status})"
                     for p in left if p.priority == mark]
            if names:
                parts.append(f"{mark}: " + ", ".join(names))
        if parts:
            todo.append(f"- {batch.name}: " + "; ".join(parts))
    lines = ["## Progress", ""]
    lines += _table(["Batch", "Exercises", "Skip", "Passed", "Started", UNLOGGED.capitalize(),
                     "Not started", "Rated"], rows)
    if runs_to_pass:
        lines += ["", f"Runs up to the first pass, over {len(runs_to_pass)} exercises: median "
                      f"{statistics.median(runs_to_pass):g}, most {max(runs_to_pass)}."]
    lines += ["", "### Still to solve", ""] + (todo or ["Nothing."])
    if unlogged:
        records.gaps.append(
            f"{unlogged} exercises were changed with no run logged ({unlogged_rated} of them "
            "rated): those runs happened outside the course venv or before the log existed. "
            "Check that `which pytest` points to this workspace's .venv.")
    if by_hand := sum(not batch.runs for batch in records.batches):
        records.gaps.append(f"{by_hand} batches were written by hand, so an edit there with no run "
                            "logged cannot be seen and such an exercise shows as not started.")
    strays = sum(len(runs) for key, runs in results.items() if key not in listed)
    if strays:
        records.gaps.append(f"{strays} test runs name an exercise no batch lists.")
    return lines


def _generation_row(label: str, stats: GenerationStats) -> list[object]:
    return [label, stats.runs, stats.saved, stats.discarded, stats.errors,
            _share(stats.first_pass, stats.validated), _share(stats.saved, stats.validated),
            f"{stats.tokens // stats.saved:,}" if stats.saved else "-",
            f"{stats.cached / stats.input:.0%}" if stats.input else "-"]


GENERATION_HEADER = ["", "Runs", "Saved", "Discarded", "Errors", "First pass", "After repair",
                     "Tokens per saved", "Cache share"]


def _generation_section(records: Records) -> list[str]:
    generated = [batch for batch in records.batches if batch.runs]
    runs = [run for batch in generated for run in batch.runs]
    if not runs:
        return ["## Generation", "", "No batch here was generated by the pipeline."]
    rows = [_generation_row(batch.name, generation_stats(batch.runs)) for batch in generated]
    rows.append(_generation_row("All", generation_stats(runs)))
    lines = ["## Generation", ""] + _table(["Batch", *GENERATION_HEADER[1:]], rows)

    models: dict[str, list[dict]] = {}
    for run in runs:
        models.setdefault(str(run.get("model") or "unknown"), []).append(run)
    lines += ["", "### By generating model", "", "Not a controlled comparison.", ""]
    lines += _table(["Model", *GENERATION_HEADER[1:]],
                    [_generation_row(name, generation_stats(part))
                     for name, part in sorted(models.items())])

    validated = [run for run in runs if run.get("outcome") != "error"]
    failures = Counter(first_failure(run) or "nothing: passed" for run in validated)
    lines += ["", "### What stopped the first draft", ""]
    lines += _table(["Check", "Runs"], [[name, n] for name, n in failures.most_common()])
    rounds = Counter(run.get("rounds") or 0 for run in validated)
    lines += ["", "Repair rounds per run: "
              + ", ".join(f"{n} with {r}" for r, n in sorted(rounds.items())) + "."]

    kills = draft_kills(records.root, generated)
    lines += ["", f"Mutants killed in first drafts: {_share(kills.killed, kills.mutants)}, over "
                  f"{kills.reached} drafts that reached rule 3."]
    if kills.drafts < len(runs):
        records.gaps.append(f"First-draft kills come from the {kills.drafts} draft reports kept, "
                            f"out of {len(runs)} generation runs.")
    if no_usage := generation_stats(runs).no_usage:
        records.gaps.append(f"{no_usage} generation runs do not split their tokens, so the cache "
                            "share leaves them out; their total tokens still count.")
    if no_settings := sum("settings" not in run for run in runs):
        records.gaps.append(f"{no_settings} generation runs have no `settings`, so what they ran "
                            "with is unknown.")
    return lines


def _quality_row(label: str, stats: QualityStats) -> list[object]:
    return [label, _share(stats.serious, stats.judged), _share(stats.flagged, stats.heldout),
            stats.rated, *(_share(stats.points[point], stats.rated) for point in RATING_POINTS)]


def _quality_section(records: Records) -> list[str]:
    """Generated batches beside hand-written ones, which are the baseline for ratings."""
    rows = [_quality_row(batch.name, quality_stats([batch], records.ratings))
            for batch in records.batches]
    for label, part in (("All generated", [b for b in records.batches if b.runs]),
                        ("All by hand", [b for b in records.batches if not b.runs])):
        if part:
            rows.append(_quality_row(label, quality_stats(part, records.ratings)))
    return ["## Checks on saved exercises", ""] + _table(
        ["Batch", "Judge: serious point", "Held-out: flagged", "Rated",
         *(point.capitalize() for point in RATING_POINTS)], rows)


def _calls_section(records: Records) -> list[str]:
    totals: dict[tuple[str, str], Counter] = {}
    for call in records.calls:
        key = (str(call.get("purpose") or "unknown"), str(call.get("model") or "unknown"))
        total = totals.setdefault(key, Counter())
        total["calls"] += 1
        total["input"] += call.get("input_tokens") or 0
        total["cached"] += call.get("cached_tokens") or 0
        total["output"] += call.get("output_tokens") or 0
    rows = [[purpose, model, t["calls"], f"{t['input']:,}",
             f"{t['cached'] / t['input']:.0%}" if t["input"] else "-", f"{t['output']:,}"]
            for (purpose, model), t in sorted(totals.items())]
    return ["## Model calls", ""] + _table(
        ["Purpose", "Model", "Calls", "Input tokens", "Cached", "Output tokens"], rows)


def render(records: Records, course: str, now: datetime | None = None) -> str:
    """The whole report. The sections add to records.gaps, so gaps are printed last."""
    now = now or datetime.now()
    lines = [f"# Evaluation report: {course}", "",
             f"Built {now:%Y-%m-%d %H:%M} from this workspace's records, with no model calls. "
             "Private: it names exercises.", ""]
    for section in (_progress_section, _generation_section, _quality_section, _calls_section):
        lines += section(records) + [""]
    lines += ["## What these numbers cannot show", ""] + [f"- {limit}" for limit in LIMITS]
    if records.gaps:
        lines += ["", "## Gaps in the records", ""] + [f"- {gap}" for gap in records.gaps]
    return "\n".join(lines) + "\n"


def write_report(root: Path, course: str, now: datetime | None = None) -> Path:
    """Write reports/report.md and return its path. Through a temporary file, so an editor or a
    second test run never sees half a report."""
    path = root / REPORT_DIR / REPORT_FILE
    path.parent.mkdir(exist_ok=True)
    partial = path.with_suffix(".md.partial")
    partial.write_text(render(collect(root), course, now), encoding="utf-8")
    partial.replace(path)
    return path
