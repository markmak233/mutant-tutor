import importlib.util
import os
import sys
from pathlib import Path

import pytest

from tutor.config import load_env
from tutor.testkit import (
    Output,
    assert_output,
    partitions,
    run_mrjob,
    run_program,
    write_inputs,
)


def out(*parts: list[str]) -> Output:
    """Output with part files part-00000, part-00001, ... holding the given lines."""
    return Output({f"part-{i:05d}": list(lines) for i, lines in enumerate(parts)})


# --- inputs and partitions ---------------------------------------------------------------


def test_partitions_reads_env_and_defaults_to_two(monkeypatch):
    """The validator sets TUTOR_PARTITIONS for rule 5; a plain pytest run gets 2."""
    monkeypatch.delenv("TUTOR_PARTITIONS", raising=False)
    assert partitions() == 2
    monkeypatch.setenv("TUTOR_PARTITIONS", "4")
    assert partitions() == 4


def test_write_inputs_splits_lines_and_repeats_header(tmp_path, monkeypatch):
    """5 lines over TUTOR_PARTITIONS=2 files -> 3 + 2, each file with its own header."""
    monkeypatch.setenv("TUTOR_PARTITIONS", "2")
    d = write_inputs(tmp_path / "in", ["a", "b", "c", "d", "e"], header="h", suffix=".csv")

    assert [p.name for p in sorted(d.iterdir())] == ["input-0.csv", "input-1.csv"]
    assert (d / "input-0.csv").read_text() == "h\na\nb\nc\n"
    assert (d / "input-1.csv").read_text() == "h\nd\ne\n"


def test_write_inputs_more_files_than_lines(tmp_path):
    """Extra files are written empty rather than skipped, so the file count is what was asked."""
    d = write_inputs(tmp_path / "in", ["only"], files=4)

    assert sorted(p.read_text() for p in d.iterdir()) == ["", "", "", "only\n"]


# --- running a program -------------------------------------------------------------------


class FakeProgram:
    """Stands in for a Spark program: writes one part file and _SUCCESS, records its args."""

    calls: list[tuple] = []

    def run(self, input_path, output_path, *params):
        FakeProgram.calls.append((input_path, output_path, *params))
        out_dir = Path(output_path)
        out_dir.mkdir()
        (out_dir / "part-00000").write_text("x\t1\n")
        (out_dir / ".part-00000.crc").write_text("not data")
        (out_dir / "_SUCCESS").write_text("")


class SavesNothing:
    def run(self, input_path, output_path):
        Path(output_path).mkdir()


def test_run_program_passes_string_args_and_reads_parts(tmp_path):
    """Params arrive as strings, as from sys.argv; only part-* files are output."""
    result = run_program(FakeProgram, tmp_path / "in", tmp_path / "out", 2, 5)

    assert FakeProgram.calls[-1] == (str(tmp_path / "in"), str(tmp_path / "out"), "2", "5")
    assert result.parts == {"part-00000": ["x\t1"]}


def test_run_program_requires_success_marker(tmp_path):
    """No _SUCCESS means nothing was saved, even if the test expected empty output."""
    with pytest.raises(AssertionError, match="no output was saved"):
        run_program(SavesNothing, tmp_path / "in", tmp_path / "out")


# --- order="any" -------------------------------------------------------------------------


def test_any_ignores_order_and_part_files():
    assert_output(out(["b"], ["a", "c"]), ["a", "b", "c"])


def test_any_reports_missing_and_unexpected_lines():
    """The message names both sides, e.g. a tuple printed instead of the asked format."""
    with pytest.raises(AssertionError) as e:
        assert_output(out(["('s1', 20.0)"]), ["s1\t20.0"])
    assert "unexpected: [\"('s1', 20.0)\"]" in str(e.value)
    assert "missing:    ['s1\\t20.0']" in str(e.value)


def test_any_counts_duplicates():
    """A multiset: a line written twice is not the same as once."""
    with pytest.raises(AssertionError, match="got 2 lines, expected 1"):
        assert_output(out(["a", "a"]), ["a"])


# --- float_tol and quotes ----------------------------------------------------------------


def test_exact_by_default_so_number_format_matters():
    """Exercises that ask for one decimal place check it: 20.00 is not 20.0."""
    with pytest.raises(AssertionError):
        assert_output(out(["s1\t20.00"]), ["s1\t20.0"])


def test_float_tol_accepts_tiny_differences_and_equal_representations():
    """Both projects accept tiny float differences, and 3.0 == 3.000."""
    assert_output(
        out(["travel,food,2,2.666666666666667", "gaming,esports,3,3.000"]),
        ["gaming,esports,3,3.0", "travel,food,2,2.6666666666666665"],
        float_tol=1e-9,
    )


