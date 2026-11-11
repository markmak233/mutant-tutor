"""The item's own nodes of the generation graph: plain functions, state in, partial state out.

One item goes retrieve_context -> generate -> write_oracle (oracle.py) -> validate_draft
(drafts.py), then route sends it to save, repair (back to write_oracle) or discard; a batch
is plan_batch, the owner's apply_decision, the items, then collect (batch.py).
notes/langgraph.md walks through it.

This module holds what an item's run shares: Deps (what every node needs besides the state),
the calls to the generating model (generate, repair) and the token counts they add up, the
routing after validation, save and discard, and the batch log. Nothing in the graph's
modules imports langgraph (build.py wires the nodes and owns the interrupt), so every node is
tested and usable on its own. Only `plan_batch`, `generate` and `repair` call a model, and
`write_oracle` calls a second one; `validate` is the existing validator, unchanged, and is
the only judge of whether an exercise is good enough to save.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import threading
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from tutor import llm
from tutor.graph import prompts
from tutor.graph.languages import PROFILES, PYTHON, Profile
from tutor.graph.settings import DEFAULTS, DEPS_FIELDS
from tutor.graph.state import ItemState
from tutor.materials import cache
from tutor.question import write_question
from tutor.testkit_c import DEFAULT_FLAGS, course_flags
from tutor.validation.checks import RuleResult, Status, ValidationReport, validate
from tutor.validation.exercise import load_exercise
from tutor.validation.safety import workspace_allow_headers, workspace_allow_imports

# The JSON Schemas sent as structured output, one per kind of reply; the replies are
# parsed back into the same Pydantic models (state.py). A C course's plan and draft use its
# own (graph/languages.py); these are Python's.
DRAFT_SCHEMA = PYTHON.draft_schema
PLAN_SCHEMA = PYTHON.plan_schema
UNREAD = ("pending", "partial")  # cached pages whose transcription is missing or cut off
_LOG_LOCK = threading.Lock()  # items run in parallel threads and share the batch log


def check_round(exercise: Path, solution: Path) -> ValidationReport:
    """The validator on one round's files; rule 4 runs even when rule 1 fails, since which
    side the oracle agrees with tells repair what to fix."""
    return validate(load_exercise(exercise, solution), oracle_on_failure=True)


# How long an item waits for the lecture's first generate call before it goes on its own
# (uncached): the first calls seen take 70-360 s; one that hangs (an Anthropic stream
# gave nothing for 900 s, 2026-11-11) must not hold the whole lecture.
GATE_WAIT_S = 600
# When a provider's prompt cache can be read by the next call, in seconds after the first
# call's reply begins to stream; a provider not here opens the gate only when its first
# call has returned. Measured 2026-11-11 (a 33k / 23k-token shared prefix, three calls
# released at once, their cached share): anthropic 99.9% at the first streamed event
# (the docs: the entry is readable once the response begins streaming); deepseek 0% at the
# first event (0.6 s) but 99% from 3 s on (its cache is built in the background), so a
# margin; glm 0% at 30 s and 99.9% only after the first call ended. OpenAI not measured.
CACHE_READY_S: dict[str, float] = {"anthropic": 0.0, "deepseek": 10.0}


class CacheGate:
    """The first generate call of each lecture goes alone; the lecture's other items wait
    for it, then go in parallel. The call's shared part (the lecture's course pages, the
    longest part of the prompt) is then in the provider's prompt cache for every call
    after the first; with all of them sent at once each writes it for itself and none
    reads it (Spark course, Sonnet 5.5, parallel 8, 2026-11-11: the first eight calls hit
    0 tokens, the next four 15,647 each, about a dollar of the batch's three). The others
    go as soon as the provider can serve the cache, which is before the first call ends
    where CACHE_READY_S says so (its first reply's thinking and output take a minute or
    more): `turn` yields a `release` for that moment, and stream_args calls it at the
    reply's first streamed event (llm.py, llm_anthropic.py: `on_progress(0)`), at once or
    after the provider's margin. A reply that does not stream, a provider not in the
    table, or a call that fails or raises, frees the others when it returns. Repair rounds
    do not wait: by then the item's own first call has filled the cache. A first call that
    outlasts GATE_WAIT_S is not waited for any longer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # lecture -> set when its first call's stream has begun (or the call has ended)
        self._first: dict[str, threading.Event] = {}

    @contextmanager
    def turn(self, key: str):
        """Yields the gate's `release()`: for the first call of `key` it lets the others go
        (called again, nothing happens); for every other call it is nothing to call."""
        with self._lock:
            event = self._first.get(key)
            first = event is None
            if first:
                event = self._first[key] = threading.Event()
        if not first:
            event.wait(GATE_WAIT_S)
        try:
            yield event.set if first else _nothing
        finally:
            if first:
                event.set()


def _nothing() -> None:
    """A follower's `release`: its lecture's gate is not its to open."""


@dataclass(frozen=True)
class Deps:
    """What the nodes need besides the state. `chat` and `check` are swapped in tests."""

    workspace: Path
    batch_dir: Path
    config: llm.LLMConfig
    # The tunable values: their defaults, meaning and the measurements behind them live in
    # graph/settings.py (GenerationSettings); the CLI passes the resolved ones in.
    max_rounds: int = DEFAULTS.max_rounds
    item_token_budget: int = DEFAULTS.item_token_budget
    context_chars: int = DEFAULTS.context_chars
    max_tokens: int = DEFAULTS.max_tokens
    max_tokens_no_thinking: int = DEFAULTS.max_tokens_no_thinking
    thinking: bool = DEFAULTS.thinking
    mode: str = DEFAULTS.mode
    hide_answers: bool = DEFAULTS.hide_answers
    repair_temperature_step: float = DEFAULTS.repair_temperature_step
    plan_thinking: bool = DEFAULTS.plan_thinking
    stronger_repair_from: int = DEFAULTS.stronger_repair_from
    repair_thinking: bool = DEFAULTS.repair_thinking
    oracle_thinking: bool = DEFAULTS.oracle_thinking
    concept_lines: int = DEFAULTS.concept_lines
    # The course's language (graph/languages.py): "c" makes every exercise of the batch C.
    language: str = "python"
    plan_config: llm.LLMConfig | None = None  # the `plan` role; default: config
    # The `repair` role: a larger model for the repair rounds from `stronger_repair_from` on,
    # where the cheap model fails most (it cannot tell which side of a failure is wrong).
    # None: the generating model repairs too.
    repair_config: llm.LLMConfig | None = None
    # The `oracle` role: another model family writes oracle.py from the question alone.
    # None: the generating model writes the whole oracle, as hand drafting does.
    oracle_config: llm.LLMConfig | None = None
    chat: Callable = llm.chat
    check: Callable[[Path, Path], ValidationReport] = check_round
    # Called with (exercise id, node, update) after every node of an item, for progress.
    on_update: Callable[[str, str, dict], None] | None = None
    # Exercise replies arrive streamed: a reply that keeps coming is never cut off by the
    # timeout, and on_stream gets (exercise id, characters so far) while it arrives.
    stream: bool = True
    on_stream: Callable[[str, int], None] | None = None
    # One per batch: the first generate call of each lecture goes alone, so the others
    # read its course pages from the prompt cache (CacheGate above).
    cache_gate: CacheGate = field(default_factory=CacheGate)

    @property
    def profile(self) -> Profile:
        return PROFILES[self.language]

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
    """The lecture's cached pages, whole if they fit, else the item's slides only.

    A page of the item's slides that is not fully read (pending: its image is not
    transcribed and the text layer stands in; partial: cut off) stops the item, naming the
    pages: a formula or diagram may be missing, and no test can show the question is
    faithful to the slide. The item's pages run from its first slide's PDF page to its
    last, so a page without a footer between them counts; in a lecture without footers
    every page does. Such pages elsewhere in the lecture are left out."""
    item = state["item"]
    path = cache.lecture_cache(deps.workspace, item.lecture)
    entries = sorted(cache.load(path).values(), key=lambda e: e.number)
    if not entries:
        raise FileNotFoundError(f"no cached pages for {item.lecture}: run python -m "
                                f"tutor.materials --vision lectures")
    # The item's own pages: those whose slide footer falls in its range ("4.15-4.24").
    low, high = _slide_range(item.slides)
    slides = [e for e in entries if e.label and low <= _slide(e.label) <= high]
    if not slides and all(len(_slide(e.label)) == 1 for e in entries if e.label):
        # Footers that are single numbers ("17") against a range the plan model wrote with
        # the lecture's number in front ("4.7-4.9", after the prompt's example; the
        # deep-learning course, 2026-11-11: five items of a week found no page): the last
        # number of each end is the footer meant. A lecture whose footers carry a dot (the
        # Spark course's "4.15") is not here.
        low, high = low[-1:], high[-1:]
        slides = [e for e in entries if e.label and low <= _slide(e.label) <= high]
    too_long = sum(len(e.body) for e in entries) > deps.context_chars
    # A lecture without slide footers cannot be checked, only sent whole.
    if not slides and (too_long or any(e.label for e in entries)):
        raise ValueError(f"no page of {item.lecture} has a slide footer in {item.slides}")
    pages = ([e for e in entries if slides[0].number <= e.number <= slides[-1].number]
             if slides else entries)
    if unread := [e.number for e in pages if e.read in UNREAD]:
        raise ValueError(f"{item.lecture} pages {unread} (slides {item.slides}) are not fully "
                         f"read: run python -m tutor.materials --vision lectures")
    # Whole lecture when it fits (the model sees what comes before and after); else only
    # the item's pages. Unread pages elsewhere are left out rather than sent half-read.
    chosen = pages if too_long else [e for e in entries if e.read not in UNREAD]
    return {"context": "\n\n".join(_section(e) for e in chosen)}


def _section(e: cache.Entry) -> str:
    """One page as the model sees it: "## p.12 · slide 2.10", then its text. The generate
    prompt tells the model to cite the p.N and slide S it uses."""
    return f"## p.{e.number}" + (f" · slide {e.label}" if e.label else "") + f"\n{e.body}"


def _slide(label: str) -> tuple[int, ...]:
    """"4.15" -> (4, 15): compared as numbers, so 4.9 < 4.15, unlike the strings."""
    return tuple(int(part) for part in re.findall(r"\d+", label))


def _slide_range(slides: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """"4.15-4.24" -> ((4, 15), (4, 24)); a single slide is a range of one."""
    low, _, high = slides.partition("-")
    return _slide(low), _slide(high or low)


# --- generate / repair ------------------------------------------------------------------

def generate(state: ItemState, deps: Deps) -> ItemState:
    """Round 0: the first draft of the item's exercise, from its plan line and the course
    pages."""
    item = state["item"]
    user = prompts.item_prompt(item, state["context"], deps.mode, deps.language)
    return _ask(state, deps, user, "generate", 0)


def repair(state: ItemState, deps: Deps) -> ItemState:
    """Rounds 1..max_rounds: the draft again, told what failed. From round
    `stronger_repair_from` the `repair` role's (larger) model takes over, if one is set."""
    item = state["item"]
    if state.get("draft") is None:
        # Nothing usable came back (cut off while thinking, or not JSON): showing that
        # reply would only lengthen the prompt, so the item is asked again.
        user = prompts.retry_prompt(item, state["context"], state["failure"] or "", deps.mode,
                                    deps.language)
    else:
        user = prompts.repair_prompt(item, state["context"], state.get("draft_json", ""),
                                     state["failure"] or "", deps.mode,
                                     unchanged=state.get("unchanged", False),
                                     language=deps.language)
    round_ = state.get("round", 0) + 1
    stronger = deps.repair_config is not None and round_ >= deps.stronger_repair_from
    if stronger:
        return _ask(state, deps, user, "repair", round_, deps.repair_config,
                    deps.repair_thinking)
    return _ask(state, deps, user, "repair", round_)


def _ask(state: ItemState, deps: Deps, user: str, purpose: str, round_: int,
         config: llm.LLMConfig | None = None, thinking: bool | None = None) -> ItemState:
    """One exercise call (generate or repair): the reply parsed into an ExerciseDraft, or
    a format failure for repair when it does not fit the schema."""
    config = config or deps.config
    thinking = deps.thinking if thinking is None else thinking
    conventions = deps.workspace / "conventions.md"
    c = deps.language == "c"
    messages = [
        {"role": "system", "content": prompts.system_prompt(
            conventions.read_text(encoding="utf-8") if conventions.is_file() else "",
            oracle_interface=deps.oracle_config is not None,
            concept_lines=deps.concept_lines,
            # What the course lets exercises import besides the core modules (the owner
            # ticks them on the Environment page), so the model knows it may.
            extra_imports=workspace_allow_imports(deps.workspace),
            language=deps.language,
            # A C course's compiler flags and extra headers, as its tests will use them.
            c_flags=(course_flags(deps.workspace) or list(DEFAULT_FLAGS)) if c else (),
            c_headers=workspace_allow_headers(deps.workspace) if c else ())},
        {"role": "user", "content": user},
    ]
    # A repair round asks a little warmer each time: at temperature 0 an unchanged prompt
    # got the identical draft back (graph/settings.py).
    # Round 0 of a lecture's items: the first call alone, the rest after it (CacheGate).
    # Round 0 takes the lecture's turn at the cache gate; its `release` opens the gate when
    # the provider's cache is readable (CACHE_READY_S after the reply's first streamed
    # event, through on_progress in stream_args), else when the call returns.
    gate = deps.cache_gate.turn(state["item"].lecture) if round_ == 0 else nullcontext(_nothing)
    with gate as release:
        reply = deps.chat(config, messages, purpose=purpose, log_path=deps.llm_log,
                          max_tokens=deps.max_tokens if thinking else deps.max_tokens_no_thinking,
                          response_schema=deps.profile.draft_schema,
                          thinking=thinking,
                          temperature=min(round_ * deps.repair_temperature_step, 0.8),
                          **stream_args(state, deps, release, CACHE_READY_S.get(config.provider)))
    # The draft is the JSON itself: a ```json fence or a sentence around it goes before
    # parsing and before it is kept (a chat model writes one now and then although the
    # prompt asks for JSON alone; Sonnet 5.5, 2026-11-11), so a repair round quotes JSON.
    text = llm.json_body(reply.text)
    update: ItemState = {**tokens_spent(state, reply), "round": round_, "draft_json": text,
                         "model": config.name,
                         "unchanged": round_ > 0 and text == state.get("draft_json", "").strip()}
    try:
        update.update(draft=deps.profile.draft.model_validate_json(text), failure=None,
                      format_error=False)
    except ValidationError as e:
        cut = " (the reply was cut off at max_tokens)" if reply.finish_reason == "length" else ""
        # A whole reply that is not JSON (and not merely cut short): its first characters
        # say what came instead (a sentence, a fence of another kind), as the round's text
        # goes to the next round's prompt but never to the batch's log.
        head = ""
        if not cut and any(err["loc"] == () for err in e.errors()):
            start = " ".join(reply.text[:80].split()) or "(empty)"
            head = f"; the reply begins: {start!r}"
        update.update(draft=None, format_error=True,
                      failure=f"format: the reply does not fit the JSON schema{cut}: "
                              + "; ".join(describe_error(err) for err in e.errors()[:10]) + head)
    return update


def stream_args(state: ItemState, deps: Deps, release: Callable[[], None] = _nothing,
                ready_s: float | None = None) -> dict:
    """The chat arguments for a streamed reply: progress reported per item, and the cache
    gate's `release` called `ready_s` after the reply's first event (the readers call
    on_progress(0) then, before any text; 0 means at once), so the lecture's other items
    go while this reply streams. `ready_s` None: the gate opens when the call returns."""
    item_id = state["item"].id
    begun = False

    def progress(chars: int) -> None:
        nonlocal begun
        if not begun:
            begun = True
            if ready_s == 0:
                release()
            elif ready_s is not None:
                timer = threading.Timer(ready_s, release)
                timer.daemon = True  # never holds the process; the gate opens at the end anyway
                timer.start()
        if deps.on_stream:
            deps.on_stream(item_id, chars)
    wanted = deps.on_stream is not None or (release is not _nothing and ready_s is not None)
    return {"stream": deps.stream, "on_progress": progress if wanted else None}


def tokens_spent(state: ItemState, reply: llm.Reply) -> ItemState:
    """The item's running token counts after `reply`: `tokens` (input + output, for the
    budget) and `usage`, which also counts the input served from the provider's cache."""
    before = state.get("usage", {"input": 0, "cached": 0, "output": 0})
    usage = {"input": before["input"] + (reply.input_tokens or 0),
             "cached": before["cached"] + (reply.cached_tokens or 0),
             "output": before["output"] + (reply.output_tokens or 0)}
    return {"tokens": usage["input"] + usage["output"], "usage": usage}


def describe_error(err: dict) -> str:
    """One Pydantic error as "field.path: message", for a failure text a model can act on."""
    where = ".".join(str(part) for part in err["loc"]) or "(top level)"
    return f"{where}: {err['msg']}"


# --- routing, save, discard -------------------------------------------------------------

def route(state: ItemState, deps: Deps) -> str:
    """After validation: save a VALID draft; else repair while rounds and the item's token
    budget last; else discard."""
    if state.get("failure") is None:
        return "save"
    if state.get("round", 0) < deps.max_rounds and state.get("tokens", 0) < deps.item_token_budget:
        return "repair"
    return "discard"


def save(state: ItemState, deps: Deps) -> ItemState:
    """Copy the valid round into the batch. The manifest is written once per batch
    (write_manifest), not here: items run in parallel.

    Both parts are copied into hidden staging folders first and renamed into place after,
    so a copy that fails leaves nothing a retry trips over. An item saved before whose
    result was lost (the batch stopped before recording it, and a resume ran it again)
    is saved already: both folders exist and the batch log says "saved". Its folders are
    never replaced, since the owner may be solving the exercise."""
    item, src = state["item"], Path(state["round_dir"])
    parts = [(src / part / item.id, deps.batch_dir / part / item.id)
             for part in ("exercises", "solutions")]
    if all(target.is_dir() for _, target in parts) and _saved_before(deps, item.id):
        return {"outcome": "saved"}
    for _, target in parts:
        if target.exists():
            raise FileExistsError(f"{target} exists; never overwrite an exercise")
    staged = []
    for source, target in parts:
        staging = target.with_name(f".{item.id}.saving")  # hidden: not an exercise id
        if staging.exists():
            shutil.rmtree(staging)  # left by a copy that failed
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging)
        student = staging / f"student{deps.profile.suffix}"
        if source.parent.name == "exercises" and student.is_file():
            # The question's "How to run" names the folder it will have from now on; the
            # line under its title comes from the exercise's metadata and the batch mode.
            metadata = json.loads(
                (src / "solutions" / item.id / "metadata.json").read_text(encoding="utf-8"))
            write_question(student.read_text(encoding="utf-8"), staging,
                           location=target, metadata=metadata, mode=deps.mode)
        staged.append((staging, target))
    for staging, target in staged:
        staging.rename(target)
    _log(state, deps, "saved")
    return {"outcome": "saved"}


def _saved_before(deps: Deps, exercise_id: str) -> bool:
    """The batch log records `exercise_id` as saved (by an earlier run of this batch)."""
    return any(line.get("exercise") == exercise_id and line.get("outcome") == "saved"
               for line in log_lines(deps.generation_log))


def log_lines(path: Path) -> list[dict]:
    """The batch log's records, in order; none when there is no file. A line that is not a
    JSON object is skipped with a warning, not fatal: a run killed while writing one (Stop
    after its grace time, a full disk) once would have stopped every later resume of the
    batch at the broken line (2026-11-10)."""
    if not path.is_file():
        return []
    found, bad = [], 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError:
            row = None
        if isinstance(row, dict):
            found.append(row)
        elif line.strip():
            bad += 1
    if bad:
        print(f"warning: {path.name}: {bad} unreadable line(s) left out", file=sys.stderr)
    return found


def discard(state: ItemState, deps: Deps) -> ItemState:
    """The drafts stay in batches/.drafts/<batch>/<id>/ for a look at why it failed."""
    _log(state, deps, "discarded")
    return {"outcome": "discarded"}


def reusing_reports(check: Callable[[Path, Path], ValidationReport],
                    folder: Path) -> Callable[[Path, Path], ValidationReport]:
    """`check`, remembering each report by the exact files it checked. In assistant mode an
    item runs again from its start after every answer (replay); without this, every
    earlier round's validation (minutes of Spark) would run again too."""
    def cached(exercise: Path, solution: Path) -> ValidationReport:
        digest = hashlib.sha256()
        for root in (exercise, solution):
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                # as_posix: the same files give the same key on every platform
                digest.update(path.relative_to(root).as_posix().encode() + b"\0"
                              + path.read_bytes())
        stored = folder / f"{digest.hexdigest()[:20]}.json"
        if stored.is_file():
            data = json.loads(stored.read_text(encoding="utf-8"))
            return ValidationReport(data["exercise_id"], [
                RuleResult(**{**r, "status": Status(r["status"])}) for r in data["results"]])
        report = check(exercise, solution)
        folder.mkdir(parents=True, exist_ok=True)
        stored.write_text(json.dumps({"exercise_id": report.exercise_id, "results": [
            {**asdict(r), "status": r.status.value} for r in report.results]}), encoding="utf-8")
        return report
    return cached


def model_label(config: llm.LLMConfig) -> str:
    """e.g. "deepseek: deepseek-v4-flash", or "cc: claude-code (assistant)" when a coding
    assistant answered (assistant mode), so a manifest or log says who wrote what."""
    label = f"{config.name}: {config.model}"
    return label + " (assistant)" if config.provider == "assistant" else label


def log_error(state: ItemState, deps: Deps, error: str) -> None:
    """An item stopped by an exception (the others go on): logged like the outcomes."""
    _log(state, deps, "error", error)


def _log(state: ItemState, deps: Deps, outcome: str, error: str | None = None) -> None:
    """One line per finished item in the batch's generation.jsonl: its outcome, every
    round's failure, tokens and models, for the phase 5 metrics and model comparisons."""
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "exercise": state["item"].id,
        "outcome": outcome,
        "rounds": state.get("round", 0),
        "format_errors": sum(a["format_error"] for a in state.get("attempts", [])),
        "attempts": state.get("attempts", []),
        "tokens": state.get("tokens", 0),
        "usage": state.get("usage", {"input": 0, "cached": 0, "output": 0}),
        "model": model_label(deps.config),
        # What this item ran with, so a comparison of runs can tell configurations apart.
        "settings": {name: getattr(deps, name) for name in DEPS_FIELDS},
    }
    if deps.oracle_config:
        line["oracle_model"] = model_label(deps.oracle_config)
    if deps.repair_config and any(a.get("model") == deps.repair_config.name
                                  for a in state.get("attempts", [])):
        line["repair_model"] = model_label(deps.repair_config)
    if error:
        line["error"] = error
    with _LOG_LOCK:
        deps.generation_log.parent.mkdir(parents=True, exist_ok=True)
        with deps.generation_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
