"""Model-written held-out candidates (roadmap 3b step 2; docs/design.md, "Independent checks").

The owner's own code only exists for exercises they have solved, and it follows the
question, so it cannot show a question that misreads the slides. This module asks another
model (the `heldout` role, a different family from the generating and oracle models) to
write, for one exercise:

- `correct.py`: a correct solution of the question as written;
- `slides.py`: the solution as the slides do it, with one sentence on where the question
  departs from them (none when the slides give no procedure for the task);
- `wrong_01.py` ...: up to three typical student mistakes, each with what it gets wrong.

One model call writes one file. Asked for all of them in one reply, GLM-5.3 returned
placeholders ("pass", "see below") without thinking and spent its whole output on
reasoning with it (2026-10-31); one file per call is what its oracle calls already do
well. Each wrong one is told the mistakes already written, so they differ.

The model sees the question, the original skeleton, the sample data and the lectures the
exercise names (whole, with the exercise's own pages marked, as generation sees them when
they fit): never the reference, tests, mutants, oracle or hints, so its reading of the
question is independent of the generator's. The files go to solutions/<id>/heldout/model/
with a meta.json; validation/heldout.py runs them like the owner's candidates. This module
only writes files; nothing here runs them, and nothing here decides validity.

    python -m tutor.heldout_writer <batch_dir> [<exercise_id> ...] [--force]
        [--profile NAME] [--thinking] [--parallel N]

An exercise whose inputs (question, skeleton, data, slides, prompt version) are unchanged
since the same model last wrote it is kept without a model call; --force writes it again.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tutor import llm
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.execution import copied_files
from tutor.materials import cache
from tutor.validation.exercise import STUDENT_FILE, TEST_FILE
from tutor.validation.heldout import MODEL_DIR
from tutor.validation.heldout import MODEL_META as META_FILE
from tutor.validation.safety import allowed_imports, find_hazards, workspace_allow_imports

# Bump when a prompt changes: it is part of the inputs' hash, so every exercise is
# written again with the new prompt.
PROMPT_VERSION = 4
MAX_TOKENS = 8_000  # one student.py and a sentence, with room to spare
RETRIES = 2  # more replies after the first, each told what was wrong with the last
WRONG_COUNT = 3  # mistakes asked for, one call each
# Characters of lecture pages a prompt may hold, as generation's context_chars: lectures
# that fit go whole, else only the exercise's own pages (the largest lecture is ~53k).
CONTEXT_CHARS = 120_000
SAMPLE_CHARS = 2000  # per sample data file shown to the model
# Modules a solution has no use for: the test tools and the exercise's own module names.
TEST_ONLY = frozenset({"hypothesis", "pytest", "oracle", "student", "tutor", "__future__"})
# Files of the exercise folder the model never sees: the owner's file, the tests, the hints
# (they carry the generator's reading of the task), the question (sent separately), and
# what running the program by hand leaves behind (sample_output/, as exercise.py skips it).
_HIDDEN = {STUDENT_FILE, TEST_FILE, "hints.md", "question.md", "sample_output", "__pycache__"}


# --- the replies: one file each ---------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# The field descriptions go into the JSON Schema the model is given. `plan` comes first: a
# scratchpad. Without it GLM-5.3, shown a whole lecture, wrote its reasoning into `code`
# (2026-10-31), as repairs without `diagnosis` did in generation (graph/state.py).
_PLAN = "First, in a few sentences: how you will solve it. Not code."
_CODE = "The whole student.py: Python source only, no prose, no Markdown fences."


class CorrectDraft(_Strict):
    plan: str = Field(default="", description=_PLAN)
    code: Annotated[str, Field(min_length=1, description=_CODE)]

    def note(self) -> str:
        return ""


class SlidesDraft(_Strict):
    plan: str = Field(default="", description=_PLAN)
    differs: str = Field(description=(
        "Where the question departs from the slides, in one sentence; \"\" if nowhere."))
    code: str = Field(description=_CODE + " \"\" when the slides give no procedure for it.")

    def note(self) -> str:
        return self.differs


class WrongDraft(_Strict):
    plan: str = Field(default="", description=_PLAN)
    mistake: Annotated[str, Field(min_length=1, description=(
        "\"<the mistake>, <what goes wrong>\"."))]
    code: Annotated[str, Field(min_length=1, description=_CODE)]

    def note(self) -> str:
        return self.mistake


class HeldOutWriteError(Exception):
    """No usable candidate came back for an exercise; the message says why."""


@dataclass(frozen=True)
class Writer:
    """Which model writes, and how to call it (tests pass a fake `chat`)."""

    config: llm.LLMConfig
    chat: Callable = llm.chat
    log_path: Path | None = None  # logs/llm_calls.jsonl, for the cost
    # Off by default: with thinking on, GLM-5.3 spent its whole output on reasoning
    # (graph/settings.py; again here on 2026-10-31).
    thinking: bool = False


@dataclass(frozen=True)
class Inputs:
    """Everything the model sees for one exercise."""

    question: str
    skeleton: str
    data: dict[str, str]  # sample data file -> its first SAMPLE_CHARS characters
    slides: str  # lecture pages from the cache; "" if none found
    framework: str
    entry_points: list[str] = field(default_factory=list)  # names the tests import

    def key(self) -> str:
        """A hash of the inputs and the prompt version: unchanged means no new call."""
        text = json.dumps([PROMPT_VERSION, asdict(self)], sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()


# --- what the model sees ----------------------------------------------------------------


def gather(batch_dir: Path, exercise_id: str, workspace: Path) -> Inputs:
    solution = batch_dir / "solutions" / exercise_id
    exercise = batch_dir / "exercises" / exercise_id
    metadata = json.loads((solution / "metadata.json").read_text())
    # The original skeleton, never exercises/<id>/student.py: that file may hold the
    # owner's answer, which must not shape the model's reading.
    skeleton = (solution / "skeleton.py").read_text()
    question_md = exercise / "question.md"
    question = (question_md.read_text() if question_md.is_file()
                else ast.get_docstring(ast.parse(skeleton)) or "")
    # Sample data only: every non-Python, non-hidden file a test run would copy in.
    files = {path.name: path for path in sorted(exercise.iterdir())
             if path.name not in _HIDDEN and not path.name.startswith(".")}
    data = {name: path.read_text(errors="replace")[:SAMPLE_CHARS]
            for name, path in copied_files(files).items()
            if not name.endswith(".py") and not any(part.startswith(".")
                                                    for part in name.split("/"))}
    return Inputs(question=question, skeleton=skeleton, data=data,
                  slides=lecture_context(workspace, metadata.get("source")),
                  framework=metadata.get("framework", "python"),
                  entry_points=list(metadata.get("entry_point", [])))


def lecture_context(workspace: Path, source, limit: int = CONTEXT_CHARS) -> str:
    """The lectures metadata `source` names, as "## <lecture> p.N · slide L" sections, the
    exercise's own pages (`source.pdf_pages`) marked. Whole lectures if they fit in
    `limit` characters, else only the exercise's own pages. "" when no lecture is found (a
    hand-made exercise's "synthetic" source, or a lecture not in the cache)."""
    if not isinstance(source, dict):
        return ""
    # The lecture as the exercise names it: "L2.2", "Lecture 5: Spark III",
    # "Lecture_4.2-Spark_II", or several ("L2.1, L2.2"). Only its number is reliable.
    numbers = re.findall(r"\d+(?:\.\d+)?", source.get("lecture") or "")
    own: dict[str, set[int]] = {}
    for lecture, pages in page_groups(source.get("pdf_pages") or "", numbers):
        own.setdefault(lecture, set()).update(pages)
    lectures = []  # (name, its pages, the exercise's pages in it)
    for number in dict.fromkeys([*numbers, *own]):  # in order, each once
        path = _lecture_file(workspace, number)
        if path is not None:
            lectures.append((path.name.removesuffix(".pdf.md"), cache.load(path),
                             own.get(number, set())))
    whole = sum(len(e.body) for _, entries, _ in lectures
                for e in entries.values()) <= limit
    sections = []
    for name, entries, mine in lectures:
        for number in sorted(entries):
            if whole or number in mine:
                e = entries[number]
                label = f" · slide {e.label}" if e.label else ""
                mark = " · this exercise's page" if number in mine else ""
                sections.append(f"## {name} p.{number}{label}{mark}\n{e.body}")
    return "\n\n".join(sections)


