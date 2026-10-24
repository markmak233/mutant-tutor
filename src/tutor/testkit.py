"""Helpers for exercise tests: input files, running a file-level program or MRJob job,
comparing output.

Each exercise's test_student.py and test_oracle.py import this, so they stay short and the
output rules live in one place (docs/design.md, "Interface extensions"). The comparison mode
and any parameters are written in the test itself, not in metadata.json: tests run where the
solution folder is not available.

Importing this module sets PYSPARK_PYTHON to the running interpreter (if not already set), so
Spark workers use the same Python as the test.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tutor.config import load_env

PARTITIONS_ENV = "TUTOR_PARTITIONS"
DEFAULT_PARTITIONS = 2
ORDERS = ("any", "per_file", "total")

os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

# A number inside an output line: 3, -1.5, .5, 2.6666666666666665, 1e-05.
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SHOWN = 10  # lines listed per kind in a mismatch message


def partitions() -> int:
    """How many partitions / input files to use. The validator sets TUTOR_PARTITIONS to
    1, 2 or 4 (rule 5); a plain `pytest` run uses 2."""
    return int(os.environ.get(PARTITIONS_ENV, DEFAULT_PARTITIONS))


def write_inputs(
    input_dir: Path | str,
    lines: Sequence[str],
    files: int | None = None,
    header: str | None = None,
    suffix: str = ".txt",
) -> Path:
    """Split `lines` into `files` input files (default: partitions()), each starting with
    `header` if given, like an HDFS input directory. Returns the directory."""
    input_dir = Path(input_dir)
    input_dir.mkdir(parents=True)
    files = files or partitions()
    size = -(-len(lines) // files) or 1  # ceiling division, at least 1
    for i in range(files):
        chunk = list(lines[i * size:(i + 1) * size])
        if header is not None:
            chunk.insert(0, header)
        (input_dir / f"input-{i}{suffix}").write_text("".join(f"{line}\n" for line in chunk))
    return input_dir


@dataclass(frozen=True)
class Output:
    parts: dict[str, list[str]] = field(default_factory=dict)  # part file name -> its lines

    @property
    def lines(self) -> list[str]:
        return [line for lines in self.parts.values() for line in lines]


def read_output(output_dir: Path | str) -> Output:
    """Every part-* file in name order (part-00000, part-00001, ...). _SUCCESS and the
    hidden .crc files are not data."""
    paths = sorted(Path(output_dir).glob("part-*"))
    return Output({p.name: p.read_text().splitlines() for p in paths})


def run_program(program, input_dir: Path | str, output_dir: Path | str, *params) -> Output:
    """Run a file-level program the way the course runs it,
    `program().run(inputPath, outputPath, *params)`, with every argument a string as from
    sys.argv. Returns what it wrote."""
    try:
        program().run(str(input_dir), str(output_dir), *(str(p) for p in params))
    finally:
        # A crash before sc.stop() would make every later test fail with "Cannot run
        # multiple SparkContexts" instead of its own error.
        _stop_leftover_spark()
    # Spark writes _SUCCESS when saving finished; without this check a program that saves
    # nothing would pass a test that expects empty output.
    assert (Path(output_dir) / "_SUCCESS").exists(), "no output was saved to outputPath"
    return read_output(output_dir)


def run_mrjob(
    job_cls,
    input_dir: Path | str,
    output_dir: Path | str,
    *,
    runner: str = "inline",
    jobconf: dict | None = None,
) -> Output:
    """Run an MRJob job on every file in `input_dir`, the way the course runs it:
    `python job.py -r <runner> <input> -o <output> --jobconf myjob.settings.k=3 ...`.

    runner="hadoop" uses the local standalone Hadoop (HADOOP_HOME): partitioner, comparator
    and reducer count are honoured there, unlike inline. It sets mapreduce.job.reduces to
    partitions(), so rule 5 checks 1, 2 and 4 reducers; a reducer count written in an
    MRStep's own jobconf still wins for that step (verified 2026-10-25).
    """
    if runner not in ("inline", "hadoop"):
        raise ValueError(f"runner must be 'inline' or 'hadoop', not {runner!r}")
    _assert_entry_point(job_cls)
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    conf = dict(jobconf or {})
    if runner == "inline":
        _run_job(job_cls, [str(input_dir), "-r", "inline", "-o", str(output_dir)], conf)
        return read_output(output_dir)

    hadoop = _hadoop_bin()
    conf.setdefault("mapreduce.job.reduces", partitions())
    # Fail on the first error: by default every failed task is retried 4 times.
    conf.update({"mapreduce.map.maxattempts": 1, "mapreduce.reduce.maxattempts": 1})
    args = [
        input_dir.as_uri(), "-r", "hadoop", "-o", output_dir.as_uri(),
        "--hadoop-bin", str(hadoop), "--python-bin", sys.executable,
        "--hadoop-tmp-dir", (output_dir.parent / f"{output_dir.name}-hadoop-tmp").as_uri(),
    ]
    from mrjob.step import StepFailedException  # only MRJob exercises need mrjob

    failure = None
    try:
        _run_job(job_cls, args, conf)
    except StepFailedException as hadoop_error:
        failure = hadoop_error
    if failure is not None:
        # Outside the except block, so the report does not start with the Hadoop command
        # line (Python would chain it to whatever the rerun raises).
        _explain_hadoop_failure(job_cls, input_dir, output_dir, conf, failure)
    return read_output(output_dir)


def assert_output(
    actual: Output,
    expected: Iterable[str],
    order: str = "any",
    float_tol: float = 0.0,
    optional_quotes: bool = False,
) -> None:
    """Compare a program's output with the expected lines.

    order:
        "any"       all lines of all part files, in any order (a multiset)
        "per_file"  as "any", and each part file keeps the order of `expected`: one reducer's
                    output is sorted only within itself (secondary sort)
        "total"     exactly one non-empty part file, in the order of `expected`
    float_tol: 0 compares lines character by character, so `3.0` is not `3.000`. Above 0,
        the text around numbers must match and the numbers may differ by up to float_tol.
    optional_quotes: ignore double quotes, as MRJob writes keys as JSON ("zounds,a").
    """
    if order not in ORDERS:
        raise ValueError(f"order must be one of {ORDERS}, not {order!r}")
    expected = [_strip_quotes(line, optional_quotes) for line in expected]
    parts = {
        name: [_strip_quotes(line, optional_quotes) for line in lines]
        for name, lines in actual.parts.items()
    }

    if order == "total":
        data = {name: lines for name, lines in parts.items() if lines}
        if len(data) > 1:
            raise AssertionError(
                f"expected all output in one part file, found {len(data)} non-empty ones: "
                f"{', '.join(data)}"
            )
        _assert_same_sequence(next(iter(data.values()), []), expected, float_tol)
        return

    _assert_same_multiset([line for lines in parts.values() for line in lines], expected, float_tol)
    if order == "per_file":
        for name, lines in parts.items():
            _assert_in_expected_order(name, lines, expected, float_tol)


def _assert_entry_point(job_cls) -> None:
    """Hadoop (and the course's run command) start the job as a script. Without
    `if __name__ == "__main__": Job.run()` the script defines the class and exits, and
    the job silently writes nothing; inline would still work, which hides the mistake."""
    script = Path(job_cls.mr_job_script() or "")
    pattern = rf"__name__\s*==\s*['\"]__main__['\"]\s*:\s*\n\s*{job_cls.__name__}\.run\(\)"
    if not script.is_file() or not re.search(pattern, script.read_text()):
        raise AssertionError(
            f"{script.name or 'the job file'} needs an entry point at the end:\n"
            f'if __name__ == "__main__":\n    {job_cls.__name__}.run()'
        )


def _run_job(job_cls, args: list[str], conf: dict) -> None:
    for key, value in conf.items():
        args = [*args, "--jobconf", f"{key}={value}"]
    job = job_cls(args)
    with job.make_runner() as runner:
        runner.run()


def _hadoop_bin() -> Path:
    # Exercise tests run from a workspace folder, where the repo's .env is not in the current
    # directory, so load it from the repo; variables exported in the shell still win.
    load_env(Path(__file__).resolve().parents[2] / ".env")
    hadoop = Path(os.environ.get("HADOOP_HOME", "")) / "bin" / "hadoop"
    if not os.environ.get("HADOOP_HOME") or not hadoop.is_file():
        raise RuntimeError("runner='hadoop' needs HADOOP_HOME (a Hadoop 3.3 install) in .env")
    if not os.environ.get("JAVA_HOME"):
        raise RuntimeError("runner='hadoop' needs JAVA_HOME (Java 17) in .env")
    return hadoop


def _explain_hadoop_failure(job_cls, input_dir: Path, output_dir: Path, conf: dict, error):
    """mrjob cannot read the Python traceback from local Hadoop task logs; it only reports
    "subprocess failed with code 1". Rerun on the inline runner (about a second) so the
    learner sees the real error and line."""
    rerun_dir = output_dir.parent / f"{output_dir.name}-inline-rerun"
    try:
        _run_job(job_cls, [str(input_dir), "-r", "inline", "-o", str(rerun_dir)], conf)
    except Exception as inline_error:
        raise AssertionError(
            "the job failed on Hadoop; on the inline runner the same input raises "
            f"{type(inline_error).__name__}{f': {inline_error}' if str(inline_error) else ''} "
            "(traceback above)"
        ) from inline_error
    raise AssertionError(
        "the job failed on Hadoop but runs on the inline runner, which ignores partitioning "
        "and sort settings: check the partitioner, the key field separator and the order in "
        "which keys reach each reducer"
    ) from error


def _stop_leftover_spark() -> None:
    # Look pyspark up instead of importing it, so non-Spark exercises never load it.
    sql = sys.modules.get("pyspark.sql")
    if sql is not None:
        session = sql.SparkSession.getActiveSession()
        if session is not None:
            session.stop()
    pyspark = sys.modules.get("pyspark")
    if pyspark is not None and pyspark.SparkContext._active_spark_context is not None:
        pyspark.SparkContext._active_spark_context.stop()


def _strip_quotes(line: str, optional: bool) -> str:
    return line.replace('"', "") if optional else line


def _split(line: str) -> tuple[str, list[float]]:
    """'s1\\t20.50' -> ('s{}\\t{}', [1.0, 20.5]): the text around numbers, and the numbers."""
    numbers = [float(m) for m in _NUMBER.findall(line)]
    return _NUMBER.sub("{}", line), numbers


def _same(a: str, b: str, float_tol: float) -> bool:
    if float_tol == 0:
        return a == b
    (text_a, nums_a), (text_b, nums_b) = _split(a), _split(b)
    # Same text means the same count of numbers, so strict zip holds.
    return text_a == text_b and all(
        abs(x - y) <= float_tol for x, y in zip(nums_a, nums_b, strict=True)
    )


def _assert_same_multiset(got: list[str], expected: list[str], float_tol: float) -> None:
    unmatched = list(expected)
    extra = []
    for line in got:
        match = next((i for i, e in enumerate(unmatched) if _same(line, e, float_tol)), None)
        if match is None:
            extra.append(line)
        else:
            del unmatched[match]
    if extra or unmatched:
        raise AssertionError(
            f"got {len(got)} lines, expected {len(expected)}\n"
            f"  unexpected: {_show(extra)}\n"
            f"  missing:    {_show(unmatched)}"
        )


def _assert_same_sequence(got: list[str], expected: list[str], float_tol: float) -> None:
    for i, (g, e) in enumerate(zip(got, expected, strict=False), start=1):
        if not _same(g, e, float_tol):
            raise AssertionError(f"line {i}: got {g!r}, expected {e!r}")
    if len(got) != len(expected):
        raise AssertionError(
            f"got {len(got)} lines, expected {len(expected)}; "
            f"first {'extra' if len(got) > len(expected) else 'missing'} line: "
            f"{(got if len(got) > len(expected) else expected)[min(len(got), len(expected))]!r}"
        )


def _assert_in_expected_order(
    name: str, lines: list[str], expected: list[str], float_tol: float
) -> None:
    """`lines` must appear in `expected` in the same relative order (a subsequence)."""
    position = 0
    for i, line in enumerate(lines):
        found = next(
            (j for j in range(position, len(expected)) if _same(line, expected[j], float_tol)),
            None,
        )
        if found is None:
            raise AssertionError(
                f"{name} line {i + 1}: {line!r} comes after {lines[i - 1]!r}, but should come "
                "before it (lines within one output file must be in the expected order)"
            )
        position = found + 1


def _show(lines: list[str]) -> str:
    more = f" ... and {len(lines) - _SHOWN} more" if len(lines) > _SHOWN else ""
    return f"{lines[:_SHOWN]!r}{more}"
