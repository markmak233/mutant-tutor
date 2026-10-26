"""Validation rule 4: the Spark program (reference copied in as student.py) writes exactly
the lines oracle.py expects, for random input files mixing valid and malformed lines.
Validator-only; never shown to the student.

Every example starts a SparkContext (the program creates its own), so examples are few
but each one is large, and shrinking is off: each shrink step would start Spark again and a
failure would take minutes to report. The reported input is the unshrunk one.
"""

import tempfile
from pathlib import Path

from hypothesis import Phase, given, settings
from hypothesis import strategies as st

from tutor.testkit import assert_output, run_program, write_inputs

from oracle import expected_output
from student import StationMax

HEADER = "station,date,temperature"

temperature = st.one_of(
    st.integers(-40, 50).map(str),
    st.floats(-40, 50, allow_nan=False, allow_infinity=False).map(lambda t: f"{t:.3f}"),
)
valid_line = st.builds(
    lambda station, t, pad: f"{pad}{station}{pad},2024-01-01{pad},{pad}{t}{pad}",
    st.sampled_from(["s1", "s2", "s3", "north"]),
    temperature,
    st.sampled_from(["", " ", "  "]),
)
bad_line = st.sampled_from([
    "", "junk", HEADER, "s1,2024-01-01", ",2024-01-01,3.0", "s1,2024-01-01,warm",
    "a,b,c,d", "s2,2024-01-01,",
])
lines_strategy = st.lists(st.one_of(valid_line, valid_line, bad_line), max_size=40)


@settings(
    max_examples=8, deadline=None, derandomize=True,
    phases=[Phase.explicit, Phase.reuse, Phase.generate],  # no Phase.shrink, see above
)
@given(lines=lines_strategy, files=st.sampled_from([1, 2, 4]))
def test_matches_oracle(lines, files):
    with tempfile.TemporaryDirectory() as tmp:
        input_dir = write_inputs(Path(tmp) / "input", lines, files=files, header=HEADER)
        got = run_program(StationMax, input_dir, Path(tmp) / "output")
    assert_output(got, expected_output(lines))
