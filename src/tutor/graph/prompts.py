"""Prompts of the generation graph: one template per prompt (generate and repair, oracle,
plan), filled with the parts of the course's language (graph/prompt_parts/).

Ordered for prompt caching, which reuses the longest prefix a provider has seen: the system
prompt is the same for every item of a course (format rules, the testkit API, the course
conventions); the user message then gives the course pages (shared by the items of one
lecture), the example exercise (shared by items of one framework and interface), and only
then the item and, when repairing, the failed draft. The generate, repair and retry prompts
are `llm.Prompt`s in two parts, the pages and the rest: one string to every reader, and
to the anthropic provider the place of its cache breakpoint (2026-11-10; the bytes are the
ones pinned before). The course conventions come from the workspace's conventions.md,
never from this file: everything here holds for any course.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from tutor.graph.prompt_parts import LanguagePrompts, c, python
from tutor.graph.state import PlanItem
from tutor.llm import Prompt
from tutor.validation.safety import CORE_IMPORTS

# Each language's parts, by the name its graph/languages.py Profile has.
LANGUAGES: dict[str, LanguagePrompts] = {"python": python.PROMPTS, "c": c.PROMPTS}

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
# The example shown for each (framework, interface): the closest hand-made, validated
# exercise, as each language names them; any other pair sees DEFAULT_EXAMPLE.
EXAMPLE_FOR = {pair: name for parts in LANGUAGES.values() for pair, name in parts.examples.items()}
DEFAULT_EXAMPLE = "station-average-rdd"

# The generate / repair system prompt: what the validator checks, then each file of the
# reply (ExerciseDraft's fields) with its rules. One template for every language (owner,
# 2026-11-07: one version, not one per language): each language fills the {slots} that
# differ from its LanguagePrompts.spec, and then {imports}, {oracle} and {concept_lines}
# (and for C {flags} and {headers}) are filled in by system_prompt().
# Python's parts are its prompt as calibrated, byte for byte (tests/test_prompt_pins.py).
SPEC = """You write programming exercises for a university course, one exercise at a time.
Each exercise is checked by an automatic validator before anyone sees it; write it so that
it passes:
{rules}

Files (fields of the JSON you return):
- metadata: the exercise's metadata. `source` is an object naming the lecture, the slide
  footers and the PDF pages it comes from (the PDF page is the `p.N` of a section heading
  below, the footer its `slide S`), and the teaching assumptions (choices of this
  exercise, not rules of the framework). Never "synthetic": that is only for the example,
  which is not from a course.
{student}
    ## Concept     learn mode only: the knowledge point as a condensed lecture, at most
                   {concept_lines} lines, so the student need not go back to the slides.
                   In `### ` parts: "### Why it is needed" first (at most 3 lines: what
                   the technique does and why it is needed), then one part per idea, its
                   slides in the heading ("### The update rule (slides 4.2-4.3)"). In the
                   slides' words and terms; a slide's own example may be shown on data
                   other than this exercise's. Steps as a numbered list (nested for
                   sub-steps) or as pseudocode in a ```text block: the slides' steps,
                   language-neutral (`for i = 1..d: increment C[i][h_i(x)]`), never
                   runnable {language} and never this exercise's names, signature or edge
                   cases. Properties as bullets; comparisons and traces as tables; the key
                   claim as a `> ` quote; proofs, analyses and optional extensions folded
                   in <details><summary>...</summary> ... </details>. Give an algorithm's
                   starting state as the slides do (the first candidate, the initial
                   count). Claim nothing stronger than the slides: a guarantee holds under
                   its conditions only. Never solution code or how to write this exercise
                   (hints_md's job)
    ## Symptom     debugging only: the program is wrong and how that shows
    ## Task        one or two sentences: what to write or fix; names as `inline code`
    ## Input       a table: | Parameter | Meaning |
    ## Output
    ## Example     a ```text block with the input and the output, which must be exactly
                   what the reference gives and the tests expect; for a trace, a table of
                   the steps after it
    ## Constraints a numbered list, the key words in **bold**
    ## Slides      the lecture, slides and PDF pages. Every teaching assumption, and every
                   place the exercise departs from the slides (another rule, boundary,
                   formula or simplification), as a `> **Assumption:** ...` quote: never a
                   silent departure
{run_section}
  Do not write the line under the title, a contents line, rules (---) or "How to run":
  code adds them. Math is LaTeX: $...$ inline, $$...$$ on lines of its own, never inside a
  list item; \\lbrace and \\rbrace, not \\{ and \\}. {language} expressions that say what the
  code computes stay `inline code`. Paragraphs are plain Markdown paragraphs. Explanation
  goes in Concept, never into Task, which stays one or two sentences. For a debugging exercise
  the skeleton is the buggy program; whether Symptom also names the cause depends on the
  batch's mode (below). The buggy program is the reference with only the bug changed:
  every other line already works, so fixing the bug is the whole task (no missing steps
  to write). {main_rule}
{tests} Work out every expected value from the question by hand on
  that test's data, never from your reference; keep each test's data small enough for
  that.
