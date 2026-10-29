"""The nodes of the generation graph: plain functions, state in, partial state out.

Nothing here imports langgraph (build.py wires the nodes and owns the interrupt), so every
node is tested and usable on its own. Only `plan_batch`, `generate` and `repair` call a
model, and `write_oracle` calls a second one; `validate` is the existing validator,
unchanged, and is the only judge of whether an exercise is good enough to save.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from tutor import llm
from tutor.graph import prompts
from tutor.graph.settings import DEFAULTS, DEPS_FIELDS
from tutor.graph.state import (
    MIN_MUTANTS,
    BatchPlan,
    BatchState,
    ExerciseDraft,
    ItemState,
    MutantDraft,
    OracleDraft,
    PlanItem,
)
from tutor.materials import cache
from tutor.schema import Manifest, SourceRef
from tutor.validation.checks import Status, ValidationReport, validate
from tutor.validation.exercise import load_exercise
from tutor.validation.safety import CORE_IMPORTS, find_hazards, workspace_allow_imports

DRAFT_SCHEMA = ExerciseDraft.model_json_schema()
PLAN_SCHEMA = BatchPlan.model_json_schema()
ORACLE_SCHEMA = OracleDraft.model_json_schema()
# Files of the exercise folder that data_files must not replace.
OWN_FILES = {"student.py", "test_student.py", "hints.md"}
REPO = Path(__file__).resolve().parents[3]
PLAN_RETRIES = 2  # extra plan calls when the plan breaks a rule checked here
UNREAD = ("pending", "partial")  # cached pages whose transcription is missing or cut off
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
    # The tunable values: their defaults, meaning and the measurements behind them live in
    # graph/settings.py (GenerationSettings); the CLI passes the resolved ones in.
    max_rounds: int = DEFAULTS.max_rounds
    item_token_budget: int = DEFAULTS.item_token_budget
    context_chars: int = DEFAULTS.context_chars
    max_tokens: int = DEFAULTS.max_tokens
    max_tokens_no_thinking: int = DEFAULTS.max_tokens_no_thinking
    thinking: bool = DEFAULTS.thinking
    mode: str = DEFAULTS.mode
    repair_temperature_step: float = DEFAULTS.repair_temperature_step
    plan_thinking: bool = DEFAULTS.plan_thinking
    stronger_repair_from: int = DEFAULTS.stronger_repair_from
    repair_thinking: bool = DEFAULTS.repair_thinking
    plan_config: llm.LLMConfig | None = None  # the `plan` role; default: config
    # The `repair` role: a larger model for the repair rounds from `stronger_repair_from` on,
    # where the cheap model fails most (it cannot tell which side of a failure is wrong).
    # None: the generating model repairs too.
    repair_config: llm.LLMConfig | None = None
    # The `oracle` role: another model family writes oracle.py from the question alone.
    # None: the generating model writes the whole oracle, as hand drafting does.
    oracle_config: llm.LLMConfig | None = None
    chat: Callable = llm.chat
    check: Callable[[Path, Path], ValidationReport] = field(
        default=lambda exercise, solution: validate(load_exercise(exercise, solution),
                                                    oracle_on_failure=True))
    # Called with (exercise id, node, update) after every node of an item, for progress.
    on_update: Callable[[str, str, dict], None] | None = None
    # Exercise replies arrive streamed: a reply that keeps coming is never cut off by the
    # timeout, and on_stream gets (exercise id, characters so far) while it arrives.
    stream: bool = True
    on_stream: Callable[[str, int], None] | None = None

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
    low, high = _slide_range(item.slides)
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
    chosen = pages if too_long else [e for e in entries if e.read not in UNREAD]
    return {"context": "\n\n".join(_section(e) for e in chosen)}


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
                                     state["failure"] or "", deps.mode,
                                     unchanged=state.get("unchanged", False))
    round_ = state.get("round", 0) + 1
    stronger = deps.repair_config is not None and round_ >= deps.stronger_repair_from
    if stronger:
        return _ask(state, deps, user, "repair", round_, deps.repair_config,
                    deps.repair_thinking)
    return _ask(state, deps, user, "repair", round_)


def _ask(state: ItemState, deps: Deps, user: str, purpose: str, round_: int,
         config: llm.LLMConfig | None = None, thinking: bool | None = None) -> ItemState:
    config = config or deps.config
    thinking = deps.thinking if thinking is None else thinking
    conventions = deps.workspace / "conventions.md"
    messages = [
        {"role": "system", "content": prompts.system_prompt(
            conventions.read_text() if conventions.is_file() else "",
            oracle_interface=deps.oracle_config is not None)},
        {"role": "user", "content": user},
    ]
    reply = deps.chat(config, messages, purpose=purpose, log_path=deps.llm_log,
                      max_tokens=deps.max_tokens if thinking else deps.max_tokens_no_thinking,
                      response_schema=DRAFT_SCHEMA,
                      thinking=thinking,
                      temperature=min(round_ * deps.repair_temperature_step, 0.8),
                      **_streaming(state, deps))
    update: ItemState = {**_spent(state, reply), "round": round_, "draft_json": reply.text,
                         "model": config.name,
                         "unchanged": round_ > 0 and reply.text.strip()
                         == state.get("draft_json", "").strip()}
    try:
        update.update(draft=ExerciseDraft.model_validate_json(reply.text), failure=None,
                      format_error=False)
    except ValidationError as e:
        cut = " (the reply was cut off at max_tokens)" if reply.finish_reason == "length" else ""
        update.update(draft=None, format_error=True,
                      failure=f"format: the reply does not fit the JSON schema{cut}: "
                              + "; ".join(_describe(err) for err in e.errors()[:10]))
    return update


def _streaming(state: ItemState, deps: Deps) -> dict:
    item_id = state["item"].id
    progress = (lambda chars: deps.on_stream(item_id, chars)) if deps.on_stream else None
    return {"stream": deps.stream, "on_progress": progress}


def _spent(state: ItemState, reply: llm.Reply) -> ItemState:
    """The item's running token counts after `reply`: `tokens` (input + output, for the
    budget) and `usage`, which also counts the input served from the provider's cache."""
    before = state.get("usage", {"input": 0, "cached": 0, "output": 0})
    usage = {"input": before["input"] + (reply.input_tokens or 0),
             "cached": before["cached"] + (reply.cached_tokens or 0),
             "output": before["output"] + (reply.output_tokens or 0)}
    return {"tokens": usage["input"] + usage["output"], "usage": usage}


