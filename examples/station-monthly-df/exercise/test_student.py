"""Tests for StationMonthly.

Each test writes input files (each with the header line) into a temp dir, runs the program
with min_readings as its third argument, and compares the output in order: all rows must be
in one part file, in exactly the required order. Averages may differ by tiny float errors.
TUTOR_PARTITIONS sets how many input files the data is split into (rule 5: 1, 2 and 4).
"""

from tutor.testkit import assert_output, run_program, write_inputs

from student import StationMonthly

HEADER = "station,date,temperature"


def run(tmp_path, lines, min_readings):
    input_dir = write_inputs(tmp_path / "input", lines, header=HEADER, suffix=".csv")
    return run_program(StationMonthly, input_dir, tmp_path / "output", min_readings)


def check(result, expected):
    assert_output(result, expected, order="total", float_tol=1e-9)


def test_example_from_question(tmp_path):
    lines = [
        "s1,2024-01-03,12.5", "s1,2024-01-20,20.0", " s1 , 2024-02-01 , 9.0 ",
        "s1,2024-02-14,n/a", "s1,2024-02-15,17.0", "s2,2024-01-09,4.0", "s2,2024-01-10,",
        ",2024-01-11,30.0", "s2,2024-03-01,-1.0", "s2,2024-03-02,9.0",
    ]
    check(run(tmp_path, lines, 2), ["s1,2024-01,16.25", "s1,2024-02,13.0", "s2,2024-03,4.0"])


def test_order_ties_and_years(tmp_path):
    """Equal averages are ordered by month; January of two years are different months;
    unusable lines are spread so every input file gets some."""
    lines = [
        "b,2023-01-05,10", "b,2023-01-06,20",      # b 2023-01: 15.0
        "b,2024-01-05,15", "b,2024-01-06,n/a",     # b 2024-01: 15.0 (tie: 2023 first)
        "b,2024-02-01,30", "b,2024-02-02,  ",      # b 2024-02: 30.0
        "a,2024-05-01,-2.5", "a,2024-05-02,-7.5",  # a 2024-05: -5.0
        "a,2024-06-01,8", ",2024-06-01,100",       # a 2024-06: 8.0
        " , 2024-06-01, 1", "c,,3", "c,  ,4",       # no station / no date
    ]
    check(run(tmp_path, lines, 1), [
        "a,2024-06,8.0", "a,2024-05,-5.0",
        "b,2024-02,30.0", "b,2023-01,15.0", "b,2024-01,15.0",
    ])


def test_min_readings_counts_only_usable_readings(tmp_path):
    """Three lines in 2024-04 but one is "warm": 2 usable readings, below min_readings=3.
    Many readings per month, spread over all input files."""
    lines = [f"s{i % 3},2024-0{1 + i % 2}-{i % 28 + 1:02d},{i % 7}" for i in range(60)]
    lines += ["s9,2024-04-01,1", "s9,2024-04-02,2", "s9,2024-04-03,warm"]
    sums, counts = {}, {}
    for i in range(60):
        key = (f"s{i % 3}", f"2024-0{1 + i % 2}")
        sums[key] = sums.get(key, 0) + i % 7
        counts[key] = counts.get(key, 0) + 1
    rows = sorted(((s, m, sums[s, m] / counts[s, m]) for s, m in sums),
                  key=lambda r: (r[0], -r[2], r[1]))
    check(run(tmp_path, lines, 3), [f"{s},{m},{a}" for s, m, a in rows])


def test_nothing_qualifies(tmp_path):
    """min_readings above every count: empty output, but the program still writes it."""
    check(run(tmp_path, ["s1,2024-01-01,5", "s1,2024-01-02,6"], 3), [])
