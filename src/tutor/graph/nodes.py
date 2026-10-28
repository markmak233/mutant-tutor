"""The nodes of the generation graph: plain functions, state in, partial state out.

Nothing here imports langgraph (build.py wires the nodes and owns the interrupt), so every
node is tested and usable on its own. Only `plan_batch`, `generate` and `repair` call a
model; `validate` is the existing validator, unchanged, and is the only judge of whether an
exercise is good enough to save.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from tutor import llm
from tutor.graph import prompts
from tutor.graph.state import (
    BatchPlan,
    BatchState,
    ExerciseDraft,
    ItemState,
    MutantDraft,
    PlanItem,
)
from tutor.materials import cache
from tutor.schema import Manifest, SourceRef
from tutor.validation.checks import Status, ValidationReport, validate
from tutor.validation.exercise import load_exercise

DRAFT_SCHEMA = ExerciseDraft.model_json_schema()
PLAN_SCHEMA = BatchPlan.model_json_schema()
# Files of the exercise folder that data_files must not replace.
OWN_FILES = {"student.py", "test_student.py", "hints.md"}
REPO = Path(__file__).resolve().parents[3]
PLAN_RETRIES = 2  # extra plan calls when the plan breaks a rule checked here
_LOG_LOCK = threading.Lock()  # items run in parallel threads and share the batch log


class DraftError(Exception):
    """The reply could not be turned into exercise files (a format failure, not a
    validation failure)."""


@dataclass(frozen=True)
class Deps:
    """What the nodes need besides the state. `chat` and `check` are swapped in tests."""

    workspace: Path
    batch_dir: Path
    config: llm.LLMConfig
    max_rounds: int = 3  # repair rounds after the first draft
    item_token_budget: int = 400_000  # input + output tokens per item, all rounds
    context_chars: int = 120_000  # above this a lecture is sliced to the item's slides
    max_tokens: int = 32_000  # per reply
    # Off by default: with thinking on, GLM-5.3 (Flash and full) spent the whole 32k output
    # on reasoning and was cut off before the answer, 9-10 min per call (2026-10-28,
    # docs/design.md); thinking low passed in one round in about a minute.
    thinking: bool = False
    mode: str = "learn"  # the batch's mode: how much a question may explain
    plan_config: llm.LLMConfig | None = None  # the `plan` role; default: config
    chat: Callable = llm.chat
    check: Callable[[Path, Path], ValidationReport] = field(
        default=lambda exercise, solution: validate(load_exercise(exercise, solution)))
    # Called with (exercise id, node, update) after every node of an item, for progress.
    on_update: Callable[[str, str, dict], None] | None = None

    @property
    def drafts(self) -> Path:
        # Per batch: the same exercise id may be generated into two batches at once.
        return self.workspace / "batches" / ".drafts" / self.batch_dir.name

    @property
    def llm_log(self) -> Path:
        return self.workspace / "logs" / "llm_calls.jsonl"

    @property
    def generation_log(self) -> Path:
        return self.batch_dir / "generation.jsonl"


# --- retrieve_context -------------------------------------------------------------------

def retrieve_context(state: ItemState, deps: Deps) -> ItemState:
    """The lecture's cached pages, whole if they fit, else the item's slides only."""
    item = state["item"]
    path = cache.cache_path(deps.workspace, deps.workspace / "lectures" / f"{item.lecture}.pdf")
    entries = sorted(cache.load(path).values(), key=lambda e: e.number)
    if not entries:
        raise FileNotFoundError(f"no cached pages for {item.lecture}: run python -m "
                                f"tutor.materials --vision lectures")
    if sum(len(e.body) for e in entries) > deps.context_chars:
        low, high = _slide_range(item.slides)
        entries = [e for e in entries if e.label and low <= _slide(e.label) <= high]
    return {"context": "\n\n".join(_section(e) for e in entries)}


def _section(e: cache.Entry) -> str:
    return f"## p.{e.number}" + (f" · slide {e.label}" if e.label else "") + f"\n{e.body}"


def _slide(label: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", label))


def _slide_range(slides: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    low, _, high = slides.partition("-")
    return _slide(low), _slide(high or low)


# --- generate / repair ------------------------------------------------------------------

def generate(state: ItemState, deps: Deps) -> ItemState:
    item = state["item"]
    user = prompts.item_prompt(item, state["context"], deps.mode)
    return _ask(state, deps, user, "generate", 0)


def repair(state: ItemState, deps: Deps) -> ItemState:
    item = state["item"]
    if state.get("draft") is None:
        # Nothing usable came back (cut off while thinking, or not JSON): showing that
        # reply would only lengthen the prompt, so the item is asked again.
        user = prompts.retry_prompt(item, state["context"], state["failure"] or "", deps.mode)
    else:
        user = prompts.repair_prompt(item, state["context"], state.get("draft_json", ""),
                                     state["failure"] or "", deps.mode)
    return _ask(state, deps, user, "repair", state.get("round", 0) + 1)


def _ask(state: ItemState, deps: Deps, user: str, purpose: str, round_: int) -> ItemState:
    item = state["item"]
    conventions = deps.workspace / "conventions.md"
    messages = [
        {"role": "system", "content": prompts.system_prompt(
            item, conventions.read_text() if conventions.is_file() else "")},
        {"role": "user", "content": user},
    ]
    reply = deps.chat(deps.config, messages, purpose=purpose, log_path=deps.llm_log,
                      max_tokens=deps.max_tokens, response_schema=DRAFT_SCHEMA,
                      thinking=deps.thinking)
    tokens = state.get("tokens", 0) + (reply.input_tokens or 0) + (reply.output_tokens or 0)
    update: ItemState = {"round": round_, "tokens": tokens, "draft_json": reply.text}
    try:
        update.update(draft=ExerciseDraft.model_validate_json(reply.text), failure=None,
                      format_error=False)
    except ValidationError as e:
        cut = " (the reply was cut off at max_tokens)" if reply.finish_reason == "length" else ""
        update.update(draft=None, format_error=True,
                      failure=f"format: the reply does not fit the JSON schema{cut}: "
                              + "; ".join(_describe(err) for err in e.errors()[:10]))
    return update


def _describe(err: dict) -> str:
    where = ".".join(str(part) for part in err["loc"]) or "(top level)"
    return f"{where}: {err['msg']}"


# --- validate ---------------------------------------------------------------------------

def validate_draft(state: ItemState, deps: Deps) -> ItemState:
    """Write the draft out and run the validator. The failure shown to repair lists the
    failing rules only, not the passing ones."""
    item, round_ = state["item"], state.get("round", 0)
    attempts = list(state.get("attempts", []))
    failure, format_error, round_dir = state.get("failure"), state.get("format_error", False), ""
    if state.get("draft") is not None:
        round_dir_path = deps.drafts / item.id / f"round-{round_}"
        try:
            exercise, solution = write_draft(state["draft"], item.id, round_dir_path)
        except DraftError as e:
            failure, format_error = f"format: {e}", True
        else:
            round_dir = str(round_dir_path)
            report = deps.check(exercise, solution)
            (round_dir_path / "report.txt").write_text(_report_text(report))
            failure = None if report.valid else "\n".join(
                f"rule {r.rule} ({r.name}) FAIL: {r.detail}"
                for r in report.results if r.status is Status.FAIL)
    attempts.append({"round": round_, "failure": failure, "format_error": format_error})
    return {"failure": failure, "format_error": format_error, "attempts": attempts,
            "round_dir": round_dir}


def _report_text(report: ValidationReport) -> str:
    return "\n".join(f"{r.rule:>4} {r.status.value:<5} {r.name}: {r.detail}"
                     for r in report.results) + ("\nVALID\n" if report.valid else "\nINVALID\n")


def write_draft(draft: ExerciseDraft, exercise_id: str, root: Path) -> tuple[Path, Path]:
    """root/exercises/<id> and root/solutions/<id>, laid out like a batch."""
    if draft.metadata.id != exercise_id:
        raise DraftError(f"metadata.id is {draft.metadata.id!r}, the plan names {exercise_id!r}")
    if draft.metadata.source == "synthetic":
        # The example exercise says synthetic, and a model copies it; a generated exercise
        # always comes from course pages, which it must cite.
        raise DraftError('metadata.source is "synthetic": give the lecture, slides and PDF '
                         "pages the exercise comes from")
    if root.exists():
        shutil.rmtree(root)
    exercise, solution = root / "exercises" / exercise_id, root / "solutions" / exercise_id
    files = {
        exercise / "student.py": draft.student_py,
        exercise / "test_student.py": draft.test_student_py,
        exercise / "hints.md": draft.hints_md,
        solution / "metadata.json": draft.metadata.model_dump_json(
            indent=2, exclude_unset=True, exclude_none=True) + "\n",
        solution / "reference.py": draft.reference_py,
        solution / "skeleton.py": draft.student_py,  # what the student starts from
        solution / "oracle.py": draft.oracle_py,
        solution / "test_oracle.py": draft.test_oracle_py,
    }
    for name, content in draft.data_files.items():
        files[exercise / _data_path(name)] = content
    problems = []  # every mutant that cannot be applied, so one repair round fixes them all
    for mutant in draft.mutants:
        try:
            files[solution / "mutants" / f"{mutant.name}.py"] = apply_mutant(
                draft.reference_py, mutant)
        except DraftError as e:
            problems.append(str(e))
    if problems:
        raise DraftError("\n".join(problems))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n")
    return exercise, solution


def _data_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or not path.parts or name.startswith("~")
            or any(part.startswith(".") for part in path.parts) or name in OWN_FILES
            or path.suffix == ".py"):
        raise DraftError(f"data file {name!r}: give a relative path inside the exercise "
                         "folder, not a Python file or one of student.py / test_student.py "
                         "/ hints.md")
    return path


def _closest(code: str, old: str) -> str:
    """The reference line most like the first line of `old`, copied exactly: a model's
    `old` often differs only in quotes or indentation, and repair needs the real text."""
    lines = [line for line in code.splitlines() if line.strip()]
    first = old.strip().splitlines()[0] if old.strip() else old
    match = difflib.get_close_matches(first.strip(), [line.strip() for line in lines], n=1,
                                      cutoff=0.5)
    if not match:
        return ""
    exact = next(line for line in lines if line.strip() == match[0])
    return f"; the closest line in reference.py is, exactly: {exact!r}"


def apply_mutant(reference: str, mutant: MutantDraft) -> str:
    """The reference with the mutant's replacements, and its docstring saying the bug."""
    code = reference
    for r in mutant.replacements:
        count = code.count(r.old)
        if count != 1:
            raise DraftError(f"mutant {mutant.name}: {r.old!r} occurs {count} times in "
                             "reference.py; `old` must be exact text that occurs once"
                             + _closest(code, r.old))
        code = code.replace(r.old, r.new)
    if code == reference:
        raise DraftError(f"mutant {mutant.name} changes nothing")
    doc = f'"""Mutant: {mutant.bug.rstrip(".")}."""'
    try:
        first = ast.parse(code).body[0]
    except (SyntaxError, IndexError):
        return f"{doc}\n{code}"
    lines = code.splitlines()
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        lines[first.lineno - 1:first.end_lineno] = [doc]  # replace the reference's docstring
        return "\n".join(lines) + "\n"
    return f"{doc}\n{code}"


