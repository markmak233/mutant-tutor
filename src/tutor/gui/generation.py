"""Generating a batch from the GUI (step 4): the new-batch form's checks, the commands it
starts, and what a planned batch's files say about where it stands. What each exercise
of an approved plan came to, and trying one again, is plan_items.py.

Nothing here calls a model or writes a file. The GUI runs the CLI's own commands as tasks
(tasks.py), the way the owner would in a terminal:

    python -m tutor.graph plan --lectures ... --batch=LABEL ...   makes the plan, then pauses
    python -m tutor.graph resume --batch=LABEL --approve          generates the batch
    python -m tutor.graph resume --batch=LABEL --feedback=TEXT    makes the plan again
    python -m tutor.graph resume --batch=LABEL                    continues a stopped run

A command started without a terminal pauses at the plan instead of asking, and exits 0
(`_ask` in graph/plan_review.py). Selection is checkpointed by the graph. Free text
(preferences, feedback)
goes in as one argument in `--name=value` form, so text starting with "-" cannot pass for an
option; no shell is involved.

File evidence supports legacy batches; plan_selection.py reads approval and selection
from the checkpoint without constructing clients or loading credentials:
- a task of this GUI running for the label: making the plan, or generating;
- `batches/<batch>/manifest.json`: generated (the CLI writes it once every item has finished);
- a draft folder for one of the plan's ids, or the batch's generation.jsonl: generation began
  and did not finish, so it continues;
- else plan.json: the plan waits for the owner.
One case files cannot tell: a plan being made again (feedback) that stopped leaves the old
plan.json behind, and approving would first finish the new plan and approve it unseen. The
GUI's own task history covers it: when its last task for the label made a plan and did not
finish, the page offers Continue, not Approve. A batch planned in a terminal has no such
history; its terminal showed what happened.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tutor.gui import tasks, toml_edit
from tutor.gui.courses import Course
from tutor.gui.i18n import N_
from tutor.materials import coverage_store
from tutor.materials.cache import is_lecture, lecture_caches, lecture_name
from tutor.materials.slides import lecture_number

# The command, a list so a test can put a stand-in script here.
GRAPH = [sys.executable, "-m", "tutor.graph"]
# A new label: what the CLI's batch folders use, kept to one plain shape.
LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# A planned batch's folder in batches/.drafts/, e.g. 2026-11-06_p3-w9-streams. Older labels
# came from the command line, so any word characters; never a leading "-".
_DRAFT = re.compile(r"^\d{4}-\d{2}-\d{2}_p3-(\w[\w.-]*)$")
_ANY_LABEL = re.compile(r"^\w[\w.-]*$")
MAX_COUNT = 40  # GenerationSettings.max_count's bound (see there why 40)
# The form's limit when the model decides, unless [generation] max_count sets one: the
# highest, as some lectures hold far more than 6 knowledge points (owner, 2026-11-06), and
# only ticked items are generated, so a longer plan costs no more (owner, 2026-11-09). The
# CLI's default stays GenerationSettings.max_count (6); the owner reads every plan anyway.
DEFAULT_MAX_COUNT = MAX_COUNT
MAX_TEXT = 2000  # characters of preferences or feedback
ROLES = ("plan", "generate", "oracle", "repair")
WAITING_FOR_ASSISTANT = 3  # graph/__main__.py's exit code in assistant mode

# Task kinds, kept in task.json's meta with the label.
PLAN, FEEDBACK, APPROVE, CONTINUE = "plan", "feedback", "approve", "continue"
# One exercise of a generated batch, generated again (owner, 2026-11-06: a status next to
# each exercise, and a button to try one that did not succeed again).
RETRY = "retry"
KINDS = (PLAN, FEEDBACK, APPROVE, CONTINUE, RETRY)  # a task of one of these goes back to its plan
_PLANNING_KINDS = (PLAN, FEEDBACK, CONTINUE)

# Where a batch stands; the words the pages show.
MAKING_PLAN = N_("making the plan")
GENERATING = N_("generating")
CONTINUING = N_("continuing")
WAITING = N_("waiting for you")
STOPPED = N_("stopped midway")
NO_PLAN = N_("plan not finished")
ASSISTANT = N_("waiting for the assistant")
GENERATED = N_("generated")
NOT_STARTED = N_("did not start")
QUEUED = N_("queued")  # a task of this plan waits for the course's running task to end
# What each state means, as the plan page says it (N_(): translated when shown).
EXPLAIN = {
    QUEUED: N_("A task of this plan is queued: it starts when the course's running task ends. "
            "Until then it can be removed from the course page."),
    MAKING_PLAN: N_("The plan model is at work. This page updates by itself, and the plan "
                 "appears here when it is ready."),
    GENERATING: N_("Generating the approved plan: each exercise is written, validated in a "
                "container and repaired when it fails. It goes on if you close this page."),
    CONTINUING: N_("Continuing from where the last run stopped."),
    WAITING: N_("The plan waits for you. Approve it to generate the exercises, edit plan.json "
             "first, or send it back with what to change. Nothing is generated before you "
             "approve."),
    STOPPED: N_("Generation stopped before every exercise finished. Continue runs the rest; "
             "exercises that finished are kept and not run again."),
    NO_PLAN: N_("Making the plan stopped before it was ready. Continue makes it from where it "
             "stopped, and it waits for you again."),
    ASSISTANT: N_("The batch waits for answers from the coding assistant (assistant mode, "
               "docs/assistant-mode.md). Answer them from a terminal, then Continue."),
    GENERATED: N_("The run has finished. Saved exercises, if any, are on the course page."),
    NOT_STARTED: N_("The command stopped before it made the batch's folder; its output below "
                 "says why (Docker not running, for example). Fix that, then start a new "
                 "batch; the label is free."),
}


@dataclass(frozen=True)
class NewBatch:
    """A checked new-batch form."""

    label: str
    lectures: list[str]
    mode: str  # learn or review
    count: int | None  # exactly this many; None: the plan model decides
    max_count: int | None  # when the model decides: at most this many
    prefs: str
    hide_answers: bool


@dataclass(frozen=True)
class PlanView:
    label: str
    state: str  # one of the words above
    draft: Path | None  # batches/.drafts/<batch>/, once the command has made it
    batch: str | None  # the batch folder's name, e.g. 2026-11-06_p3-w9-streams
    items: list[dict] = field(default_factory=list)  # plan.json, every value as text
    plan_error: str | None = None  # plan.json exists but cannot be read
    edited: bool = False  # plan.json differs from the model's plan
    problems: list[str] = field(default_factory=list)  # the "!" lines of the last plan shown
    settings: dict = field(default_factory=dict)  # run.json's mode and hide_answers
    task: tasks.Task | None = None  # the newest GUI task for this label
    # How the number was set, from the GUI task that made the plan: {"count": N} or
    # {"max_count": M}; empty when unknown (planned in a terminal, or the task pruned).
    how_many: dict = field(default_factory=dict)
    # Checkpoint metadata separates proposed material, authorized work and completion.
    approved_plan: list[dict] | None = None
    selected_ids: list[str] | None = None
    plan_digest: str = ""
    selection_error: str = ""
    generation_complete: bool = False
    has_manifest: bool = False
    source_lectures: list[str] = field(default_factory=list)

    @property
    def can_review(self) -> bool:
        return self.state == WAITING and not self.selection_error

    @property
    def can_approve(self) -> bool:
        return self.can_review and bool(self.items) and not self.plan_error

    @property
    def hides_answers(self) -> bool:
        """Generated with hide_answers (hand-calculation exercises): the page then shows
        nothing that can quote a test's expected value."""
        return str(self.settings.get("hide_answers", "")).lower() == "true"

    @property
    def can_continue(self) -> bool:
        return self.state in (STOPPED, NO_PLAN, ASSISTANT) and not self.selection_error

    @property
    def shows_plan(self) -> bool:
        """Show the full review/approved material, but hide an old plan during replanning.
        A queued task's plan stays visible: the owner sees what waits to run."""
        return self.state in (WAITING, GENERATING, STOPPED, ASSISTANT, GENERATED, QUEUED)

    @property
    def generated(self) -> bool:
        return self.state == GENERATED

    @property
    def at_limit(self) -> bool:
        """The model chose the number and stopped at the limit: knowledge points may have been
        merged or left out. The limit is fixed in the batch's state once planned, so a plan
        sent back cannot go past it either."""
        limit = self.how_many.get("max_count")
        return isinstance(limit, int) and len(self.items) == limit


