"""Validation rules 1-5 and the static API check.

Unit tests build tiny pure-Python exercises in a temp dir, so they need no Spark. The
`spark`-marked tests validate the real examples end to end (about 7.5 min): run them with
`pytest -m spark`.
"""

import json
import os
from pathlib import Path

import pytest

from tutor.config import load_env
from tutor.validation import ExerciseError, Status, load_exercise, validate
from tutor.validation.__main__ import format_report, main
from tutor.validation.api import count_uses, find_violations

TESTS = (
    "from student import add\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n"
    "def test_negative():\n    assert add(-1, 1) == 0\n"
)
REFERENCE = "def add(a, b):\n    return a + b\n"
SKELETON = "def add(a, b):\n    raise NotImplementedError\n"
MUTANT_SUB = "def add(a, b):\n    return a - b\n"
ORACLE = "def add(a, b):\n    return a + b\n"
ORACLE_TEST = (
    "from hypothesis import given, settings, strategies as st\n"
    "import oracle\nfrom student import add\n\n"
    "@settings(max_examples=50, deadline=None, derandomize=True)\n"
    "@given(st.integers(-10**6, 10**6), st.integers(-10**6, 10**6))\n"
    "def test_matches_oracle(a, b):\n    assert add(a, b) == oracle.add(a, b)\n"
)

# A partition-aware mini exercise: the test cuts 1..8 into TUTOR_PARTITIONS chunks, the way
# Spark tests split their input, and the code must total all chunks.
PART_TESTS = (
    "import os\nfrom student import total\n\n"
    "PARTS = int(os.environ.get('TUTOR_PARTITIONS', '2'))\n\n"
    "def test_total():\n"
    "    values = list(range(1, 9))\n"
    "    size = -(-len(values) // PARTS)\n"
    "    chunks = [values[i:i + size] for i in range(0, len(values), size)]\n"
    "    assert total(chunks) == 36\n"
)
PART_REFERENCE = "def total(chunks):\n    return sum(sum(c) for c in chunks)\n"
PART_SKELETON = "def total(chunks):\n    raise NotImplementedError\n"
FIRST_ONLY = "def total(chunks):\n    return sum(chunks[0])\n"  # right only with 1 chunk
FIRST_TWO = "def total(chunks):\n    return sum(sum(c) for c in chunks[:2])\n"  # wrong only at 4
PART_ORACLE = "def total(values):\n    return sum(values)\n"
PART_ORACLE_TEST = (
    "from hypothesis import given, settings, strategies as st\n"
    "import oracle\nfrom student import total\n\n"
    "@settings(max_examples=50, deadline=None, derandomize=True)\n"
    "@given(st.lists(st.integers(-100, 100)), st.sampled_from([1, 2, 4]))\n"
    "def test_matches_oracle(values, parts):\n"
    "    size = -(-len(values) // parts) or 1\n"
    "    chunks = [values[i:i + size] for i in range(0, len(values), size)]\n"
    "    assert total(chunks) == oracle.total(values)\n"
)


def make_exercise(tmp_path, *, tests=TESTS, reference=REFERENCE, skeleton=SKELETON,
                  mutants=None, extra_files=None, metadata=None,
                  oracle=ORACLE, oracle_test=ORACLE_TEST):
    """Write an exercise/ + solution/ pair like the examples, return both paths.
    oracle / oracle_test = None leaves that file out."""
    mutants = {"m01_sub": MUTANT_SUB} if mutants is None else mutants
    exercise, solution = tmp_path / "exercise", tmp_path / "solution"
    (solution / "mutants").mkdir(parents=True)
    exercise.mkdir()
    (exercise / "test_student.py").write_text(tests)
    (exercise / "student.py").write_text(skeleton)
    (exercise / "hints.md").write_text("# Hints\n")
    for name, text in (extra_files or {}).items():
        (exercise / name).write_text(text)
    (solution / "metadata.json").write_text(json.dumps({"id": "demo", **(metadata or {})}))
    (solution / "reference.py").write_text(reference)
    (solution / "skeleton.py").write_text(skeleton)
    for name, text in mutants.items():
        (solution / "mutants" / f"{name}.py").write_text(text)
    if oracle is not None:
        (solution / "oracle.py").write_text(oracle)
    if oracle_test is not None:
        (solution / "test_oracle.py").write_text(oracle_test)
    return exercise, solution


