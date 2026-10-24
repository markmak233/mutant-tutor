"""Validation rule 4: the Spark reference (copied in as student.py) agrees with oracle.py
on random inputs and random partition counts. Validator-only; never shown to the student.
"""

import os
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pyspark import SparkConf, SparkContext

from oracle import average_by_station as oracle_average
from student import average_by_station

readings = st.lists(
    st.tuples(
        st.sampled_from(["s1", "s2", "s3", "s4"]),
        st.floats(-50, 50, allow_nan=False, allow_infinity=False),
    ),
    max_size=40,
)


@pytest.fixture(scope="module")
def sc():
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    conf = (
        SparkConf()
        .setMaster("local[2]")
        .setAppName("station-average-oracle")
        .set("spark.ui.enabled", "false")
    )
    context = SparkContext(conf=conf)
    context.setLogLevel("ERROR")
    yield context
    context.stop()


@settings(max_examples=25, deadline=None, derandomize=True)
@given(data=readings, partitions=st.sampled_from([1, 2, 4]))
def test_matches_oracle(sc, data, partitions):
    pairs = average_by_station(sc.parallelize(data, partitions)).collect()

    assert len(pairs) == len({station for station, _ in pairs})  # one pair per station
    # Sums run in a different order than the oracle's, so floats are compared with tolerance.
    assert dict(pairs) == pytest.approx(oracle_average(data), rel=1e-9, abs=1e-9)