# --- the new-batch form ---------------------------------------------------------------------


def lectures(course: Course) -> tuple[list[str], list[str]]:
    """The lectures a plan can use (read into the cache: cache/lectures/<name>.pdf.md, or
    .html.md), and the lectures in the lecture folder not read yet."""
    ready = sorted(name for p in lecture_caches(course.workspace.root)
                   if (name := lecture_name(p)) and _usable(p.name))
    files = sorted(p.stem for p in _files(course.workspace.lecture_dir)
                   if is_lecture(p) and _usable(p.name))
    return ready, [name for name in files if name not in ready]


def coverage_missing(course: Course, lectures: list[str]) -> list[str]:
    """The lectures among `lectures` that coverage.md shows no trace of, in their order: a
    plan made for them comes from the page titles alone, as week 9's did on 2026-11-09
    when the drafted rows had not been merged (owner, 2026-11-10: warn). Three traces
    count, as the file is written two ways: a heading equal to the lecture's name (the
    draft's format, `coverage.has_section`); a heading that carries its number ("L9.1",
    "Lecture 9.1", a hand-written file); or a table row whose Slides cell starts with its
    week ("9.22-9.23"). The last cannot tell two lectures of one week apart (both number
    their slides 9.x), so a week with rows for one of them warns for neither. Without a
    coverage.md every lecture is missing; a lecture whose name carries no number is judged
    by its heading alone."""
    store = coverage_store.load(course.workspace.root)
    if store is not None:  # the data says exactly which lectures have points
        return coverage_store.missing_lectures(store, lectures)
    try:
        text = (course.workspace.root / "coverage.md").read_text(encoding="utf-8")
    except OSError:
        return list(lectures)
    headings = [line.lstrip("#").strip() for line in text.splitlines()
                if line.lstrip().startswith("#")]
    cells = [line.strip().strip("|").split("|")[0].strip() for line in text.splitlines()
             if line.lstrip().startswith("|")]
    missing = []
    for lecture in lectures:
        number = lecture_number(lecture)
        week = number.split(".")[0] if number else None
        if lecture in headings:
            continue
        if number and any(re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", h)
                          for h in headings):
            continue
        if week and any(cell.startswith(f"{week}.") for cell in cells):
            continue
        missing.append(lecture)
    return missing