def _describe(err: dict) -> str:
    where = ".".join(str(part) for part in err["loc"]) or "(top level)"
    return f"{where}: {err['msg']}"


# --- write_oracle -----------------------------------------------------------------------

def write_oracle(state: ItemState, deps: Deps) -> ItemState:
    """Fill in the draft's oracle interface with the oracle model, which sees the question,
    the interface and the sample data, never the reference, tests or mutants. It is written
    again only when one of those changed, so a repair that fixes something else keeps it.
    A problem goes to repair like a validation failure: the question or the interface did
    not tell the oracle model enough, and the generating model can fix either."""
    draft = state.get("draft")
    if deps.oracle_config is None:
        return {}
    if draft is None:  # the reply could not be read; repair asks again
        return {"oracle_status": "skipped"}
    question = _docstring(draft.student_py)
    interface = draft.oracle_py
    problems = _interface_problems(interface)
    if problems and all(p.endswith(_HAS_BODY) for p in problems):
        # A whole oracle where an interface was asked for (DeepSeek-V4-Pro did it on every
        # first draft of a batch): keep the signatures and docstrings, drop the bodies. The
        # oracle model never sees the bodies, so its reading stays independent.
        interface, problems = _as_interface(interface), []
    if not question:
        problems.insert(0, "student.py has no module docstring: the question goes there")
    if problems:
        return _oracle_failed(state, "oracle_py must be an interface for another model to "
                              "fill in: " + "; ".join(problems))
    key = hashlib.sha256(json.dumps(
        [question, interface, draft.data_files], sort_keys=True).encode()).hexdigest()
    if key == state.get("oracle_key") and state.get("oracle_py"):
        return {"draft": draft.model_copy(update={"oracle_py": state["oracle_py"]}),
                "oracle_status": "kept"}
    messages = [{"role": "system", "content": prompts.oracle_spec()},
                {"role": "user", "content": prompts.oracle_prompt(
                    question, interface, draft.data_files)}]
    allowed = CORE_IMPORTS | workspace_allow_imports(deps.workspace)
    spent: ItemState = {}
    for _ in range(1 + ORACLE_RETRIES):
        reply = deps.chat(deps.oracle_config, messages, purpose="oracle",
                          log_path=deps.llm_log, max_tokens=deps.max_tokens,
                          response_schema=ORACLE_SCHEMA, thinking=deps.thinking,
                          **_streaming(state, deps))
        spent = _spent({**state, **spent}, reply)
        try:
            code = OracleDraft.model_validate_json(reply.text).oracle_py
        except ValidationError as e:
            problem = "its reply does not fit the JSON schema: " + "; ".join(
                _describe(err) for err in e.errors()[:5])
            continue
        problem = _filled_problems(interface, code) or "; ".join(find_hazards(code, allowed))
        if not problem:
            return {"draft": draft.model_copy(update={"oracle_py": code}), **spent,
                    "oracle_key": key, "oracle_py": code, "oracle_status": "written"}
        # The oracle model's own mistake (an import outside the allowlist, a function left
        # unwritten): it fixes it, since the generating model cannot change its code.
        messages = [*messages[:2], {"role": "assistant", "content": reply.text},
                    {"role": "user", "content": f"That oracle.py has problems: {problem}. "
                                                "Return it again with them fixed."}]
    return {**_oracle_failed(state, f"the oracle model could not implement oracle_py: "
                             f"{problem}. Make the interface's docstrings and the question "
                             "say exactly what each function takes and returns."),
            **spent}


