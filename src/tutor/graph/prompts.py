"""Prompts of the generation graph.

Ordered for prompt caching, which reuses the longest prefix a provider has seen: the system
prompt is the same for every item of a course (format rules, the testkit API, the course
conventions); the user message then gives the course pages (shared by the items of one
lecture), the example exercise (shared by items of one framework and interface), and only
then the item and, when repairing, the failed draft. The course
conventions come from the workspace's conventions.md, never from this file: everything
here holds for any course.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from tutor import testkit
from tutor.graph.state import PlanItem
from tutor.validation.safety import CORE_IMPORTS

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
# The example shown for each (framework, interface): the closest hand-made, validated
# exercise. There is no plain-Python example yet; those items get the function-level one.
EXAMPLE_FOR = {
    ("pyspark-rdd", "function"): "station-average-rdd",
    ("pyspark-rdd", "file"): "station-max-file",
    ("pyspark-df", "file"): "station-monthly-df",
    ("mrjob", "file"): "station-condition-share",
}
DEFAULT_EXAMPLE = "station-average-rdd"

# The generate / repair system prompt: what the validator checks, then each file of the
# reply (ExerciseDraft's fields) with its rules. {imports}, {oracle} and {concept_lines}
# are filled in by system_prompt().
SPEC = """You write programming exercises for a university course, one exercise at a time.
Each exercise is checked by an automatic validator before anyone sees it; write it so that
it passes:
- rule 0: metadata fits the format below;
- safe: a static check rejects unsafe code (see "Code rules");
- 1: the reference passes test_student.py;
- 2: the skeleton (student.py as given to the student) fails test_student.py by failing
  tests, not by crashing before they run;
- 3: every mutant fails test_student.py (the tests catch each bug);
- 4: the reference agrees with oracle.py on random inputs (test_oracle.py, hypothesis);
- 5: Spark: the result does not depend on the number of partitions (TUTOR_PARTITIONS);
- api: constraints in metadata (forbidden calls / imports, required definitions) hold.

Files (fields of the JSON you return):
- metadata: the exercise's metadata. `source` is an object naming the lecture, the slide
  footers and the PDF pages it comes from (the PDF page is the `p.N` of a section heading
  below, the footer its `slide S`), and the teaching assumptions (choices of this
  exercise, not rules of the framework). Never "synthetic": that is only for the example,
  which is not from a course.