def defaults(course: Course) -> dict:
    """The form's starting values, from workspace.toml's [generation] table as a run would
    use them."""
    table = _toml(course).get("generation", {})
    mode, limit = table.get("mode"), table.get("max_count")
    return {"mode": mode if mode in ("learn", "review") else "learn",
            "hide_answers": table.get("hide_answers") is True, "how": "auto",
            "max_count": limit if isinstance(limit, int) and 1 <= limit <= MAX_COUNT
            else DEFAULT_MAX_COUNT}


def models(course: Course, overrides: dict | None = None) -> list[tuple[str, str]]:
    """(role, what does it) for each role a batch uses, from [llm.roles] and [llm.profiles];
    names only, never a key."""
    llm = _toml(course).get("llm", {})
    if not llm and not overrides:
        return [("all roles", "the LLM_* variables in .env")]
    roles, profiles = llm.get("roles", {}), llm.get("profiles", {})
    shown = []
    for role in ROLES:
        name = (overrides or {}).get("profile" if role == "generate" else role + "_profile")
        name = name or roles.get(role)
        if not name:
            text = {"plan": "the generate model", "generate": "none set: generation stops",
                    "oracle": "none: the generate model writes the oracle",
                    "repair": "none: the generate model repairs"}[role]
        elif not isinstance(profiles.get(name), dict):
            text = f"{name} (no such profile)"
        else:
            data = profiles[name]
            model = data.get("model") or data.get("provider", "?")
            text = f"{name} ({data.get('provider', '?')}: {model})"
        shown.append((role, text))
    return shown


