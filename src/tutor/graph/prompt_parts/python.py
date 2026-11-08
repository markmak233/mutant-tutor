"""Python's parts of the generation prompts (see prompt_parts/__init__.py): the exercise's
code is Python, and its tests use tutor.testkit. Calibrated: every text here is the prompt as
measured, byte for byte (tests/test_prompt_pins.py); change one only with a measurement.
"""

from __future__ import annotations

import inspect

from tutor import testkit
from tutor.graph.prompt_parts import LanguagePrompts

# SPEC's slots, filled in by prompts.system_prompt(); {imports} in the code rules is filled
# there too.
SPEC_PARTS = {
    "language": "Python",
    "rules": """\
- rule 0: metadata fits the format below;
- safe: a static check rejects unsafe code (see "Code rules");
- 1: the reference passes test_student.py;
- 2: the skeleton (student.py as given to the student) fails test_student.py by failing
  tests, not by crashing before they run;
- 3: every mutant fails test_student.py (the tests catch each bug);
- 4: the reference agrees with oracle.py on random inputs (test_oracle.py, hypothesis);
- 5: Spark: the result does not depend on the number of partitions (TUTOR_PARTITIONS);
- api: constraints in metadata (forbidden calls / imports, required definitions) hold.""",
    "student": """\
- student_py: the question in the module docstring, then the skeleton: imports, the
  function or class signatures, and `raise NotImplementedError` (or `pass` with a TODO)
  where the student writes code. The docstring is a raw string, r\"\"\"...\"\"\", so LaTeX
  keeps its backslashes. The question is Markdown, read as question.md, as in the example
  exercise below: a `# <title>` line, then these `## ` sections in this order:""",
    "run_section": """\
    ## Run         file interface only: the command line in a ```bash block
                   (`python student.py <input> <output>`)""",
    "main_rule": """\
It ends with an `if __name__ == "__main__":` block, so `python student.py`
  runs on its own (validation rule "main"): for the function interface it calls the
  functions on the question's example and prints the results (a Spark one builds its own
  session and stops it, with PYSPARK_PYTHON set to sys.executable); for the file interface
  it takes the input and output paths from sys.argv, as the slides' standalone programs do.
  Only code that a framework launches and that has no main of its own (a Hadoop submission,
  a project's entry point) leaves the block out and sets metadata `standalone` to false.""",
    "tests": """\
- test_student_py: pytest tests importing `student`, using tutor.testkit (API below). Spark
  tests read the partition count with testkit.partitions(); file-level tests split their
  input into that many files.""",
    "reference": """\
- reference_py: the worked solution, as numbered steps (`# Step N: what and why`), each with a
  comment showing a record after that step on the question's example data. Never one long
  chain. Write it the way a good student would with the slides' APIs; never force an API
  into a role it does not fit (e.g. an action's result sent back to the cluster to join).
  It ends with the same `__main__` block as student_py.""",
    "extra_files": "",
    "oracle_test": """\
test_oracle_py: a hypothesis test comparing student.py with oracle.py
  (`@settings(deadline=None, derandomize=True)`, a moderate max_examples).""",
    "reference_field": "reference_py",
    "misconception": """\
(for
  example, for lazy evaluation: an accumulator updated in a transformation that no action
  ever runs)""",
    "mutant_notes": "",
    "test_data": """\
Test data must break the regularities a bug relies on: keys not already sorted, unequal
record counts per map task or partition (a mean of means then differs), ties when there is
a tie-break rule, values that make integer division and rounding visible.""",
    "how": """\
Making "how" testable, not only the output: count reads of an input with an accumulator
(persist); a dict that can be pickled only inside a spied broadcast; monkeypatch a method to
count calls (DataFrame vs SQL); check module-level constants directly.""",
    "code_rules": """\
Code rules (the "safe" check): imports only from {imports} and the exercise's framework
(mrjob; pyspark); `os` only for os.environ / os.path, `sys` only for
argv / exit / stdin / stdout / stderr / executable; no eval / exec / compile / __import__;
no dunder tricks; no literal absolute or `~` paths and no `..`; no RDD.pipe or MRJob
*_cmd options; no conftest.py or pytest.ini.""",
    "boundary": "",
}

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

# What the oracle is compared with.
ORACLE_COMPARED = ("the obviously correct, slow-but-simple result that a Spark or MapReduce "
                   "solution is\ncompared against on random inputs.")

# The kinds of exercise a plan may choose.
PLAN_KINDS = """\
- type: implementation (write it), completion (fill in parts), debugging (fix a given
  program that is wrong); interface: function (a function on an RDD / DataFrame / list)
  or file (a program reading input files, writing output); framework: one of python,
  mrjob, pyspark-rdd, pyspark-df, the one the slides use."""

# A surviving mutant whose output equals the reference's (groupByKey then sum, instead of
# reduceByKey) went through three repairs on a week 4 item: no output test can catch it.
RULE_3_HINT = (
    "\n\nA mutant that survives shows a gap in the tests: add a case its bug gets wrong, or, "
    "when the bug is in how the result is computed and not in the result, catch it as in "
    "\"Making how testable\" (a spy or monkeypatch on the API). A mutant whose result equals "
    "the reference's on every input is not a mistake the tests can show: replace it.")


def api() -> str:
    """The public functions of tutor.testkit with their signatures and docs, read from the
    module itself so the prompt never drifts from the code."""
    lines = []
    for name, func in inspect.getmembers(testkit, inspect.isfunction):
        if func.__module__ == testkit.__name__ and not name.startswith("_"):
            doc = inspect.getdoc(func) or ""
            lines.append(f"testkit.{name}{inspect.signature(func)}\n    "
                         + doc.replace("\n", "\n    "))
    return "tutor.testkit API:\n" + "\n".join(lines)


PROMPTS = LanguagePrompts(
    spec=SPEC_PARTS,
    oracle_whole=ORACLE_WHOLE,
    oracle_interface=ORACLE_INTERFACE,
    oracle_compared=ORACLE_COMPARED,
    oracle_boundary="",
    plan_kinds=PLAN_KINDS,
    plan_boundary="",
    rule_3_hint=RULE_3_HINT,
    question_place="student.py's docstring",
    marks_material=False,  # calibrated before material was marked; stays as measured
    # Plain-Python items saw the Spark function-level example until 2026-11-05, with a
    # SparkContext fixture and a shuffle to explain; a file-level one has no example of its
    # own.
    examples={
        ("python", "function"): "station-longest-rise",
        ("python", "file"): "station-longest-rise",
        ("pyspark-rdd", "function"): "station-average-rdd",
        ("pyspark-rdd", "file"): "station-max-file",
        ("pyspark-df", "file"): "station-monthly-df",
        ("mrjob", "file"): "station-condition-share",
    },
    api=api,
)
