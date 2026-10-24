"""Tests for StationMax.

Each test writes input files into a temp dir, runs the program, reads every part-* file of
the output and compares the lines exactly but in any order. TUTOR_PARTITIONS sets how many
input files the data is split into, so the validator can rerun with 1, 2 and 4 files.
"""

from tutor.testkit import assert_output, run_program, write_inputs

from student import StationMax

HEADER = "station,date,temperature"


def run(tmp_path, lines):
    """Write `lines` as input files (each starting with the header), run the program."""
    input_dir = write_inputs(tmp_path / "input", lines, header=HEADER, suffix=".csv")
    return run_program(StationMax, input_dir, tmp_path / "output")


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
    assert_output(run(tmp_path, lines), ["s1\t20.0", "s2\t-1.5"])


def test_bad_lines_in_every_file_and_one_decimal_place(tmp_path):
    """Malformed lines are spread so every input file gets some; 3.14159 must print as 3.1."""
    lines = [
        "a,2024-02-01,3.14159", "not a reading", ",2024-02-01,9.0",
        "b,2024-02-01,7", "a,2024-02-02,", "b,2024-02-02,x7",
        "a,2024-02-03,1.0", "too,many,fields,here", "b,2024-02-03,-0.5",
    ]
    assert_output(run(tmp_path, lines), ["a\t3.1", "b\t7.0"])


def test_station_readings_spread_over_all_files(tmp_path):
    """Every station has readings in every input file, so per-file maxima must be merged."""
    lines = [f"s{i % 3},2024-03-{i % 28 + 1:02d},{(i * 7) % 23 - 5}.0" for i in range(60)]
    expected = {}
    for i in range(60):
        station, value = f"s{i % 3}", float((i * 7) % 23 - 5)
        expected[station] = max(expected.get(station, value), value)
    assert_output(run(tmp_path, lines), [f"{s}\t{v:.1f}" for s, v in expected.items()])


def test_only_bad_lines_gives_empty_output(tmp_path):
    assert_output(run(tmp_path, ["", "junk", "s1,2024-01-01,warm"]), [])