def make_partition_exercise(tmp_path, *, reference=PART_REFERENCE, mutants=None, metadata=None):
    mutants = {"m01_first_only": FIRST_ONLY} if mutants is None else mutants
    return make_exercise(
        tmp_path, tests=PART_TESTS, reference=reference, skeleton=PART_SKELETON,
        mutants=mutants, metadata={"partition_check": True, **(metadata or {})},
        oracle=PART_ORACLE, oracle_test=PART_ORACLE_TEST,
    )


def run(exercise_paths):
    return validate(load_exercise(*exercise_paths))


# --- rules 1-3 -------------------------------------------------------------------------


def test_valid_exercise(tmp_path):
    """Reference passes, skeleton fails its tests, the one mutant is killed, the reference
    matches the oracle; rule 5 and the API check do not apply without metadata asking."""
    report = run(make_exercise(tmp_path))

    assert report.valid
    assert [report.result(r).status for r in ("1", "2", "3", "4")] == [Status.OK] * 4
    assert report.result("5").status is Status.NOT_APPLICABLE
    assert report.result("api").status is Status.NOT_APPLICABLE
    assert "1/1 killed" in report.result("3").detail


def test_broken_reference_skips_the_rules_that_run_tests(tmp_path):
    """If the reference fails, rules 2-5 would prove nothing, so they are skipped."""
    wrong = "def add(a, b):\n    return a * b\n"
    report = run(make_exercise(tmp_path, reference=wrong))

    assert report.result("1").status is Status.FAIL
    assert [report.result(r).status for r in ("2", "3", "4", "5")] == [Status.SKIPPED] * 4
    assert not report.valid


def test_skeleton_that_passes_breaks_rule_2(tmp_path):
    """Tests that an unfinished skeleton can pass do not check the student's work."""
    report = run(make_exercise(tmp_path, skeleton=REFERENCE))

    assert report.result("2").status is Status.FAIL
    assert "skeleton passes" in report.result("2").detail


def test_skeleton_that_cannot_import_breaks_rule_2(tmp_path):
    """A skeleton with a syntax error crashes before any test runs: ERROR, not a real failure."""
    broken = "def add(a, b)\n    raise NotImplementedError\n"
    report = run(make_exercise(tmp_path, skeleton=broken))

    assert report.result("2").status is Status.FAIL
    assert "errors before any test runs" in report.result("2").detail


def test_surviving_mutant_is_named(tmp_path):
    """b + a behaves exactly like a + b, so no test can kill it; the report names it."""
    mutants = {"m01_sub": MUTANT_SUB, "m02_swapped": "def add(a, b):\n    return b + a\n"}
    rule3 = run(make_exercise(tmp_path, mutants=mutants)).result("3")

    assert rule3.status is Status.FAIL
    assert "1/2 killed" in rule3.detail
    assert "m02_swapped" in rule3.detail


def test_mutant_that_cannot_import_is_reported(tmp_path):
    """A mutant that does not even import tests nothing about the tests."""
    mutants = {"m01_sub": MUTANT_SUB, "m02_typo": "def add(a, b)\n    return a\n"}
    rule3 = run(make_exercise(tmp_path, mutants=mutants)).result("3")

    assert rule3.status is Status.FAIL
    assert "cannot run" in rule3.detail
    assert "m02_typo" in rule3.detail


def test_no_mutants_breaks_rule_3(tmp_path):
    rule3 = run(make_exercise(tmp_path, mutants={})).result("3")

    assert rule3.status is Status.FAIL
    assert "no mutants" in rule3.detail