# "L2.1 13-24, 41-42": a group of pages that names its lecture first.
_LABELLED = re.compile(r"^\s*L(\d+(?:\.\d+)?)\s+(.*)$")


def page_groups(pdf_pages: str, lectures: list[str]) -> list[tuple[str, set[int]]]:
    """(lecture number, PDF pages) from the forms the batches use: "35-38, 43-45",
    "p.13-p.15, p.19", "36–41" (an en dash), or per lecture "L2.1 13-24; L2.2 13-18".
    A group without a lecture label belongs to the exercise's only lecture; with several
    lectures and no label, which pages are whose is unknown, so the group is left out."""
    groups = []
    for part in pdf_pages.split(";"):
        labelled = _LABELLED.match(part)
        if labelled:
            lecture, text = labelled.group(1), labelled.group(2)
        elif len(lectures) == 1:
            lecture, text = lectures[0], part
        else:
            continue
        pages: set[int] = set()
        for item in text.replace("–", "-").replace("p.", "").split(","):
            bounds = [b.strip() for b in item.split("-")]
            if 1 <= len(bounds) <= 2 and all(b.isdigit() for b in bounds):
                pages.update(range(int(bounds[0]), int(bounds[-1]) + 1))
        if pages:
            groups.append((lecture, pages))
    return groups


