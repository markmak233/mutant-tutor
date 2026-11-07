"""Validation rule 4: the reference (copied in as student.c, with driver.c) agrees with
oracle.py on random inputs. Validator-only; never shown to the student.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from tutor.testkit_c import build

from oracle import drop_below

program = build("student.c", "driver.c")

# Small values next to the limit's, so that readings equal to the limit come up.
values = st.integers(-3, 3) | st.integers(-1000, 1000)


@settings(max_examples=100, deadline=None, derandomize=True)
@given(readings=st.lists(values, max_size=40), limit=values)
def test_matches_oracle(readings, limit):
    text = f"{limit} {len(readings)} {' '.join(map(str, readings))}\n"
    count, kept = program.run(stdin=text).lines()
    assert (int(count), [int(w) for w in kept.split()]) == drop_below(readings, limit)