def check_form(course: Course, label: str, chosen: list[str], mode: str, how: str, count: str,
               max_count: str, prefs: str, hide_answers: bool
               ) -> tuple[NewBatch | None, list[str]]:
    """The form as a NewBatch, or the problems to show with it. `how`: "auto" (the plan
    model counts the knowledge points, at most `max_count`) or "exact" (`count`)."""
    problems = []
    label = label.strip()
    if not LABEL.match(label):
        problems.append("The label takes lowercase letters, digits and '-', 40 at most, "
                        "e.g. w9-streams.")
    elif (free := free_label(course, label)) is None:
        problems.append(f"A batch labelled {label} exists already, and adding -1, -2, ... "
                        "would pass 40 characters: choose a shorter label.")
    else:
        label = free  # a taken label gets the first free -1, -2, ... (owner, 2026-11-09)
    ready, _ = lectures(course)
    if not chosen:
        problems.append("Choose at least one lecture.")
    elif any(name not in ready for name in chosen):
        problems.append("Only lectures read into the cache can be chosen.")
    if mode not in ("learn", "review"):
        problems.append("The mode is learn or review.")
    number = limit = None
    if how == "exact":
        number = _number(count)
        if number is None:
            problems.append(f"Exactly how many: a number from 1 to {MAX_COUNT}.")
    elif how == "auto":
        limit = _number(max_count)
        if limit is None:
            problems.append(f"The limit when the model decides: a number from 1 to "
                            f"{MAX_COUNT}.")
        if count.strip():
            # No script switches the choice when a number is typed: say so, never guess.
            problems.append("A number is typed under \"Exactly\", but \"Let the plan model "
                            "decide\" is chosen: choose one.")
    else:
        problems.append("Choose how many exercises: the model decides, or exactly a number.")
    text = clean_text(prefs)
    if text is None:
        problems.append(f"Preferences: {MAX_TEXT} characters at most, plain text.")
    if problems:
        return None, problems
    return NewBatch(label, [n for n in ready if n in chosen], mode, number, limit, text or "",
                    hide_answers), []


def _number(text: str) -> int | None:
    """A count from the form, 1 to MAX_COUNT, or None."""
    try:
        number = int(text.strip())
    except ValueError:
        return None
    return number if 1 <= number <= MAX_COUNT else None


def clean_text(text: str) -> str | None:
    """Preferences or feedback as one argument: line ends made "\\n", trimmed; None if too
    long or holding other control characters (a NUL cannot be in an argument at all)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_TEXT or any(ord(c) < 32 and c not in "\n\t" for c in text):
        return None
    return text


def label_taken(course: Course, label: str) -> bool:
    """A batch or a draft has this label, which `plan` would refuse."""
    root = course.workspace.root / "batches"
    return any(label_of(p.name) == label
               for p in [*_dirs(root), *_dirs(root / ".drafts")])


def free_label(course: Course, label: str) -> str | None:
    """The label itself when no batch or draft has it, else the first of label-1, label-2,
    ... that is free and still a valid label; None when the suffix would pass LABEL's 40
    characters. Only the GUI's form does this: on the command line, `plan` refuses a
    taken label, as a name typed by hand should not change silently."""
    if not label_taken(course, label):
        return label
    n = 1
    while LABEL.match(candidate := f"{label}-{n}"):
        if not label_taken(course, candidate):
            return candidate
        n += 1
    return None


def graph_command(python: str | None) -> list[str]:
    """`python -m tutor.graph` with the course's interpreter (courses.task_python), whose
    test runs then use it too; GRAPH's own when none is given."""
    return [python or GRAPH[0], *GRAPH[1:]]