def _lecture_file(workspace: Path, number: str) -> Path | None:
    """cache/lectures/Lecture_<number>-....pdf.md. The character after the number must be
    "-" or "_", so lecture 5 does not match Lecture_5.2-...."""
    pattern = re.compile(rf"^Lecture_{re.escape(number)}[-_]")
    found = sorted(p for p in (workspace / "cache" / "lectures").glob("*.pdf.md")
                   if pattern.match(p.name))
    return found[0] if found else None


# --- the prompts ------------------------------------------------------------------------

COMMON = """You write one solution to one exercise of a university programming course. It is
used to test the exercise's own tests, which you never see (nor its model solution).

Write it as a complete student.py: the starting file with every function written, the
same names and signatures, and its `if __name__ == "__main__":` block kept. Import only
{imports}; no network; read and write only the paths the question gives; no eval, exec or
__import__; no randomness unless the question asks for it, and then only through what the
question provides. Return only the JSON object; the task comes last, after the exercise."""

TASKS = {
    "correct": "Write a correct solution of the question exactly as written, including the "
               "assumptions and edge cases it states. Solve it your own way.",
    "slides": "Solve the task the way the slides do it. Where the question uses a different "
              "rule, formula, boundary or tie-break from the slides, follow the slides. "
              "`differs`: one sentence naming where the question departs from the slides, "
              "or \"\" if it does not. If the slides give no procedure for this task, set "
              "`code` to \"\".",
    "wrong": "Write a solution with one mistake a student who misunderstands the concept "
             "would make: not a typo, not a crash, not an empty answer. It must run and "
             "return a result of the right shape. `mistake`: \"<the mistake>, <what goes "
             "wrong>\".",
}
DRAFTS = {"correct": CorrectDraft, "slides": SlidesDraft, "wrong": WrongDraft}


def system_prompt(allowed: frozenset[str]) -> str:
    """The same for every file of an exercise, so with the exercise after it, every call
    starts with the same long prefix: the provider's prompt cache serves it from the
    second call on (DeepSeek and OpenAI cache prefixes by themselves)."""
    return COMMON.replace("{imports}", ", ".join(sorted(allowed - TEST_ONLY)))


def task_prompt(kind: str, written: list[str] = ()) -> str:
    """The one part that differs between calls, so it goes last. A wrong one lists the
    mistakes already `written`, so the next is a different one."""
    task = TASKS[kind]
    if written:
        task += " Mistakes already written, so make a different one: " + "; ".join(written)
    return "Your task: " + task


def user_prompt(inputs: Inputs) -> str:
    parts = ["The question:\n" + inputs.question,
             "The starting file (student.py):\n```python\n" + inputs.skeleton.rstrip()
             + "\n```"]
    if inputs.data:
        parts.append("Sample data:\n" + "\n\n".join(
            f"=== {name} ===\n{text}" for name, text in sorted(inputs.data.items())))
    parts.append("The lecture slides (the exercise's own pages are marked):\n" + inputs.slides
                 if inputs.slides else
                 "No slides are available for this exercise.")
    return "\n\n".join(parts)