- student_py: the question in the module docstring, then the skeleton: imports, the
  function or class signatures, and `raise NotImplementedError` (or `pass` with a TODO)
  where the student writes code. The docstring is a raw string, r\"\"\"...\"\"\", so LaTeX
  keeps its backslashes. The question is Markdown, read as question.md, as in the example
  exercise below: a `# <title>` line, then these `## ` sections in this order:
    ## Concept     learn mode only: the knowledge point as a condensed lecture, at most
                   {concept_lines} lines, so the student need not go back to the slides.
                   In `### ` parts: "### Why it is needed" first (at most 3 lines: what
                   the technique does and why it is needed), then one part per idea, its
                   slides in the heading ("### The update rule (slides 4.2-4.3)"). In the
                   slides' words and terms; a slide's own example may be shown on data
                   other than this exercise's. Steps as a numbered list (nested for
                   sub-steps) or as pseudocode in a ```text block: the slides' steps,
                   language-neutral (`for i = 1..d: increment C[i][h_i(x)]`), never
                   runnable Python and never this exercise's names, signature or edge
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
    ## Run         file interface only: the command line in a ```bash block
                   (`python student.py <input> <output>`)
  Do not write the line under the title, a contents line, rules (---) or "How to run":
  code adds them. Math is LaTeX: $...$ inline, $$...$$ on lines of its own, never inside a
  list item; \\lbrace and \\rbrace, not \\{ and \\}. Python expressions that say what the
  code computes stay `inline code`. Paragraphs are plain Markdown paragraphs. Explanation
  goes in Concept, never into Task, which stays one or two sentences. For a debugging exercise
  the skeleton is the buggy program; whether Symptom also names the cause depends on the
  batch's mode (below). The buggy program is the reference with only the bug changed:
  every other line already works, so fixing the bug is the whole task (no missing steps
  to write). It ends with an `if __name__ == "__main__":` block, so `python student.py`
  runs on its own (validation rule "main"): for the function interface it calls the
  functions on the question's example and prints the results (a Spark one builds its own
  session and stops it, with PYSPARK_PYTHON set to sys.executable); for the file interface
  it takes the input and output paths from sys.argv, as the slides' standalone programs do.
  Only code that a framework launches and that has no main of its own (a Hadoop submission,
  a project's entry point) leaves the block out and sets metadata `standalone` to false.
- test_student_py: pytest tests importing `student`, using tutor.testkit (API below). Spark
  tests read the partition count with testkit.partitions(); file-level tests split their
  input into that many files. Work out every expected value from the question by hand on
  that test's data, never from your reference; keep each test's data small enough for
  that.
- hints_md: "## Useful functions" first (each relevant API, and any forbidden one, with a
  tiny example on different data and its exact output), then "## Hint 1", "## Hint 2",
  "## Hint 3", each more specific. Never the answer.
- data_files: sample input for file-level exercises, e.g. {"sample_input/readings.csv": ...}.
  Paths are relative to the exercise folder.
- reference_py: the worked solution, as numbered steps (`# Step N: what and why`), each with a
  comment showing a record after that step on the question's example data. Never one long
  chain. Write it the way a good student would with the slides' APIs; never force an API
  into a role it does not fit (e.g. an action's result sent back to the cluster to join).
  It ends with the same `__main__` block as student_py.
{oracle}test_oracle_py: a hypothesis test comparing student.py with oracle.py
  (`@settings(deadline=None, derandomize=True)`, a moderate max_examples).
- mutants: 3 to 6 mistakes, each named m01_ to m06_ plus a short snake_case description
  (e.g. m01_average_of_averages), each given as replacements of exact text in reference_py
  (`old` must occur exactly once in it, copied character for character, quotes and
  indentation included). Most must be misconceptions of this knowledge
  point: the code a student writes who has a wrong idea of how the concept works (for
  example, for lazy evaluation: an accumulator updated in a transformation that no action
  ever runs). At most one may be a plain slip such as > for >=; never arbitrary edits such
  as doubling a value. Each mutant must really be wrong on the tests' data (no equivalent
  mutants). `bug` reads "<the bug>, <what goes wrong>".

Test data must break the regularities a bug relies on: keys not already sorted, unequal
record counts per map task or partition (a mean of means then differs), ties when there is
a tie-break rule, values that make integer division and rounding visible.

Making "how" testable, not only the output: count reads of an input with an accumulator
(persist); a dict that can be pickled only inside a spied broadcast; monkeypatch a method to
count calls (DataFrame vs SQL); check module-level constants directly.

Code rules (the "safe" check): imports only from {imports} and the exercise's framework
(mrjob; pyspark); `os` only for os.environ / os.path, `sys` only for
argv / exit / stdin / stdout / stderr / executable; no eval / exec / compile / __import__;
no dunder tricks; no literal absolute or `~` paths and no `..`; no RDD.pipe or MRJob
*_cmd options; no conftest.py or pytest.ini.

Return only the JSON object."""


# The two ways oracle_py is written (SPEC's {oracle}): whole, by the generating model, or
# as an interface whose bodies another model writes from the question alone, so rule 4
# compares two independent readings of the question (docs/design.md, "Independent checks").
ORACLE_WHOLE = (
    "- oracle_py: a plain-Python specification of the same result, written independently of\n"
    "  the reference (no Spark / MRJob), and ")
ORACLE_INTERFACE = (
    "- oracle_py: only the interface of a plain-Python specification of the same result (no\n"
    "  Spark / MRJob): imports, the functions test_oracle_py calls, each with type hints, a\n"
    "  docstring saying exactly what goes in and what comes out, and the body\n"
    "  `raise NotImplementedError`. Another model writes the bodies from the question in\n"
    "  student.py's docstring, this interface and the sample data alone; it never sees your\n"
    "  reference. So the question must say everything the result depends on. Then\n"
    "  ")


def testkit_api() -> str:
    """The public functions of tutor.testkit with their signatures and docs, read from the
    module itself so the prompt never drifts from the code."""
    lines = []
    for name, func in inspect.getmembers(testkit, inspect.isfunction):
        if func.__module__ == testkit.__name__ and not name.startswith("_"):
            doc = inspect.getdoc(func) or ""
            lines.append(f"testkit.{name}{inspect.signature(func)}\n    "
                         + doc.replace("\n", "\n    "))
    return "\n".join(lines)


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
            parts.append(f"=== {rel} ===\n{path.read_text(encoding='utf-8')}")
        except UnicodeDecodeError:
            continue
    return "\n\n".join(parts)


def system_prompt(conventions: str, oracle_interface: bool = False,
                  concept_lines: int = 50) -> str:
    """`oracle_interface`: another model writes the oracle (the `oracle` role is set).
    `concept_lines`: the most lines of a learn-mode Concept (a generation setting)."""
    imports = ", ".join(sorted(m for m in CORE_IMPORTS if not m.startswith("_")))
    spec = SPEC.replace("{imports}", imports).replace(
        "{oracle}", ORACLE_INTERFACE if oracle_interface else ORACLE_WHOLE).replace(
        "{concept_lines}", str(concept_lines))
    return "\n\n".join([
        spec,
        "tutor.testkit API:\n" + testkit_api(),
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


def item_prompt(item: PlanItem, context: str, mode: str = "learn") -> str:
    """The generate prompt for one item, longest-shared part first (see the module
    docstring): the course pages, the example exercise, then the item itself."""
    return (
        "The course pages (one section per PDF page, `## p.N · slide S`; image slides "
        "were transcribed, so code there is as on the slide):\n\n" + context
        + "\n\n" + example(item.framework, item.interface)
        + f"\n\nWrite the exercise `{item.id}`.\n"
        f"Knowledge point: {item.knowledge_point}\n"
        f"From: {item.lecture}, slides {item.slides}\n"
        f"Type: {item.type}; interface: {item.interface}; framework: {item.framework}\n"
        f"Batch mode: {MODES[mode]}\n"
        + (f"Notes: {item.notes}\n" if item.notes else "")
    )


# Rule 1 failing means the reference and a test disagree; a week 4 item spent three
# repairs changing a right reference to match a test whose hand-computed value was wrong.
RULE_1_HINT = (
    "\n\nThe reference and a test disagree, and either can be wrong. Before changing "
    "anything, work out that test's expected value from the question by hand on its data; "
    "if the test is wrong, fix the test.")


# A surviving mutant whose output equals the reference's (groupByKey then sum, instead of
# reduceByKey) went through three repairs on a week 4 item: no output test can catch it.
RULE_3_HINT = (
    "\n\nA mutant that survives shows a gap in the tests: add a case its bug gets wrong, or, "
    "when the bug is in how the result is computed and not in the result, catch it as in "
    "\"Making how testable\" (a spy or monkeypatch on the API). A mutant whose result equals "
    "the reference's on every input is not a mistake the tests can show: replace it.")


def repair_prompt(item: PlanItem, context: str, draft_json: str, failure: str,
                  mode: str = "learn", unchanged: bool = False) -> str:
    """`unchanged`: that answer repeated the one before it, which failed the same way."""
    return (
        item_prompt(item, context, mode)
        + "\n\nYour previous answer:\n" + draft_json
        + "\n\nThe validator rejected it:\n" + failure
        + (RULE_1_HINT if re.search(r"^rule 1 ", failure, re.MULTILINE) else "")
        + (RULE_3_HINT if re.search(r"^rule 3 .*survived", failure, re.MULTILINE) else "")
        + ("\n\nThat answer was identical to the one before it, which failed in the same "
           "way: returning it again cannot pass. Find what the failure points at and "
           "change it." if unchanged else "")
        + "\n\nStart with `diagnosis`: which test or rule failed, the right result worked out "
        "by hand from the question, and which file is wrong and why. Then fix the cause and "
        "return the whole exercise again. Change only what the fix needs; keep everything "
        "that already worked."
    )


def retry_prompt(item: PlanItem, context: str, failure: str, mode: str = "learn") -> str:
    """After a reply that could not be read at all (cut off, or not JSON): there is no draft
    to repair, so the item is asked again, with what went wrong."""
    return (
        item_prompt(item, context, mode)
        + "\n\nA previous attempt gave no usable answer: " + failure
        + "\nAnswer again from the start. Keep your reasoning short and spend the output on "
        "the JSON."
    )


# The oracle model's system prompt: it reads only the question, the interface and the
# sample data, so its oracle.py is a reading of the question independent of the reference.
ORACLE_SPEC = """You write the plain-Python specification (oracle) of a programming exercise:
the obviously correct, slow-but-simple result that a Spark or MapReduce solution is
compared against on random inputs. You get the exercise's question, the interface to
implement and its sample data, nothing else.
- Read the question literally: its input format, edge cases, rounding, ordering and output
  format decide the result. Where it is silent, follow the interface's docstrings.
- Write every function body of the interface; keep its names, signatures and imports.
- Plain Python only, imports only from {imports} (no Spark, MRJob, numpy or pandas), no
  files, no network, no randomness. Clear loops over clever code.
- No absolute or `~` paths, no eval / exec / __import__, no dunder tricks.

Return only the JSON object."""


# Modules an oracle may import: the safety check's core list without the test-only ones.
ORACLE_IMPORTS = sorted(m for m in CORE_IMPORTS if not m.startswith("_") and m not in {
    "hypothesis", "oracle", "pytest", "student", "tutor"})


def oracle_spec() -> str:
    """ORACLE_SPEC with the modules an oracle may import filled in."""
    return ORACLE_SPEC.replace("{imports}", ", ".join(ORACLE_IMPORTS))


def oracle_prompt(question: str, interface: str, data_files: dict[str, str],
                  sample_chars: int = 2000) -> str:
    """The oracle model's user message; each sample data file is cut to `sample_chars`."""
    samples = "\n\n".join(
        f"=== {name} ===\n{text[:sample_chars]}" + ("\n..." if len(text) > sample_chars else "")
        for name, text in sorted(data_files.items()))
    return (
        "The question (student.py's docstring):\n" + question
        + "\n\nThe interface to implement (oracle.py):\n" + interface
        + ("\n\nSample data:\n" + samples if samples else "")
    )


# The plan model's system prompt: choose knowledge points from coverage.md's gaps, one
# plan line each; plan_problems() then checks what code can check.
PLAN_SPEC = """You plan a batch of programming exercises for a university course. Choose
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
- type: implementation (write it), completion (fill in parts), debugging (fix a given
  program that is wrong); interface: function (a function on an RDD / DataFrame / list)
  or file (a program reading input files, writing output); framework: one of python,
  mrjob, pyspark-rdd, pyspark-df, the one the slides use.
- `notes` says what the exercise must check (the concept, edge cases, how the "how" is
  tested); `reason` says in one sentence why it was chosen.
- Follow the owner's preferences; they override these rules where they conflict.
- ids are short kebab-case names of what the exercise does.

Return only the JSON object."""


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
                mode: str = "learn") -> str:
    """`existing`: one line per existing exercise, "id: title [topic]"."""
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