def plan_command(batch: NewBatch, python: str | None = None) -> list[str]:
    command = [*graph_command(python), "plan", "--lectures", *batch.lectures,
               f"--batch={batch.label}", f"--mode={batch.mode}", "--set",
               f"hide_answers={'true' if batch.hide_answers else 'false'}"]
    if batch.count:
        command.append(f"--count={batch.count}")
    else:
        command.append(f"--max-count={batch.max_count}")
    if batch.prefs:
        command.append(f"--prefs={batch.prefs}")
    return command


def validate_command(label: str, python: str | None = None) -> list[str]:
    """Every exercise of the generated batch, right after generating it in the same task, so
    its page shows the verdicts without a "Validate all" click (owner, 2026-11-06)."""
    return [python or GRAPH[0], "-m", "tutor.validation.batch", label]


def resume_command(label: str, approve: bool = False, feedback: str | None = None,
                   python: str | None = None, selected: list[str] | None = None,
                   plan_digest: str | None = None) -> list[str]:
    command = [*graph_command(python), "resume", f"--batch={label}"]
    if approve:
        command.append("--approve")
        if selected is not None:
            command.append("--only=" + ",".join(selected))
        if plan_digest is not None:
            command.append("--plan-digest=" + plan_digest)
    elif feedback is not None:
        command.append(f"--feedback={feedback}")
    return command


# --- planned batches --------------------------------------------------------------------------


def plan_view(course: Course, label: str, history: list[tasks.Task] | None = None
              ) -> PlanView | None:
    """Where the batch labelled `label` stands, or None when there is no such plan (neither a
    draft made by `plan` nor a GUI task for it). `history`: the course's tasks, newest first
    (read here when not given)."""
    if not _ANY_LABEL.match(label):
        return None
    history = tasks.list_tasks(course.workspace.root) if history is None else history
    task = next((t for t in history if t.meta.get("label") == label), None)
    draft = _draft(course, label)
    if draft is None and task is None:
        return None
    if draft is None:
        # The command has not made its folder yet, or stopped before (Docker not running).
        return PlanView(label, _running_state(task) if task.pending else NOT_STARTED, None,
                        None, task=task)
    items, plan_error, edited = _plan(draft)
    batch_dir = course.workspace.root / "batches" / draft.name
    if task is not None and task.pending:
        state = _running_state(task)
    elif (batch_dir / "manifest.json").is_file():
        state = GENERATED
    elif task is not None and task.exit_codes and task.exit_codes[-1] == WAITING_FOR_ASSISTANT:
        state = ASSISTANT
    elif (batch_dir / "generation.jsonl").is_file() or any(
            (draft / str(item.get("id", ""))).is_dir() for item in items if item.get("id")):
        state = STOPPED
    elif not (draft / "plan.json").is_file():
        state = NO_PLAN
    elif (task is not None and task.meta.get("kind") in _PLANNING_KINDS
          and task.status != tasks.DONE):
        state = NO_PLAN  # see the module's docstring: the plan.json shown may be the old one
    else:
        state = WAITING
    made = next((t for t in history if t.meta.get("label") == label
                 and t.meta.get("kind") == PLAN), None)
    how_many = {k: v for k, v in (made.meta if made else {}).items()
                if k in ("count", "max_count") and isinstance(v, int)}
    # Imported here to keep the optional checkpoint reader away from new-batch setup.
    from tutor.gui.plan_selection import adapt

    return adapt(PlanView(label, state, draft, draft.name, items, plan_error, edited,
                          _problems(task) if task is not None else [], _settings(draft), task,
                          how_many))