def test_data_files_are_copied_but_student_and_hints_are_not(tmp_path):
    """Files the tests read (sample data) come along; student.py is always the candidate."""
    tests = (
        "from pathlib import Path\nfrom student import add\n\n"
        "def test_from_file():\n"
        "    a, b, want = map(int, Path('data.txt').read_text().split())\n"
        "    assert add(a, b) == want\n\n"
        "def test_no_hints_copied():\n    assert not Path('hints.md').exists()\n"
    )
    exercise, solution = make_exercise(tmp_path, tests=tests, extra_files={"data.txt": "2 3 5"})
    files = load_exercise(exercise, solution).run_files(solution / "reference.py")

    assert files["student.py"] == solution / "reference.py"
    assert "hints.md" not in files
    assert run((exercise, solution)).valid


def test_missing_reference_is_a_clear_error(tmp_path):
    exercise, solution = make_exercise(tmp_path)
    (solution / "reference.py").unlink()

    with pytest.raises(ExerciseError, match="reference.py"):
        load_exercise(exercise, solution)


# --- rule 4: differential testing -------------------------------------------------------


def test_reference_wrong_beyond_the_tested_inputs_breaks_rule_4(tmp_path):
    """The tests only try small numbers, so rule 1 passes; random inputs find the input
    where the reference stops being correct, and the report shows it."""
    narrow = "def add(a, b):\n    return a + b if a < 1000 else 0\n"
    report = run(make_exercise(tmp_path, reference=narrow))

    assert report.result("1").status is Status.OK
    assert report.result("4").status is Status.FAIL
    assert "Failing test case" in report.result("4").detail
    assert "a=1000" in report.result("4").detail  # hypothesis shrinks to the boundary


def test_missing_oracle_breaks_rule_4(tmp_path):
    report = run(make_exercise(tmp_path, oracle=None, oracle_test=None))

    assert report.result("4").status is Status.FAIL
    assert "no oracle.py" in report.result("4").detail


def test_differential_run_uses_the_oracle_test_not_the_student_tests(tmp_path):
    exercise, solution = make_exercise(tmp_path)
    files = load_exercise(exercise, solution).differential_files()

    assert files["student.py"] == solution / "reference.py"
    assert "test_student.py" not in files
    assert files["test_oracle.py"] == solution / "test_oracle.py"


# --- rule 5 and partition-dependent mutants -------------------------------------------


def test_partition_aware_exercise_is_valid(tmp_path):
    report = run(make_partition_exercise(tmp_path))

    assert report.valid, format_report(report)
    assert report.result("5").status is Status.OK


def test_reference_wrong_at_4_partitions_breaks_rule_5(tmp_path):
    """Correct at the default 2 partitions, so rule 1 passes; rule 5 tries 1 and 4 too."""
    report = run(make_partition_exercise(tmp_path, reference=FIRST_TWO))

    assert report.result("1").status is Status.OK
    assert report.result("5").status is Status.FAIL
    assert "4 partition" in report.result("5").detail


def test_mutant_wrong_only_at_4_needs_mutant_partitions(tmp_path):
    """By default mutants run at 2 partitions only, so a bug that shows at 4 survives;
    listing [2, 4] in "mutant_partitions" kills it."""
    mutants = {"m01_first_only": FIRST_ONLY, "m02_first_two": FIRST_TWO}

    default = run(make_partition_exercise(tmp_path / "a", mutants=mutants)).result("3")
    stricter = run(make_partition_exercise(
        tmp_path / "b", mutants=mutants, metadata={"mutant_partitions": [2, 4]},
    )).result("3")

    assert default.status is Status.FAIL
    assert "m02_first_two" in default.detail
    assert stricter.status is Status.OK
    assert "2/2 killed (partitions 2, 4)" in stricter.detail


def test_metadata_timeout_applies_to_every_run(tmp_path):
    """Hadoop exercises set `timeout_s` in metadata; here it is too short for pytest even to
    start, so rule 1 times out."""
    report = run(make_exercise(tmp_path, metadata={"timeout_s": 0.05}))

    assert report.result("1").status is Status.FAIL
    assert "timeout" in report.result("1").detail


