"""Validation rule 4: the program (reference copied in as student.py) writes exactly the
lines oracle.py expects, in order, for random input files and a random min_readings.
Validator-only; never shown to the student.

Temperatures come from a few multiples of 2.5: sums are exact in binary floating point, so
Spark and Python compute bit-identical averages, and equal averages are common, so the
month tie-break is exercised (with a wide range of values, ties almost never occurred and a
reversed tie-break passed this test). Every example starts the program's own SparkSession, so
examples are few and shrinking is off (see station-max-file).
"""

import tempfile
from pathlib import Path

from hypothesis import Phase, given, settings
from hypothesis import strategies as st

from tutor.testkit import assert_output, run_program, write_inputs

from oracle import expected_output
from student import StationMonthly

HEADER = "station,date,temperature"

valid_line = st.builds(
    lambda station, date, t, pad: f"{pad}{station}{pad},{date}{pad},{pad}{t}",
    st.sampled_from(["s1", "s2", "s10"]),
    st.sampled_from(["2023-12-31", "2024-01-01", "2024-01-15", "2024-02-01", "2025-01-01"]),
    st.sampled_from(["0", "2.5", "5", "7.5", "10", "-5.0"]),
    st.sampled_from(["", " "]),
)
bad_line = st.sampled_from([
    "", ",2024-01-01,5", "s1,,5", "s1,2024-01-01,", "s1,2024-01-01,n/a", "s1,2024-01-01",
    " , 2024-01-01 , 3",
])
lines_strategy = st.lists(st.one_of(valid_line, valid_line, bad_line), max_size=40)


@settings(
    max_examples=8, deadline=None, derandomize=True,
    phases=[Phase.explicit, Phase.reuse, Phase.generate],  # no Phase.shrink, see above
)
@given(lines=lines_strategy, min_readings=st.integers(1, 3), files=st.sampled_from([1, 2, 4]))
def test_matches_oracle(lines, min_readings, files):
    with tempfile.TemporaryDirectory() as tmp:
        input_dir = write_inputs(Path(tmp) / "input", lines, files=files, header=HEADER)
        got = run_program(StationMonthly, input_dir, Path(tmp) / "output", min_readings)
    assert_output(got, expected_output(lines, min_readings), order="total", float_tol=1e-9)