def after_oracle(state: ItemState, deps: Deps) -> str:
    """Validate the draft, unless the oracle could not be written: then the round already
    failed, and validating the unfilled interface would only replace that failure."""
    return route(state, deps) if state.get("oracle_status") == "failed" else "validate"


def _oracle_failed(state: ItemState, failure: str) -> ItemState:
    """Not written: shown to repair as a format failure of this round."""
    attempts = [*state.get("attempts", []), {
        "round": state.get("round", 0), "failure": f"format: {failure}",
        "format_error": True, "dropped": [], "unchanged": state.get("unchanged", False),
        "model": state.get("model", "")}]
    return {"failure": f"format: {failure}", "format_error": True, "attempts": attempts,
            "oracle_status": "failed"}


def _docstring(code: str) -> str:
    try:
        return ast.get_docstring(ast.parse(code)) or ""
    except SyntaxError:
        return ""


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _is_stub(func: ast.FunctionDef) -> bool:
    body = func.body[1:] if ast.get_docstring(func) is not None else func.body
    return (len(body) == 1 and isinstance(body[0], ast.Raise)
            and "NotImplementedError" in ast.unparse(body[0]))


ORACLE_RETRIES = 1  # the oracle model's second try, after it was told what was wrong
_HAS_BODY = "has a body; give only its docstring and raise NotImplementedError"


def _as_interface(code: str) -> str:
    """`code` with every top-level function body replaced by its docstring and
    `raise NotImplementedError`; imports and constants stay."""
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not _is_stub(node):
            doc = ast.get_docstring(node)
            node.body = ([ast.Expr(ast.Constant(doc))] if doc is not None else []) + [
                ast.parse("raise NotImplementedError").body[0]]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def _interface_problems(code: str) -> list[str]:
    try:
        functions = _functions(ast.parse(code))
    except SyntaxError as e:
        return [f"it does not parse: {e.msg} (line {e.lineno})"]
    if not functions:
        return ["it defines no top-level function"]
    return [f"{name} {_HAS_BODY}" for name, func in functions.items() if not _is_stub(func)]


def _filled_problems(interface: str, code: str) -> str:
    try:
        written = _functions(ast.parse(code))
    except SyntaxError as e:
        return f"its oracle.py does not parse: {e.msg} (line {e.lineno})"
    wanted = _functions(ast.parse(interface))
    missing = sorted(set(wanted) - set(written))
    if missing:
        return "its oracle.py lacks " + ", ".join(missing)
    stubs = sorted(name for name in wanted if _is_stub(written[name]))
    return f"it left {', '.join(stubs)} unwritten" if stubs else ""


# --- validate ---------------------------------------------------------------------------