# A tiny inline MRJob exercise for the combiner check.
MR_TESTS = (
    "from tutor.testkit import assert_output, run_mrjob, write_inputs\n"
    "from student import Count\n\n"
    "def test_count(tmp_path):\n"
    "    inp = write_inputs(tmp_path / 'in', ['a b a', 'b a'])\n"
    "    out = run_mrjob(Count, inp, tmp_path / 'out')\n"
    "    assert_output(out, ['\"a\"\\t3', '\"b\"\\t2'])\n"
)
MR_JOB = (
    "from mrjob.job import MRJob\n\n"
    "class Count(MRJob):\n"
    "    def mapper(self, _, line):\n"
    "        for w in line.split():\n"
    "            yield w, 1\n\n"
    "    def combiner(self, w, counts):\n"
    "        yield w, {COMBINE}\n\n"
    "    def reducer(self, w, values):\n"
    "        yield w, {REDUCE}\n\n"
    "if __name__ == '__main__':\n"
    "    Count.run()\n"
)


@pytest.mark.parametrize("combine, reduce, status", [
    ("sum(counts)", "sum(values)", Status.OK),
    # the reducer needs the combiner's [sum, 'partial'] lists: fine on, crashes off
    ("[sum(counts), 'partial']", "sum(v[0] for v in values)", Status.FAIL),
])
def test_combiner_check(tmp_path, combine, reduce, status):
    """Rule 5 with combiner_check reruns the reference with the combiner removed."""
    reference = MR_JOB.format(COMBINE=combine, REDUCE=reduce)
    report = run(make_exercise(
        tmp_path, tests=MR_TESTS, reference=reference, metadata={"combiner_check": True},
    ))

    assert report.result("1").status is Status.OK
    assert report.result("5").status is status
    if status is Status.FAIL:
        assert "with the combiner off" in report.result("5").detail
    else:
        assert report.result("5").detail == "reference passes with the combiner off"


# --- static API constraints -------------------------------------------------------------


def test_forbidden_call_is_found_with_its_line():
    source = "def f(rdd):\n    return rdd.groupByKey()\n"
    assert find_violations(source, {"forbidden_calls": ["groupByKey"]}) == [
        "line 2: uses groupByKey"
    ]


def test_forbidden_name_is_found_even_without_calling_it():
    """Assigning the method and calling it later is still caught."""
    source = "def f(rdd):\n    g = rdd.groupByKey\n    return g()\n"
    assert find_violations(source, {"forbidden_calls": ["groupByKey"]}) == [
        "line 2: uses groupByKey"
    ]


def test_allowed_code_has_no_violations():
    source = "def f(rdd):\n    return rdd.reduceByKey(max)\n"
    assert find_violations(source, {"forbidden_calls": ["groupByKey", "collect"]}) == []


@pytest.mark.parametrize("line", [
    "import pyspark.sql",
    "from pyspark.sql import SparkSession",
    "from pyspark import sql",
    "import pyspark.sql.functions as F",
])
def test_forbidden_import_in_every_form(line):
    assert find_violations(line + "\n", {"forbidden_imports": ["pyspark.sql"]})


def test_similar_module_name_is_not_forbidden():
    """pyspark.sql bans its submodules, not a module that merely starts with the same text."""
    source = "from pyspark import SparkContext\nimport pyspark.sqlx\n"
    assert find_violations(source, {"forbidden_imports": ["pyspark.sql"]}) == []


def test_required_definitions():
    source = "class J:\n    def mapper_init(self):\n        pass\n"
    constraints = {"required_definitions": ["mapper_init", "mapper_final"]}
    assert find_violations(source, constraints) == ["must define mapper_final"]


COMBINE = {"required_any": [["combiner"], ["mapper_init", "mapper_final"]]}


@pytest.mark.parametrize("source", [
    "class J:\n    def combiner(self, k, v):\n        pass\n",
    "class J:\n    def mapper_init(self):\n        pass\n"
    "    def mapper_final(self):\n        pass\n",
    # multi-step jobs name the combiner method freely and pass it as a keyword
    "class J:\n    def steps(self):\n        return [MRStep(combiner=self.add_up)]\n",
])
def test_required_any_is_met_by_any_one_group(source):
    """A combiner or in-mapper combining: Project 1 accepts either."""
    assert find_violations(source, COMBINE) == []


def test_required_any_needs_a_whole_group():
    """mapper_init alone is half of in-mapper combining, and there is no combiner."""
    source = "class J:\n    def mapper_init(self):\n        pass\n"
    assert find_violations(source, COMBINE) == [
        "must define one of: combiner | mapper_init + mapper_final"
    ]


