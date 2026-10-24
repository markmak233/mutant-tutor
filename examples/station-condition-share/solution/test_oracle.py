"""Validation rule 4: the job (reference copied in as student.py) writes exactly the lines
oracle.py expects, for random input files, random tau and a random number of reducers.
Validator-only; never shown to the student.

Every example is a Hadoop job (about 7 s), so there are only 4, each with up to 60 lines,
and shrinking is off: each shrink step would run Hadoop again. The reported input is the
unshrunk one.
"""

import tempfile
from pathlib import Path

from hypothesis import Phase, given, settings
from hypothesis import strategies as st

from tutor.testkit import assert_output, run_mrjob, write_inputs

from oracle import expected_output
from student import ConditionShare

valid_line = st.builds(
    lambda station, condition, pad: f"{pad}{station}{pad},2024-01-01,{pad}{condition}{pad}",
    st.sampled_from(["s1", "s2", "s10", "north", "n"]),
    st.sampled_from(["rain", "sun", "fog", "rainy"]),
    st.sampled_from(["", " "]),
)
bad_line = st.sampled_from(["", "junk", "s1,2024-01-01", "s1,2024-01-01,", ",2024-01-01,sun",
                            "a,b,c,d"])
lines_strategy = st.lists(st.one_of(valid_line, valid_line, valid_line, bad_line), max_size=60)


@settings(
    max_examples=4, deadline=None, derandomize=True,
    phases=[Phase.explicit, Phase.reuse, Phase.generate],  # no Phase.shrink, see above
)
@given(
    lines=lines_strategy,
    tau=st.sampled_from([0.0, 0.25, 0.5, 1.0]),
    reducers=st.sampled_from([1, 2, 4]),
)
def test_matches_oracle(lines, tau, reducers):
    with tempfile.TemporaryDirectory() as tmp:
        input_dir = write_inputs(Path(tmp) / "input", lines, files=reducers)
        got = run_mrjob(
            ConditionShare, input_dir, Path(tmp) / "output", runner="hadoop",
            jobconf={"myjob.settings.tau": tau, "mapreduce.job.reduces": reducers},
        )
    assert_output(got, expected_output(lines, tau), order="per_file", float_tol=1e-9,
                  optional_quotes=True)