# --- routing, save, discard -------------------------------------------------------------

def route(state: ItemState, deps: Deps) -> str:
    if state.get("failure") is None:
        return "save"
    if state.get("round", 0) < deps.max_rounds and state.get("tokens", 0) < deps.item_token_budget:
        return "repair"
    return "discard"


def save(state: ItemState, deps: Deps) -> ItemState:
    """Copy the valid round into the batch. The manifest is written once per batch
    (write_manifest), not here: items run in parallel."""
    item, src = state["item"], Path(state["round_dir"])
    for part in ("exercises", "solutions"):
        target = deps.batch_dir / part / item.id
        if target.exists():
            raise FileExistsError(f"{target} exists; never overwrite an exercise")
        shutil.copytree(src / part / item.id, target)
    _log(state, deps, "saved")
    return {"outcome": "saved"}


def discard(state: ItemState, deps: Deps) -> ItemState:
    """The drafts stay in batches/.drafts/<batch>/<id>/ for a look at why it failed."""
    _log(state, deps, "discarded")
    return {"outcome": "discarded"}


def item_summary(item: PlanItem, final: ItemState | None, error: str | None = None) -> dict:
    """One item's result for the batch state (plain data, it is checkpointed)."""
    final = final or {}
    return {"id": item.id, "lecture": item.lecture, "slides": item.slides,
            "outcome": "error" if error else final.get("outcome", "discarded"),
            "rounds": final.get("round", 0), "tokens": final.get("tokens", 0),
            "last_failure": error or final.get("failure")}