def test_float_tol_still_rejects_wrong_values_and_text():
    with pytest.raises(AssertionError):
        assert_output(out(["a,1.5"]), ["a,1.6"], float_tol=1e-9)
    with pytest.raises(AssertionError):
        assert_output(out(["b,1.5"]), ["a,1.5"], float_tol=1e-9)


def test_optional_quotes_for_mrjob_json_keys():
    """MRJob writes keys as JSON; with optional_quotes both forms are accepted."""
    assert_output(out(['"zounds,a"\t0.5']), ["zounds,a\t0.5"], optional_quotes=True)
    with pytest.raises(AssertionError):
        assert_output(out(['"zounds,a"\t0.5']), ["zounds,a\t0.5"])


# --- order="per_file" --------------------------------------------------------------------


def test_per_file_accepts_each_reducer_sorted_within_itself():
    """Secondary sort: keys split over two reducers, each file ordered on its own."""
    expected = ["1\t3", "2\t1 3", "3\t1", "4\t2"]
    assert_output(out(["1\t3", "3\t1"], ["2\t1 3", "4\t2"]), expected, order="per_file")


def test_per_file_rejects_a_file_out_of_order():
    with pytest.raises(AssertionError, match="part-00001 line 2: '2' comes after '4'"):
        assert_output(out(["1"], ["4", "2"]), ["1", "2", "4"], order="per_file")


def test_per_file_still_checks_the_lines_themselves():
    with pytest.raises(AssertionError, match="missing"):
        assert_output(out(["1"], ["2"]), ["1", "2", "3"], order="per_file")


# --- order="total" -----------------------------------------------------------------------


def test_total_accepts_one_ordered_file_and_ignores_empty_parts():
    assert_output(out([], ["a", "b"], []), ["a", "b"], order="total")


def test_total_rejects_several_data_files():
    """Fully ordered output must be in one file (e.g. sort, then coalesce(1))."""
    with pytest.raises(AssertionError, match="found 2 non-empty ones"):
        assert_output(out(["a"], ["b"]), ["a", "b"], order="total")


def test_total_reports_first_wrong_line():
    with pytest.raises(AssertionError, match="line 1: got 'b', expected 'a'"):
        assert_output(out(["b", "a"]), ["a", "b"], order="total")


def test_total_reports_missing_tail():
    with pytest.raises(AssertionError, match="first missing line: 'c'"):
        assert_output(out(["a", "b"]), ["a", "b", "c"], order="total")


def test_total_empty_output():
    """No eligible results: an empty data file (or none) matches no expected lines."""
    assert_output(out([]), [], order="total")


def test_unknown_order_is_an_error():
    with pytest.raises(ValueError):
        assert_output(out([]), [], order="sorted")


# --- run_mrjob ---------------------------------------------------------------------------
# Jobs are written to files and imported, because the hadoop runner ships the job's own
# script to the task processes.

load_env()  # HADOOP_HOME / JAVA_HOME from the repo's .env, for the hadoop tests


def needs_hadoop(test):
    """Slow (each job starts Hadoop, about 7 s), so `spark`-marked; skipped without Hadoop."""
    test = pytest.mark.skipif(not os.environ.get("HADOOP_HOME"), reason="no HADOOP_HOME")(test)
    return pytest.mark.spark(test)

WORD_COUNT = '''
from mrjob.job import MRJob


class WordCount(MRJob):
    JOBCONF = {"mapreduce.job.reduces": 3}

    def mapper(self, _, line):
        for word in line.split():
            yield word, 1

    def reducer(self, word, counts):
        yield word, sum(counts)


if __name__ == "__main__":
    WordCount.run()
'''

TOP_K = '''
from mrjob.compat import jobconf_from_env
from mrjob.job import MRJob
from mrjob.step import MRStep


class TopK(MRJob):
    def steps(self):
        return [
            MRStep(mapper=self.words, combiner=self.add_up, reducer=self.add_up),
            # the global top-k needs one reducer, whatever the command line says
            MRStep(mapper=self.one_key, reducer=self.top_k,
                   jobconf={"mapreduce.job.reduces": 1}),
        ]

    def words(self, _, line):
        for word in line.split():
            yield word, 1

    def add_up(self, word, counts):
        yield word, sum(counts)

    def one_key(self, word, count):
        yield None, (count, word)

    def top_k(self, _, pairs):
        k = int(jobconf_from_env("myjob.settings.k"))
        for count, word in sorted(pairs, key=lambda p: (-p[0], p[1]))[:k]:
            yield word, count


if __name__ == "__main__":
    TopK.run()
'''

