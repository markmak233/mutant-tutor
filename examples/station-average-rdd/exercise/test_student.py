"""Tests for average_by_station.

TUTOR_PARTITIONS sets how many partitions the input is split into, so the validator can
rerun the same tests with 1, 2 and 4 partitions.
"""

import os
import sys

import pytest
from pyspark import SparkConf, SparkContext

from student import average_by_station

PARTITIONS = int(os.environ.get("TUTOR_PARTITIONS", "2"))


@pytest.fixture(scope="module")
def sc():
    # Spark workers must run the same Python as this test process, not whatever
    # `python3` is first on PATH.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    conf = (
        SparkConf()
        .setMaster("local[2]")
        .setAppName("station-average-tests")
        .set("spark.ui.enabled", "false")
    )
    context = SparkContext(conf=conf)
    context.setLogLevel("ERROR")
    yield context
    context.stop()


def run(sc, readings):
    """Split the readings into PARTITIONS partitions and collect the answer as a list."""
    return average_by_station(sc.parallelize(readings, PARTITIONS)).collect()


def test_example_from_question(sc):
    readings = [("s1", 10.0), ("s2", 4.0), ("s1", 20.0), ("s1", 30.0)]
    assert dict(run(sc, readings)) == pytest.approx({"s1": 20.0, "s2": 4.0})


def test_fractional_average(sc):
    assert dict(run(sc, [("s2", 4.0), ("s2", 5.0)])) == pytest.approx({"s2": 4.5})


def test_single_reading(sc):
    assert dict(run(sc, [("solo", 12.5)])) == pytest.approx({"solo": 12.5})


def test_negative_temperatures(sc):
    readings = [("polar", -10.0), ("polar", -20.0), ("polar", 0.0)]
    assert dict(run(sc, readings)) == pytest.approx({"polar": -10.0})


def test_empty_input(sc):
    assert run(sc, []) == []


def test_exactly_one_pair_per_station(sc):
    """dict() would hide duplicate keys, so count the raw pairs."""
    readings = [("a", 1.0), ("b", 2.0), ("a", 3.0), ("b", 4.0), ("a", 5.0)]
    result = run(sc, readings)
    assert sorted(station for station, _ in result) == ["a", "b"]


def test_many_readings(sc):
    readings = [(f"s{i % 5}", float(i % 17) - 3.5) for i in range(200)]
    expected = {}
    for station in {s for s, _ in readings}:
        temps = [t for s, t in readings if s == station]
        expected[station] = sum(temps) / len(temps)
    assert dict(run(sc, readings)) == pytest.approx(expected)