def write_manifest(deps: Deps, items: list[PlanItem], notes: str | None = None,
                   model: str | None = None) -> None:
    """List `items` (saved exercises) in the batch manifest, creating it or adding to it.
    `model`: who generated them (default: the generate model)."""
    model = model or f"{deps.config.name}: {deps.config.model}"
    if not items:
        return
    path = deps.batch_dir / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    known = Manifest.model_validate_json(path.read_text()) if path.is_file() else None
    sources = list(known.sources) if known else []
    for item in items:
        source = SourceRef(path=f"lectures/{item.lecture}.pdf", slides=item.slides)
        if source not in sources:
            sources.append(source)
    if known:
        models = list(dict.fromkeys([*known.model.split("; "), *model.split("; ")]))
        manifest = known.model_copy(update={
            "exercises": [*known.exercises, *(i.id for i in items)], "sources": sources,
            "model": "; ".join(models)})
    else:
        manifest = Manifest(
            phase=3, date=date.today(), label=deps.batch_dir.name.split("_", 1)[-1],
            mode=deps.mode, sources=sources,
            method="generated by the phase 3 pipeline (python -m tutor.graph), checked by "
                   "python -m tutor.validation",
            model=model, git_commit=_git_commit(),
            exercises=[i.id for i in items], notes=notes or None)
    Manifest.model_validate(manifest.model_dump())  # the cross-field rules
    path.write_text(manifest.model_dump_json(indent=2, exclude_none=True) + "\n")