def validate_draft(state: ItemState, deps: Deps) -> ItemState:
    """Write the draft out and run the validator. The failure shown to repair lists the
    failing rules only, not the passing ones."""
    item, round_ = state["item"], state.get("round", 0)
    attempts = list(state.get("attempts", []))
    failure, format_error, round_dir = state.get("failure"), state.get("format_error", False), ""
    dropped: dict[str, str] = {}
    if state.get("draft") is not None:
        round_dir_path = deps.drafts / item.id / f"round-{round_}"
        try:
            exercise, solution, dropped = write_draft(state["draft"], item.id, round_dir_path)
        except DraftError as e:
            failure, format_error = f"format: {e}", True
        else:
            round_dir = str(round_dir_path)
            report = deps.check(exercise, solution)
            (round_dir_path / "report.txt").write_text(_report_text(report))
            failure = None if report.valid else "\n".join(
                f"rule {r.rule} ({r.name}) FAIL: {r.detail}"
                + ("\n" + textwrap.indent(r.excerpt, "    ") if r.excerpt else "")
                for r in report.results if r.status is Status.FAIL)
            status = {r.rule: r.status for r in report.results}
            disagrees = any(r.rule == "4" and r.detail.startswith("reference disagrees")
                            for r in report.results)  # not a test that cannot run
            if deps.oracle_config is not None and disagrees:
                failure += ORACLE_DISAGREES + state["draft"].oracle_py
            elif status.get("1") is Status.FAIL and status.get("4") is Status.OK:
                failure += ORACLE_AGREES.format(
                    oracle="oracle.py, which another model wrote from the question alone"
                    if deps.oracle_config is not None else "oracle.py")
            if failure and dropped:
                # Not a failure by itself (a VALID draft is saved without them), but a
                # repair round may as well fix them.
                failure += ("\nLeft out, could not be applied (fix them too):\n"
                            + "\n".join(dropped.values()))
    attempts.append({"round": round_, "failure": failure, "format_error": format_error,
                     "dropped": sorted(dropped), "unchanged": state.get("unchanged", False),
                     "model": state.get("model", deps.config.name),
                     "diagnosis": state["draft"].diagnosis if state.get("draft") else ""})
    return {"failure": failure, "format_error": format_error, "attempts": attempts,
            "round_dir": round_dir}


ORACLE_DISAGREES = (
    "\nAbout rule 4: oracle.py was written by another model from your question, oracle "
    "interface and sample data alone; it never saw your reference. If the reference is "
    "right, the question is ambiguous or incomplete there: make it say exactly what it "
    "means (the oracle is then written again). If the reference is wrong, fix it. Keep "
    "oracle_py an interface: never copy this code into it. The oracle it wrote:\n")


ORACLE_AGREES = (
    "\nAbout rule 1: rule 4 ran anyway, and on random inputs the reference agrees with "
    "{oracle}. So the tests are the likely side to fix: their expected values (work them "
    "out again from the question on each test's data) or how they run.")


def _report_text(report: ValidationReport) -> str:
    return "\n".join(f"{r.rule:>4} {r.status.value:<5} {r.name}: {r.detail}"
                     for r in report.results) + ("\nVALID\n" if report.valid else "\nINVALID\n")


def write_draft(draft: ExerciseDraft, exercise_id: str,
                root: Path) -> tuple[Path, Path, dict[str, str]]:
    """root/exercises/<id> and root/solutions/<id>, laid out like a batch, and the mutants
    left out: one that cannot be applied is left out (name -> why) while MIN_MUTANTS others
    remain, so one copying slip does not cost the whole exercise; fewer is a format error."""
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
        target = exercise / _data_path(name)
        if target in files:  # "a.csv" and "./a.csv"
            raise DraftError(f"data file {name!r} is given twice")
        files[target] = content
    dropped = {}  # every mutant that cannot be applied, so one repair round fixes them all
    for mutant in draft.mutants:
        try:
            files[solution / "mutants" / f"{mutant.name}.py"] = apply_mutant(
                draft.reference_py, mutant)
        except DraftError as e:
            dropped[mutant.name] = str(e)
    if dropped and len(draft.mutants) - len(dropped) < MIN_MUTANTS:
        raise DraftError("\n".join(dropped.values()))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n")
    return exercise, solution, dropped


def _data_path(name: str) -> PurePosixPath:
    # Checked after PurePosixPath has normalised it: "./hints.md" is hints.md.
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or not path.parts or name.startswith("~")
            or any(part.startswith(".") for part in path.parts) or str(path) in OWN_FILES
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
        staged.append((staging, target))
    for staging, target in staged:
        staging.rename(target)
    _log(state, deps, "saved")
    return {"outcome": "saved"}