# --- checking a reply -------------------------------------------------------------------


# A whole reply wrapped in a Markdown code block: "```python\n...\n```".
_FENCED = re.compile(r"^\s*```[\w-]*\n(.*?)\n?```\s*$", re.DOTALL)


def unfence(code: str) -> str:
    """The code inside a Markdown fence, if the model put one around it anyway."""
    fenced = _FENCED.match(code)
    return fenced.group(1) if fenced else code


def code_problems(code: str, inputs: Inputs, allowed: frozenset[str]) -> list[str]:
    """Why a candidate cannot be used: it does not parse, lacks a name the tests import,
    or fails the static safety check (validation/heldout.py checks again before running)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error at line {e.lineno}: {e.msg}"]
    defined = {node.name for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    # "Class.method" entry points need the class at module level.
    missing = [name for name in inputs.entry_points if name.split(".")[0] not in defined]
    found = [f"does not define {', '.join(missing)}"] if missing else []
    # GLM-5.3 once returned `def f(...): ...`, and once a function squeezed onto one line,
    # `def f(...) -> float:n    total = 0.0n ...`: it parses, the body being the name `n`
    # and the rest a comment. Both are caught as a function that does nothing.
    empty = [node.name for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in inputs.entry_points and does_nothing(node)]
    found += [f"{name} has no body: write it out in full" for name in empty]
    return found + find_hazards(code, allowed)


def does_nothing(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Its body, past any docstring, is only `pass`, `...`, a bare name or constant, or
    `raise NotImplementedError`."""
    def idle(stmt: ast.stmt) -> bool:
        if isinstance(stmt, ast.Pass):
            return True
        if isinstance(stmt, ast.Expr):
            return isinstance(stmt.value, (ast.Constant, ast.Name))
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
            return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
        return False
    return all(idle(stmt) for stmt in func.body)


def _same_mistake(mistake: str, written: list[str]) -> bool:
    """Told the mistakes already written, DeepSeek-V4-Pro still wrote the same one again
    (2026-10-31), word for word; a close match counts too."""
    return any(SequenceMatcher(None, mistake.lower(), w.lower()).ratio() >= 0.8
               for w in written)


def ask(writer: Writer, kind: str, inputs: Inputs, allowed: frozenset[str],
        tokens: dict[str, int], written: list[str] = ()):
    """One file of `kind`: the parsed draft, or None and the last problem after the
    retries. `tokens` adds up what the calls spent."""
    model = DRAFTS[kind]
    messages = [{"role": "system", "content": system_prompt(allowed)},
                {"role": "user", "content": user_prompt(inputs) + "\n\n"
                 + task_prompt(kind, written)}]
    problem = ""
    for attempt in range(1 + RETRIES):
        reply = writer.chat(writer.config, messages, purpose="heldout",
                            log_path=writer.log_path, max_tokens=MAX_TOKENS,
                            response_schema=model.model_json_schema(),
                            thinking=writer.thinking)
        tokens["input"] += reply.input_tokens or 0
        tokens["output"] += reply.output_tokens or 0
        try:
            draft = model.model_validate_json(reply.text)
            draft = draft.model_copy(update={"code": unfence(draft.code)})
        except ValidationError as e:
            problem = "the reply does not fit the JSON schema: " + "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
        else:
            # A slides draft with no code means "the slides give no procedure": not a problem.
            found = code_problems(draft.code, inputs, allowed) if draft.code.strip() else []
            if kind == "wrong" and _same_mistake(draft.mistake, written):
                found.append("it is the same mistake as one already written; make a "
                             "different one")
            if not found:
                return draft, ""
            problem = "; ".join(found)
        if attempt < RETRIES:
            # The model sees its own reply and what was wrong with it, and answers again.
            messages = [*messages[:2], {"role": "assistant", "content": reply.text},
                        {"role": "user", "content": f"That reply has problems: {problem}. "
                                                    "Return it again with them fixed."}]
    return None, problem


# --- writing one exercise ---------------------------------------------------------------