- hints_md: "## Useful functions" first (each relevant API, and any forbidden one, with a
  tiny example on different data and its exact output), then "## Hint 1", "## Hint 2",
  "## Hint 3", each more specific. Never the answer.
- data_files: sample input for file-level exercises, e.g. {"sample_input/readings.csv": ...}.
  Paths are relative to the exercise folder.
{reference}
{extra_files}{oracle}{oracle_test}
- mutants: 3 to 6 mistakes, each named m01_ to m06_ plus a short snake_case description
  (e.g. m01_average_of_averages), each given as replacements of exact text in {reference_field}
  (`old` must occur exactly once in it, copied character for character, quotes and
  indentation included). Most must be misconceptions of this knowledge
  point: the code a student writes who has a wrong idea of how the concept works \
{misconception}. At most one may be a plain slip such as > for >=; never arbitrary edits such
  as doubling a value. Each mutant must really be wrong on the tests' data (no equivalent
  mutants). `bug` reads "<the bug>, <what goes wrong>".{mutant_notes}

{test_data}

{how}

{code_rules}

{boundary}Return only the JSON object."""
_SLOT = re.compile(r"\{(\w+)\}")


def material(tag: str, text: str) -> str:
    """`text` between <tag> and </tag>, for a prompt that says such text is material to
    read, not instructions; a closing tag inside it is broken up, so the text cannot seem to
    end early (the same marking as materials/cache.as_material). It marks the boundary for
    the model and enforces nothing: what comes back is still validated."""
    return f"<{tag}>\n" + text.replace(f"</{tag}>", f"</ {tag}>") + f"\n</{tag}>"


def _fill(template: str, parts: dict[str, str]) -> str:
    """`template` with each {slot} named in `parts` replaced, in one pass: a part's own text
    is not searched again, and slots not in `parts` ({imports}, {oracle}) stay for later."""
    return _SLOT.sub(lambda m: parts.get(m.group(1), m.group(0)), template)


def _as_is(tag: str, text: str) -> str:
    """`text` unmarked: what material() stands in for in a language that does not mark it."""
    return text


def _marker(parts: LanguagePrompts):
    """How the language's user messages put text from outside the prompt (see
    LanguagePrompts.marks_material)."""
    return material if parts.marks_material else _as_is


def example(framework: str, interface: str) -> str:
    """The example exercise closest to the item, as its files."""
    root = EXAMPLES / EXAMPLE_FOR.get((framework, interface), DEFAULT_EXAMPLE)
    parts = [f"Example exercise ({root.name}), as files; your answer gives the same content "
             "as JSON fields, with mutants as replacements:"]
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        # Only the exercise's own files: not caches, not hidden files such as macOS's
        # .DS_Store (binary, git-ignored, so absent in CI but present on a Mac).
        if (not path.is_file() or "__pycache__" in rel.parts
                or any(part.startswith(".") for part in rel.parts)
                or path.name in ("validation.json", "question.md")):  # question.md: a copy
            continue
        try:
            # as_posix: the model sees exercise/student.py on Windows too, and writes paths
            # the same way back
            parts.append(f"=== {rel.as_posix()} ===\n{path.read_text(encoding='utf-8')}")
        except UnicodeDecodeError:
            continue
    return "\n\n".join(parts)


def system_prompt(conventions: str, oracle_interface: bool = False,
                  concept_lines: int = 50, extra_imports: Iterable[str] = (),
                  language: str = "python", c_flags: Iterable[str] = (),
                  c_headers: Iterable[str] = ()) -> str:
    """`oracle_interface`: another model writes the oracle (the `oracle` role is set).
    `concept_lines`: the most lines of a learn-mode Concept (a generation setting).
    `extra_imports`: the course's `[safety] allow_imports`, which the safety check lets
    through; named here too, or the model is told it may not use them (owner, 2026-11-06).
    Without any, the text is exactly as before, so a course's prompt, and what the provider
    has cached of it, do not change. The oracle's own prompt keeps the core modules only.
    `language`: whose parts fill the template; for C, `c_flags` (the course's compiler
    flags) and `c_headers` (its [safety] allow_headers) are named too."""
    parts = LANGUAGES[language]
    imports = ", ".join(sorted({m for m in CORE_IMPORTS if not m.startswith("_")}
                               | set(extra_imports)))
    oracle = parts.oracle_interface if oracle_interface else parts.oracle_whole
    spec = _fill(SPEC, parts.spec).replace("{imports}", imports).replace(
        "{oracle}", oracle).replace("{concept_lines}", str(concept_lines))
    # The compiler's flags and the extra headers: only a compiled language's parts have
    # these slots, so for Python both replacements find nothing.
    headers = sorted(c_headers)
    spec = spec.replace("{flags}", " ".join(c_flags)).replace(
        "{headers}", f" and {', '.join(headers)}" if headers else "")
    return "\n\n".join([
        spec,
        parts.api(),
        "Course conventions (facts about this course every exercise must respect):\n"
        + (conventions.strip() or "(none recorded)"),
    ])


# The batch mode's line in the item prompt: how much the question explains (decided
# 2026-10-24; docs/design.md, "Two modes").
MODES = {
    "learn": "learn: the student is meeting the topic. The Concept section is the knowledge "
             "point's condensed lecture (see the rules); a debugging Symptom may name the "
             "cause of the bug, not the fix. The student's "
             "code practises only the knowledge point: steps that are not the point (reading "
             "and cleaning input, formatting output) are given in the skeleton or kept trivial.",
    "review": "review: the student has learnt the topic. No Concept section; a debugging "
              "Symptom gives the symptom only, never the cause.",
}


def item_parts(item: PlanItem, context: str, mode: str = "learn",
               language: str = "python") -> tuple[str, str]:
    """The generate prompt for one item in its two parts, longest-shared first (see the
    module docstring): the course pages, which every item of the lecture shares, then the
    example exercise and the item itself. A language that marks material (C) marks the
    pages (Python's stays as calibrated)."""
    mark = _marker(LANGUAGES[language])
    pages = (
        "The course pages (one section per PDF page, `## p.N · slide S`; image slides "
        "were transcribed, so code there is as on the slide):\n\n"
        + mark("lecture", context)
        + "\n\n")
    own = (
        example(item.framework, item.interface)
        + f"\n\nWrite the exercise `{item.id}`.\n"
        f"Knowledge point: {item.knowledge_point}\n"
        f"From: {item.lecture}, slides {item.slides}\n"
        f"Type: {item.type}; interface: {item.interface}; framework: {item.framework}\n"
        f"Batch mode: {MODES[mode]}\n"
        + (f"Notes: {item.notes}\n" if item.notes else "")
    )
    return pages, own


def item_prompt(item: PlanItem, context: str, mode: str = "learn",
                language: str = "python") -> Prompt:
    """The generate prompt for one item: its two parts as one Prompt (a string)."""
    return Prompt(*item_parts(item, context, mode, language))


# Rule 1 failing means the reference and a test disagree; a week 4 item spent three
# repairs changing a right reference to match a test whose hand-computed value was wrong.
RULE_1_HINT = (
    "\n\nThe reference and a test disagree, and either can be wrong. Before changing "
    "anything, work out that test's expected value from the question by hand on its data; "
    "if the test is wrong, fix the test.")


def repair_prompt(item: PlanItem, context: str, draft_json: str, failure: str,
                  mode: str = "learn", unchanged: bool = False,
                  language: str = "python") -> Prompt:
    """`unchanged`: that answer repeated the one before it, which failed the same way. A
    language that marks material (C) marks the previous answer and the validator's report,
    since the report quotes what generated code printed. The pages stay the shared part,
    so a repair round reuses what the generate call cached."""
    parts = LANGUAGES[language]
    mark = _marker(parts)
    pages, own = item_parts(item, context, mode, language)
    return Prompt(
        pages,
        own
        + "\n\nYour previous answer:\n"
        + mark("previous_answer", draft_json)
        + "\n\nThe validator rejected it:\n"
        + mark("validator_report", failure)
        + (RULE_1_HINT if re.search(r"^rule 1 ", failure, re.MULTILINE) else "")
        # A surviving mutant: how this language's tests catch one.
        + (parts.rule_3_hint if re.search(r"^rule 3 .*survived", failure, re.MULTILINE)
           else "")
        + ("\n\nThat answer was identical to the one before it, which failed in the same "
           "way: returning it again cannot pass. Find what the failure points at and "
           "change it." if unchanged else "")
        + "\n\nStart with `diagnosis`: which test or rule failed, the right result worked out "
        "by hand from the question, and which file is wrong and why. Then fix the cause and "
        "return the whole exercise again. Change only what the fix needs; keep everything "
        "that already worked."
    )


def retry_prompt(item: PlanItem, context: str, failure: str, mode: str = "learn",
                 language: str = "python") -> Prompt:
    """After a reply that could not be read at all (cut off, or not JSON): there is no draft
    to repair, so the item is asked again, with what went wrong."""
    mark = _marker(LANGUAGES[language])
    pages, own = item_parts(item, context, mode, language)
    return Prompt(
        pages,
        own
        + "\n\nA previous attempt gave no usable answer: "
        + mark("validator_report", failure)
        + "\nAnswer again from the start. Keep your reasoning short and spend the output on "
        "the JSON."
    )


# The oracle model's system prompt: it reads only the question, the interface and the
# sample data, so its oracle.py is a reading of the question independent of the reference.
ORACLE_TEMPLATE = """You write the plain-Python specification (oracle) of a programming exercise:
{compared} You get the exercise's question, the interface to
implement and its sample data, nothing else.
- Read the question literally: its input format, edge cases, rounding, ordering and output
  format decide the result. Where it is silent, follow the interface's docstrings.
- Write every function body of the interface; keep its names, signatures and imports.
- Plain Python only, imports only from {imports} (no Spark, MRJob, numpy or pandas), no
  files, no network, no randomness. Clear loops over clever code.
- No absolute or `~` paths, no eval / exec / __import__, no dunder tricks.

{boundary}Return only the JSON object."""


# Modules an oracle may import: the safety check's core list without the test-only ones.
ORACLE_IMPORTS = sorted(m for m in CORE_IMPORTS if not m.startswith("_") and m not in {
    "hypothesis", "oracle", "pytest", "student", "tutor"})


def oracle_spec(language: str = "python") -> str:
    """The oracle's system prompt for the language, with the modules it may import."""
    parts = LANGUAGES[language]
    spec = _fill(ORACLE_TEMPLATE, {"compared": parts.oracle_compared,
                                   "boundary": parts.oracle_boundary})
    return spec.replace("{imports}", ", ".join(ORACLE_IMPORTS))


def oracle_prompt(question: str, interface: str, data_files: dict[str, str],
                  sample_chars: int = 2000, language: str = "python") -> str:
    """The oracle model's user message; each sample data file is cut to `sample_chars`. A
    language that marks material (C) marks the question, the interface and the samples."""
    parts = LANGUAGES[language]
    mark = _marker(parts)
    samples = "\n\n".join(
        f"=== {name} ===\n{text[:sample_chars]}" + ("\n..." if len(text) > sample_chars else "")
        for name, text in sorted(data_files.items()))
    return (
        f"The question ({parts.question_place}):\n" + mark("question", question)
        + "\n\nThe interface to implement (oracle.py):\n" + mark("interface", interface)
        + ("\n\nSample data:\n" + mark("samples", samples) if samples else "")
    )


# The plan model's system prompt: choose knowledge points from coverage.md's gaps, one
# plan line each; plan_problems() then checks what code can check.
PLAN_TEMPLATE = """You plan a batch of programming exercises for a university course. Choose
what to practise; another step writes each exercise.

Rules:
- Pick knowledge points from the coverage table rows marked `gap-code` for the lectures
  given (`gap-tracing`, `gap-design` and `concept` rows are not code exercises). The
  text in brackets after gap-code says what an exercise there could check.
- One exercise per knowledge point; the batch covers different points, and none repeats
  an existing exercise (listed below with their titles and topics). A task the student
  has already solved with another API (word count again, a per-key average again) is
  repeated only when the new API is the whole point; say so in `reason`.
- The batch mode (below) sets how much one exercise combines.
- Use the page list to choose the slide range each exercise draws on; `slides` is a range
  of slide footers such as "4.15-4.24", and `lecture` is one of the lecture names given.
{kinds}
- `notes` says what the exercise must check (the concept, edge cases, how the "how" is
  tested); `reason` says in one sentence why it was chosen.
- Follow the owner's preferences; they override these rules where they conflict.
- ids are short kebab-case names of what the exercise does.

{boundary}Return only the JSON object."""


def plan_spec(language: str = "python") -> str:
    """The plan model's system prompt for the language."""
    parts = LANGUAGES[language]
    return _fill(PLAN_TEMPLATE, {"kinds": parts.plan_kinds, "boundary": parts.plan_boundary})


PLAN_SPEC = plan_spec()  # Python's, as tests/test_prompt_pins.py pins it


# How much one exercise combines, by batch mode (decided 2026-10-29: the owner found a
# learn exercise that chained a UDF, a split and a lowercase too hard to study from).
PLAN_MODES = {
    "learn": "learn: one knowledge point per exercise, never merged with another. A slide "
             "example that chains several ideas becomes several exercises, each practising "
             "one; the steps that are not its point are given in its skeleton.",
    "review": "review: an exercise may combine knowledge points from the lectures given, "
              "as a final exam question does.",
}


def plan_prompt(lectures: list[str], coverage: str, pages: str, existing: list[str],
                preferences: str, count: int | None, max_count: int = 6, feedback: str = "",
                previous: str = "", problems: list[str] | None = None,
                mode: str = "learn", language: str = "python") -> str:
    """`existing`: one line per existing exercise, "id: title [topic]". A language that marks
    material (C) marks what comes from files (the coverage table, the pages, the existing
    exercises, the previous plan); the owner's preferences and feedback are not."""
    if LANGUAGES[language].marks_material:
        coverage, pages = material("coverage", coverage), material("pages", pages)
        existing = [material("existing", "\n".join(existing) or "(none)")]
        previous = material("previous_plan", previous) if previous else ""
    how_many = (f"Plan {count} exercises" if count else
                "Plan one exercise per knowledge point that deserves its own exercise "
                f"(leave out what the existing exercises already practise), at most {max_count}")
    parts = [
        f"{how_many} from: {', '.join(lectures)}.",
        "Batch mode: " + PLAN_MODES[mode],
        "Owner's preferences: " + (preferences.strip() or "(none)"),
        "Existing exercises (do not reuse an id):\n" + ("\n".join(existing) or "(none)"),
        "Coverage table:\n" + coverage,
        "Pages of the lectures (PDF page, slide footer, title):\n" + pages,
    ]
    if previous:
        parts.append("Your previous plan:\n" + previous)
    if feedback:
        parts.append("The owner sent it back with this feedback; follow it:\n" + feedback)
    if problems:
        parts.append("It also had these problems; fix them:\n- " + "\n- ".join(problems))
    return "\n\n".join(parts)