def _saved_before(deps: Deps, exercise_id: str) -> bool:
    if not deps.generation_log.is_file():
        return False
    lines = (json.loads(line) for line in deps.generation_log.read_text().splitlines())
    return any(line["exercise"] == exercise_id and line["outcome"] == "saved"
               for line in lines)


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
            "usage": final.get("usage", {"input": 0, "cached": 0, "output": 0}),
            "last_failure": error or final.get("failure")}


def write_manifest(deps: Deps, items: list[PlanItem], notes: str | None = None,
                   model: str | None = None) -> None:
    """List `items` (saved exercises) in the batch manifest, creating it or adding to it.
    `model`: who generated them (default: the generate model). Listing an item again
    changes nothing, so a resumed batch can run `collect` twice; the file is replaced in
    one step, so a crash leaves the old manifest or the new one, never half of one."""
    model = model or _model_names(deps)
    path = deps.batch_dir / "manifest.json"
    known = Manifest.model_validate_json(path.read_text()) if path.is_file() else None
    if known:
        items = [i for i in items if i.id not in known.exercises]
    if not items:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
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
    partial = path.with_name(path.name + ".partial")
    partial.write_text(manifest.model_dump_json(indent=2, exclude_none=True) + "\n")
    partial.replace(path)


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
                          thinking=deps.plan_thinking)
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
    return found + blocking_problems(items, lectures, existing)


def blocking_problems(items: list[PlanItem], lectures: list[str],
                      existing: list[str]) -> list[str]:
    """What no plan may have, edited by the owner or not: a repeated or taken id (two items
    would share a drafts folder, and each draft clears it first), an unknown lecture, a
    slide range that is not one. The count is the owner's to change."""
    ids = [i.id for i in items]
    found = [f"id {i!r} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    found += [f"id {i!r} already exists in another batch" for i in ids if i in existing]
    found += [f"{i.id}: lecture {i.lecture!r} is not one of {lectures}"
              for i in items if i.lecture not in lectures]
    found += [f"{i.id}: slides {i.slides!r} is not a range of slide footers like 4.15-4.24"
              for i in items if not re.fullmatch(r"\d+(\.\d+)*(-\d+(\.\d+)*)?", i.slides)]
    return found


def apply_decision(state: BatchState, decision: dict, existing: list[str]) -> BatchState:
    """The owner's answer to the plan: approve (with the plan as they may have edited it)
    or send it back with feedback. The CLI refuses a plan with blocking problems first;
    this check is for any other caller."""
    if decision.get("action") == "approve":
        plan = decision.get("plan", state["plan"])
        items = BatchPlan.model_validate({"items": plan}).items  # an edited plan must fit
        if found := blocking_problems(items, state["lectures"], existing):
            raise ValueError("the approved plan cannot run: " + "; ".join(found))
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
    saved = [x for x in lines if x["exercise"] in ids and x["outcome"] == "saved"]
    models = ([x["model"] for x in saved]
              + [f"repair {x['repair_model']}" for x in saved if x.get("repair_model")]
              + [f"oracle {x['oracle_model']}" for x in saved if x.get("oracle_model")])
    return "; ".join(dict.fromkeys(models)) or None


def _model_names(deps: Deps) -> str:
    """e.g. "deepseek: deepseek-v4-flash; oracle gpt-terra: gpt-6.1-terra"."""
    names = f"{deps.config.name}: {deps.config.model}"
    if deps.oracle_config:
        names += f"; oracle {deps.oracle_config.name}: {deps.oracle_config.model}"
    return names


def page_titles(workspace: Path, lecture: str) -> str:
    """One line per cached page: PDF page, slide footer and its first line of text."""
    path = cache.lecture_cache(workspace, lecture)
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
        "usage": state.get("usage", {"input": 0, "cached": 0, "output": 0}),
        "model": f"{deps.config.name}: {deps.config.model}",
        # What this item ran with, so a comparison of runs can tell configurations apart.
        "settings": {name: getattr(deps, name) for name in DEPS_FIELDS},
    }
    if deps.oracle_config:
        line["oracle_model"] = f"{deps.oracle_config.name}: {deps.oracle_config.model}"
    if deps.repair_config and any(a.get("model") == deps.repair_config.name
                                  for a in state.get("attempts", [])):
        line["repair_model"] = f"{deps.repair_config.name}: {deps.repair_config.model}"
    if error:
        line["error"] = error
    with _LOG_LOCK:
        deps.generation_log.parent.mkdir(parents=True, exist_ok=True)
        with deps.generation_log.open("a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