def write(batch_dir: Path, exercise_id: str, workspace: Path, writer: Writer,
          force: bool = False) -> str:
    """Write the model's candidates for one exercise; return what happened, e.g.
    "written: correct, slides, 3 wrong" or "kept: inputs unchanged"."""
    inputs = gather(batch_dir, exercise_id, workspace)
    # MODEL_DIR is validation/heldout.py's, the module that runs these files.
    out = batch_dir / "solutions" / exercise_id / MODEL_DIR
    meta_path = out / META_FILE
    model_name = f"{writer.config.name}: {writer.config.model}"
    if not force and meta_path.is_file():
        old = json.loads(meta_path.read_text())
        # Kept only if the same model wrote it from the same inputs: another model's files
        # are its reading, not this one's.
        if old.get("inputs") == inputs.key() and old.get("model") == model_name:
            return "kept: inputs unchanged"

    allowed = allowed_imports(inputs.framework, workspace_allow_imports(batch_dir))
    tokens = {"input": 0, "output": 0}
    files: dict[str, tuple[str, str]] = {}  # file name -> (code, note)
    dropped: dict[str, str] = {}  # file name -> why it could not be written
    plan = [("correct.py", "correct"), ("slides.py", "slides")]
    plan += [(f"wrong_{i:02d}.py", "wrong") for i in range(1, WRONG_COUNT + 1)]
    for name, kind in plan:
        # Each wrong one is told the mistakes before it.
        written = [note for n, (_, note) in files.items() if n.startswith("wrong_")]
        draft, problem = ask(writer, kind, inputs, allowed, tokens, written)
        if draft is None:
            dropped[name] = problem
        elif draft.code.strip():
            files[name] = (draft.code, draft.note())
    if not files:
        raise HeldOutWriteError("; ".join(f"{n}: {p}" for n, p in dropped.items()))

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.py"):  # a rewrite replaces the whole set
        old.unlink()
    for name, (code, _) in files.items():
        (out / name).write_text(code if code.endswith("\n") else code + "\n")
    meta = {
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "model": model_name,
        "inputs": inputs.key(),
        "slides_seen": bool(inputs.slides),
        "notes": {name: note for name, (_, note) in files.items() if note},
        "dropped": dropped,
        "tokens": tokens,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    wrong = sum(name.startswith("wrong_") for name in files)
    kinds = [k for k in ("correct", "slides") if f"{k}.py" in files] + [f"{wrong} wrong"]
    return "written: " + ", ".join(kinds) + (f"; dropped {', '.join(dropped)}" if dropped else "")


# --- command line -----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Exit code 0: every exercise written or kept; 1: some failed; 2: no workspace or
    model."""
    parser = argparse.ArgumentParser(prog="python -m tutor.heldout_writer")
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("exercise_ids", nargs="*", help="default: every exercise")
    parser.add_argument("--force", action="store_true", help="write even if inputs are unchanged")
    parser.add_argument("--profile", help="a workspace.toml profile instead of the `heldout` role")
    parser.add_argument("--thinking", action="store_true", help="let the model reason first")
    parser.add_argument("--parallel", type=int, default=4, help="exercises at once")
    args = parser.parse_args(argv)
    load_env()
    try:
        root = load_workspace().root
        writer = Writer(llm.config_for("heldout", args.profile, workspace=root),
                        log_path=root / "logs" / "llm_calls.jsonl", thinking=args.thinking)
    except (WorkspaceError, llm.LLMError) as e:
        print(f"heldout_writer: {e}", file=sys.stderr)
        return 2
    batch = args.batch_dir
    ids = args.exercise_ids or sorted(p.name for p in (batch / "solutions").iterdir()
                                      if p.is_dir() and not p.name.startswith("."))

    def one(exercise_id: str) -> bool:
        try:
            outcome = write(batch, exercise_id, root, writer, args.force)
        except (llm.LLMError, HeldOutWriteError, OSError, ValueError) as e:
            # One exercise's failure never stops the others (an assistant profile's
            # unanswered request is an LLMError too: run again once it is answered).
            print(f"{exercise_id}: failed: {e}", flush=True)
            return False
        print(f"{exercise_id}: {outcome}", flush=True)
        return True

    # The model calls wait on the network, so a few exercises go at once.
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        ok = list(pool.map(one, ids))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
