"""Validation rule 4: the reference (copied in as student.py) agrees with oracle.py on random
inputs. Validator-only; never shown to the student.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from oracle import longest_rise as oracle_longest_rise
from student import longest_rise

# Few distinct values, so that equal readings and stations taking turns come up often.
readings = st.lists(
    st.tuples(st.sampled_from(["s1", "s2", "s3"]), st.sampled_from([0.0, 1.0, 2.0, 3.0])),
    max_size=30,
)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(data=readings)
def test_matches_oracle(data):
    assert longest_rise(data) == oracle_longest_rise(data)
