"""Validation rule 4: the reference (copied in as student.c) agrees with oracle.py on random
inputs. Validator-only; never shown to the student.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from tutor.testkit_c import build

from oracle import expected

program = build("student.c")

# Few distinct values, so that readings equal to the average come up.
readings = st.lists(st.integers(-3, 3) | st.integers(-100_000, 100_000), min_size=1,
                    max_size=40)


@settings(max_examples=100, deadline=None, derandomize=True)
@given(values=readings, separator=st.sampled_from([" ", "\n", " \n "]),
       count_off_by=st.sampled_from([0, 0, 0, -1, 1]))
def test_matches_oracle(values, separator, count_off_by):
    """count_off_by: the count given differs from the readings that follow by that much, so
    a count the readings fall short of (invalid) is tried too."""
    text = f"{len(values) + count_off_by}\n" + separator.join(map(str, values)) + "\n"
    run = program.run(stdin=text, check=False)
    assert (run.returncode, run.stdout, run.stderr) == expected(text)