# --- the batch: plan, owner's decision, collect -----------------------------------------

def plan_batch(state: BatchState, deps: Deps) -> BatchState:
    """Ask the plan model for `count` items; check what code can check, and ask again
    (at most PLAN_RETRIES times) with the problems found."""
    lectures, count = state["lectures"], state.get("count")
    max_count = state.get("max_count", 6)
    coverage = deps.workspace / "coverage.md"
    inputs = dict(
        lectures=lectures, coverage=coverage.read_text() if coverage.is_file() else "(none)",
        pages="\n".join(page_titles(deps.workspace, lecture) for lecture in lectures),
        existing=existing_ids(deps.workspace), preferences=state.get("preferences", ""),
        count=count, max_count=max_count, feedback=state.get("feedback", ""),
        previous=json.dumps(state["plan"], indent=1) if state.get("plan") else "")
    config = deps.plan_config or deps.config
    problems: list[str] = []
    plan: list[dict] = []
    for _ in range(1 + PLAN_RETRIES):
        messages = [{"role": "system", "content": prompts.PLAN_SPEC},
                    {"role": "user", "content": prompts.plan_prompt(**inputs, problems=problems)}]
        reply = deps.chat(config, messages, purpose="plan", log_path=deps.llm_log,
                          max_tokens=deps.max_tokens, response_schema=PLAN_SCHEMA,
                          thinking=deps.thinking)
        try:
            items = BatchPlan.model_validate_json(reply.text).items
        except ValidationError as e:
            problems = ["the reply does not fit the JSON schema: "
                        + "; ".join(_describe(err) for err in e.errors()[:10])]
            continue
        plan = [i.model_dump() for i in items]
        problems = plan_problems(items, lectures, count, inputs["existing"], max_count)
        if not problems:
            break
        inputs["previous"] = json.dumps(plan, indent=1)
    return {"plan": plan, "plan_problems": problems, "feedback": "", "approved": False}


