"""Tests for StationMax.

Each test writes input files into a temp dir, runs the program, reads every part-* file of
the output and compares the lines exactly but in any order. TUTOR_PARTITIONS sets how many
input files the data is split into, so the validator can rerun with 1, 2 and 4 files.
"""

import os
import sys
from collections import Counter

import pytest
from pyspark import SparkContext

from student import StationMax

# Spark workers must run the same Python as this test process.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

PARTITIONS = int(os.environ.get("TUTOR_PARTITIONS", "2"))
HEADER = "station,date,temperature"


@pytest.fixture(autouse=True)
def stop_leftover_spark_context():
    """If the program crashes before sc.stop(), stop its context here. Otherwise every later
    test fails with "Cannot run multiple SparkContexts" instead of its own error."""
    yield
    if SparkContext._active_spark_context is not None:
        SparkContext._active_spark_context.stop()


def run_program(tmp_path, lines):
    """Split `lines` into PARTITIONS files (each starting with the header), run the
    program, and return the output lines as a Counter (a multiset: order ignored)."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    size = -(-len(lines) // PARTITIONS) or 1  # ceiling division, at least 1
    for i in range(PARTITIONS):
        chunk = lines[i * size:(i + 1) * size]
        (input_dir / f"readings-{i}.csv").write_text("\n".join([HEADER, *chunk]) + "\n")

    output_dir = tmp_path / "output"  # must not exist before the run
    StationMax().run(str(input_dir), str(output_dir))
    # Spark writes _SUCCESS when saving finished; without this check a program that
    # writes nothing would pass the empty-output test.
    assert (output_dir / "_SUCCESS").exists(), "no output was saved to outputPath"

    out = []
    for part in sorted(output_dir.glob("part-*")):
        out.extend(part.read_text().splitlines())
    return Counter(out)


def test_example_from_question(tmp_path):
    lines = [
        "s1,2024-01-01,12.5",
        "s2,2024-01-01,-3.0",
        " s1 , 2024-01-02 , 20.0 ",
        "",
        "s2,2024-01-02,oops",
        "s3,2024-01-02",
        "s2,2024-01-03,-1.5",
    ]
    assert run_program(tmp_path, lines) == Counter(["s1\t20.0", "s2\t-1.5"])


def test_bad_lines_in_every_file_and_one_decimal_place(tmp_path):
    """Malformed lines are spread so every input file gets some; 3.14159 must print as 3.1."""
    lines = [
        "a,2024-02-01,3.14159", "not a reading", ",2024-02-01,9.0",
        "b,2024-02-01,7", "a,2024-02-02,", "b,2024-02-02,x7",
        "a,2024-02-03,1.0", "too,many,fields,here", "b,2024-02-03,-0.5",
    ]
    assert run_program(tmp_path, lines) == Counter(["a\t3.1", "b\t7.0"])


def test_station_readings_spread_over_all_files(tmp_path):
    """Every station has readings in every input file, so per-file maxima must be merged."""
    lines = [f"s{i % 3},2024-03-{i % 28 + 1:02d},{(i * 7) % 23 - 5}.0" for i in range(60)]
    expected = {}
    for i in range(60):
        station, value = f"s{i % 3}", float((i * 7) % 23 - 5)
        expected[station] = max(expected.get(station, value), value)
    assert run_program(tmp_path, lines) == Counter(f"{s}\t{v:.1f}" for s, v in expected.items())


def test_only_bad_lines_gives_empty_output(tmp_path):
    assert run_program(tmp_path, ["", "junk", "s1,2024-01-01,warm"]) == Counter()
