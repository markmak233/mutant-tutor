"""Validation rule 4: the Spark program (reference copied in as student.py) writes exactly
the lines oracle.py expects, for random input files mixing valid and malformed lines.
Validator-only; never shown to the student.

Every example starts a SparkContext (the program creates its own), so examples are few
but each one is large, and shrinking is off: each shrink step would start Spark again and a
failure would take minutes to report. The reported input is the unshrunk one.
"""

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from hypothesis import Phase, given, settings
from hypothesis import strategies as st
from pyspark import SparkContext

from oracle import expected_output
from student import StationMax

os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

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
        input_dir = Path(tmp) / "input"
        input_dir.mkdir()
        size = -(-len(lines) // files) or 1
        all_lines = []
        for i in range(files):
            chunk = [HEADER, *lines[i * size:(i + 1) * size]]
            all_lines.extend(chunk)
            (input_dir / f"part-{i}.csv").write_text("\n".join(chunk) + "\n")

        output_dir = Path(tmp) / "output"
        try:
            StationMax().run(str(input_dir), str(output_dir))
        finally:
            # A crash before sc.stop() would make every later example fail on
            # "Cannot run multiple SparkContexts" instead of showing the real error.
            if SparkContext._active_spark_context is not None:
                SparkContext._active_spark_context.stop()

        got = Counter(
            line
            for part in sorted(output_dir.glob("part-*"))
            for line in part.read_text().splitlines()
        )

    assert got == Counter(expected_output(all_lines))