def plan_problems(items: list[PlanItem], lectures: list[str], count: int | None,
                  existing: list[str], max_count: int = 6) -> list[str]:
    """`count` None: the plan chooses how many, up to max_count."""
    found = []
    if count and len(items) != count:
        found.append(f"{len(items)} items, {count} asked for")
    if not count and len(items) > max_count:
        found.append(f"{len(items)} items, at most {max_count}: merge or drop the least "
                     "important")
    ids = [i.id for i in items]
    found += [f"id {i!r} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    found += [f"id {i!r} already exists in another batch" for i in ids if i in existing]
    found += [f"{i.id}: lecture {i.lecture!r} is not one of {lectures}"
              for i in items if i.lecture not in lectures]
    found += [f"{i.id}: slides {i.slides!r} is not a range of slide footers like 4.15-4.24"
              for i in items if not re.fullmatch(r"\d+(\.\d+)*(-\d+(\.\d+)*)?", i.slides)]
    return found


def apply_decision(state: BatchState, decision: dict) -> BatchState:
    """The owner's answer to the plan: approve (with the plan as they may have edited it)
    or send it back with feedback."""
    if decision.get("action") == "approve":
        plan = decision.get("plan", state["plan"])
        BatchPlan.model_validate({"items": plan})  # an edited plan must still fit
        return {"plan": plan, "approved": True}
    return {"feedback": decision.get("feedback", ""), "approved": False}


def collect(state: BatchState, deps: Deps) -> BatchState:
    """After all items: the manifest, and the coverage.md changes proposed to the owner."""
    saved = [PlanItem.model_validate(p) for p in state["plan"]
             if any(r["id"] == p["id"] and r["outcome"] == "saved" for r in state["results"])]
    write_manifest(deps, saved, notes=("Owner's preferences: " + state["preferences"])
                   if state.get("preferences") else None,
                   model=_models_of(deps, {i.id for i in saved}))
    if saved:
        label = deps.batch_dir.name.split("_", 1)[-1]
        lines = ["# Proposed coverage.md changes", "",
                 "Generated exercises; mark their knowledge points in coverage.md once the "
                 "owner has looked at them (not applied automatically).", ""]
        lines += [f"- {i.lecture}, slides {i.slides} ({i.knowledge_point}): gap-code -> "
                  f"ex:{i.id} ({label})" for i in saved]
        (deps.batch_dir / "coverage-proposal.md").write_text("\n".join(lines) + "\n")
    return {}


def _models_of(deps: Deps, ids: set[str]) -> str | None:
    """The models that generated the saved `ids`, from the batch log: a batch resumed with
    another --profile mixes models, and the manifest names them all."""
    if not deps.generation_log.is_file():
        return None
    lines = [json.loads(line) for line in deps.generation_log.read_text().splitlines()]
    models = [x["model"] for x in lines if x["exercise"] in ids and x["outcome"] == "saved"]
    return "; ".join(dict.fromkeys(models)) or None


def page_titles(workspace: Path, lecture: str) -> str:
    """One line per cached page: PDF page, slide footer and its first line of text."""
    path = cache.cache_path(workspace, workspace / "lectures" / f"{lecture}.pdf")
    lines = [f"{lecture}:"]
    for e in sorted(cache.load(path).values(), key=lambda e: e.number):
        title = next((t for t in (line.strip().strip("#").strip() for line in e.body.splitlines())
                      if t and t != e.label and not re.fullmatch(r"[\d.]+", t)), "")
        lines.append(f"  p.{e.number} · slide {e.label or '?'}: {title[:80]}")
    return "\n".join(lines)


def existing_ids(workspace: Path) -> list[str]:
    batches = workspace / "batches"
    return sorted({p.name for p in batches.glob("*/exercises/*")
                   if p.is_dir() and not p.name.startswith((".", "_"))})


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def log_error(state: ItemState, deps: Deps, error: str) -> None:
    """An item stopped by an exception (the others go on): logged like the outcomes."""
    _log(state, deps, "error", error)


def _log(state: ItemState, deps: Deps, outcome: str, error: str | None = None) -> None:
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "exercise": state["item"].id,
        "outcome": outcome,
        "rounds": state.get("round", 0),
        "format_errors": sum(a["format_error"] for a in state.get("attempts", [])),
        "attempts": state.get("attempts", []),
        "tokens": state.get("tokens", 0),
        "model": f"{deps.config.name}: {deps.config.model}",
    }
    if error:
        line["error"] = error
    with _LOG_LOCK:
        deps.generation_log.parent.mkdir(parents=True, exist_ok=True)
        with deps.generation_log.open("a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