BROKEN_MAPPER = '''
from mrjob.job import MRJob


class Broken(MRJob):
    def mapper(self, _, line):
        raise NotImplementedError("write the mapper")


if __name__ == "__main__":
    Broken.run()
'''

# A misspelt partitioner class: Hadoop rejects the job, inline ignores partitioners.
BAD_PARTITIONER = WORD_COUNT.replace(
    'JOBCONF = {"mapreduce.job.reduces": 3}',
    'PARTITIONER = "org.apache.hadoop.mapred.lib.KeyFieldBasedPartitionr"',
)

TEXT = ["b a c a", "a b", "c c a b", "d a b c", "e d a", "b e"]


def load_job(tmp_path: Path, source: str, name: str):
    """Write `source` to <tmp>/<module>.py and return its job class `name`."""
    module_name = f"job_{name.lower()}_{tmp_path.name}"
    path = tmp_path / f"{module_name}.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # mrjob finds the job's script file through it
    spec.loader.exec_module(module)
    return getattr(module, name)


def test_mrjob_inline_two_steps_with_jobconf_parameter(tmp_path):
    """Several input files, two MRSteps, k passed as --jobconf myjob.settings.k."""
    job = load_job(tmp_path, TOP_K, "TopK")
    inp = write_inputs(tmp_path / "in", TEXT, files=3)

    result = run_mrjob(job, inp, tmp_path / "out", jobconf={"myjob.settings.k": 2})

    assert_output(result, ['"a"\t6', '"b"\t5'], order="total")


def test_mrjob_without_main_block_is_explained(tmp_path):
    """Hadoop starts the job as a script; without the entry point it silently outputs
    nothing, so this is caught before running."""
    source = TOP_K.replace('if __name__ == "__main__":\n    TopK.run()', "")
    job = load_job(tmp_path, source, "TopK")
    with pytest.raises(AssertionError, match=r"TopK\.run\(\)"):
        run_mrjob(job, write_inputs(tmp_path / "in", TEXT), tmp_path / "out")


def test_mrjob_unknown_runner_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        run_mrjob(object, tmp_path, tmp_path / "out", runner="local")


@needs_hadoop
def test_hadoop_command_line_reducers_beat_class_jobconf(tmp_path, monkeypatch):
    """JOBCONF asks for 3 reducers; run_mrjob passes TUTOR_PARTITIONS=2 on the command
    line, which wins, so rule 5 can vary the count even in jobs that fix it."""
    monkeypatch.setenv("TUTOR_PARTITIONS", "2")
    job = load_job(tmp_path, WORD_COUNT, "WordCount")
    inp = write_inputs(tmp_path / "in", TEXT)

    result = run_mrjob(job, inp, tmp_path / "out", runner="hadoop")

    assert len(result.parts) == 2
    assert_output(result, ['"a"\t6', '"b"\t5', '"c"\t4', '"d"\t2', '"e"\t2'])


@needs_hadoop
def test_hadoop_step_jobconf_beats_command_line(tmp_path, monkeypatch):
    """Four reducers from TUTOR_PARTITIONS, but the top-k step pins one: one output file."""
    monkeypatch.setenv("TUTOR_PARTITIONS", "4")
    job = load_job(tmp_path, TOP_K, "TopK")
    inp = write_inputs(tmp_path / "in", TEXT)

    result = run_mrjob(job, inp, tmp_path / "out", runner="hadoop",
                       jobconf={"myjob.settings.k": 2})

    assert len(result.parts) == 1
    assert_output(result, ['"a"\t6', '"b"\t5'], order="total")


@needs_hadoop
def test_hadoop_failure_shows_the_python_error(tmp_path):
    """Hadoop only says "subprocess failed"; the inline rerun names the real error."""
    job = load_job(tmp_path, BROKEN_MAPPER, "Broken")
    inp = write_inputs(tmp_path / "in", TEXT)

    with pytest.raises(AssertionError, match="inline runner the same input raises "
                                             "NotImplementedError: write the mapper"):
        run_mrjob(job, inp, tmp_path / "out", runner="hadoop")


@needs_hadoop
def test_hadoop_only_failure_points_at_hadoop_settings(tmp_path):
    """Fails on Hadoop, runs on inline (which ignores partitioners): the message says so."""
    job = load_job(tmp_path, BAD_PARTITIONER, "WordCount")
    inp = write_inputs(tmp_path / "in", TEXT)

    with pytest.raises(AssertionError, match="runs on the inline runner"):
        run_mrjob(job, inp, tmp_path / "out", runner="hadoop")