def test_required_any_must_be_a_list_of_groups():
    """A flat list would silently mean "every single name is its own group"."""
    assert find_violations("x = 1\n", {"required_any": ["combiner", "mapper_init"]}) == [
        'required_any must be a list of non-empty name lists, e.g. [["combiner"]]'
    ]


def test_count_uses_counts_both_spellings_but_not_the_import():
    source = (
        "import mrjob.step\nfrom mrjob.step import MRStep\n\n"
        "def steps():\n    return [MRStep(), mrjob.step.MRStep()]\n"
    )
    assert count_uses(source, ["MRStep", "combiner"]) == {"MRStep": 2, "combiner": 0}


def test_count_uses_is_reported_never_enforced(tmp_path):
    """The api line shows the counts; any count passes."""
    reference = (
        "def MRStep():\n    return 0\n\n"
        "def add(a, b):\n    return a + b + MRStep() + MRStep()\n"
    )
    report = run(make_exercise(
        tmp_path, reference=reference, metadata={"constraints": {"count_uses": ["MRStep"]}},
    ))

    assert report.result("api").status is Status.OK
    assert report.result("api").detail.endswith("; uses: MRStep x2")


def test_unknown_constraint_key_is_reported():
    """A typo would otherwise silently check nothing."""
    assert find_violations("x = 1\n", {"forbiden_calls": ["collect"]}) == [
        "unknown constraint key(s): forbiden_calls"
    ]


def test_reference_that_breaks_its_constraints_fails_the_api_check(tmp_path):
    """Checked even when rule 1 fails, because it does not depend on the tests. This
    reference is both wrong (fails rule 1) and uses the forbidden `count`."""
    reference = "def add(a, b):\n    return [a, b].count(a)\n"
    report = run(make_exercise(
        tmp_path, reference=reference, metadata={"constraints": {"forbidden_calls": ["count"]}},
    ))

    assert report.result("1").status is Status.FAIL
    assert report.result("api").status is Status.FAIL
    assert "line 2: uses count" in report.result("api").detail


# --- command line -----------------------------------------------------------------------


def test_cli_exit_codes(tmp_path, capsys):
    """0 for a valid exercise, 1 for an invalid one, 2 for wrong usage."""
    good = make_exercise(tmp_path / "good")
    bad = make_exercise(tmp_path / "bad", mutants={})

    assert main([str(p) for p in good]) == 0
    assert "VALID" in capsys.readouterr().out
    assert main([str(p) for p in bad]) == 1
    assert main([]) == 2


def test_cli_saves_the_report_as_json(tmp_path):
    """The same report the CLI prints, in <solution_dir>/validation.json; rerunning
    overwrites it."""
    exercise, solution = make_exercise(tmp_path, mutants={})
    main([str(exercise), str(solution)])
    data = json.loads((solution / "validation.json").read_text())

    assert data["exercise_id"] == "demo"
    assert data["valid"] is False
    assert [r["rule"] for r in data["results"]] == ["1", "2", "3", "4", "5", "api"]
    assert data["results"][2]["status"] == "FAIL"  # zero mutants fails rule 3
    assert "validated_at" in data and "tool_commit" in data

    (solution / "mutants" / "m01_sub.py").write_text(MUTANT_SUB)
    main([str(exercise), str(solution)])
    assert json.loads((solution / "validation.json").read_text())["valid"] is True


# --- real examples, with Spark ----------------------------------------------------------

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.spark
@pytest.mark.parametrize(
    "name",
    ["station-average-rdd", "station-max-file", "station-monthly-df", "station-condition-share"],
)
def test_examples_are_valid(name):
    """End to end on the real examples. The MRJob one runs on Hadoop (about 4 min)."""
    load_env()
    if name == "station-condition-share" and not os.environ.get("HADOOP_HOME"):
        pytest.skip("needs HADOOP_HOME")
    exercise = load_exercise(EXAMPLES / name / "exercise", EXAMPLES / name / "solution")

    report = validate(exercise)

    assert report.valid, format_report(report)