def open_plans(course: Course, history: list[tasks.Task] | None = None) -> list[PlanView]:
    """The batches planned but not generated yet, for the course page: drafts made by `plan`
    without a manifest, and labels a GUI task is planning before their folder exists."""
    history = tasks.list_tasks(course.workspace.root) if history is None else history
    labels = [label for p in _dirs(course.workspace.root / "batches" / ".drafts")
              if (label := label_of(p.name)) and (p / "checkpoint.sqlite").is_file()]
    labels += [t.meta["label"] for t in history
               if t.pending and isinstance(t.meta.get("label"), str)]
    views = [plan_view(course, label, history) for label in dict.fromkeys(labels)]
    return [v for v in views if v is not None and v.state not in (GENERATED, NOT_STARTED)]


def _running_state(task: tasks.Task) -> str:
    """What a running task does, by its kind; Continue may finish a plan or a batch. A
    queued one: queued, whatever its kind."""
    if task.queued:
        return QUEUED
    kind = task.meta.get("kind")
    return GENERATING if kind in (APPROVE, RETRY) else MAKING_PLAN if kind in (PLAN, FEEDBACK) \
        else CONTINUING



def _draft(course: Course, label: str) -> Path | None:
    """The label's draft folder made by `plan` (it holds checkpoint.sqlite), the newest if
    several, as `resume` picks it."""
    found = sorted(p for p in _dirs(course.workspace.root / "batches" / ".drafts")
                   if label_of(p.name) == label and (p / "checkpoint.sqlite").is_file())
    return found[-1] if found else None


def _plan(draft: Path) -> tuple[list[dict], str | None, bool]:
    """plan.json's items with every value as text, an error if it cannot be read, and whether
    it differs from plan.model.json (the owner edited it)."""
    path = draft / "plan.json"
    if not path.is_file():
        return [], None, False
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], f"plan.json cannot be read: {e}", False
    if not isinstance(plan, list) or not all(isinstance(i, dict) for i in plan):
        return [], "plan.json is not a list of items", False
    try:
        model = json.loads((draft / "plan.model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        model = plan
    items = [{k: v if isinstance(v, str) else json.dumps(v) for k, v in i.items()} for i in plan]
    return items, None, plan != model


def _problems(task: tasks.Task) -> list[str]:
    """The "!" lines the CLI printed under the last plan it showed in this task's log."""
    lines = tasks.tail(task)[0].splitlines()
    end = next((n for n in range(len(lines) - 1, -1, -1)
                if lines[n].startswith("The plan is in ")), None)
    if end is None:
        return []
    found = []
    for line in reversed(lines[:end]):
        if line.startswith("   ! "):
            found.append(line[5:])
        elif line.strip():
            break
    return found[::-1]


def _settings(draft: Path) -> dict:
    try:
        data = json.loads((draft / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    settings = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(settings, dict):
        return {}
    return {k: settings[k] for k in ("mode", "hide_answers") if k in settings}


def plan_label(course: Course, batch: str) -> str | None:
    """The label of the plan a batch came from, for the batch page's link back to it; None
    when its draft is gone, or when a newer draft took the label, as the plan page would
    then show that one and not the plan this batch was made from."""
    label = label_of(batch)
    draft = _draft(course, label) if label else None
    return label if draft is not None and draft.name == batch else None


def label_of(name: str) -> str | None:
    match = _DRAFT.match(name)
    return match.group(1) if match else None


def _toml(course: Course) -> dict:
    return toml_edit.read(course.workspace.root)


def _usable(name: str) -> bool:
    """A name that can be one argument of --lectures: not hidden, not taken for an option."""
    return not name.startswith((".", "-"))


def _files(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []


def _dirs(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_dir()] if folder.is_dir() else []
