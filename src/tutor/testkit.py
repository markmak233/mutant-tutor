"""Helpers for exercise tests: input files, running a file-level program, comparing output.

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
