"""Tests for longest_rise."""

from student import longest_rise


def test_example_from_question():
    readings = [("s1", 1.0), ("s2", 9.0), ("s1", 2.0), ("s2", 8.0), ("s1", 3.0), ("s1", 3.0)]
    assert longest_rise(readings) == {"s1": 3, "s2": 1}


def test_empty_input():
    assert longest_rise([]) == {}


def test_single_reading():
    assert longest_rise([("solo", 12.5)]) == {"solo": 1}


def test_an_equal_reading_ends_a_run():
    # Current run: 1, 1, 2.
    assert longest_rise([("s", 1.0), ("s", 1.0), ("s", 2.0)]) == {"s": 2}


def test_a_fall_starts_the_run_again():
    # Current run: 1, 2, 1, 2.
    assert longest_rise([("s", 1.0), ("s", 2.0), ("s", 0.0), ("s", 1.0)]) == {"s": 2}


def test_the_longest_run_need_not_be_the_last():
    # Current run: 1, 2, 3, 1.
    assert longest_rise([("s", 1.0), ("s", 2.0), ("s", 3.0), ("s", 0.0)]) == {"s": 3}


def test_stations_do_not_mix():
    # a: 1.0, 2.0, 3.0 rises throughout, and so does b: 0.0, 1.0, though every reading of b
    # is lower than the reading of a before it.
    readings = [("a", 1.0), ("b", 0.0), ("a", 2.0), ("b", 1.0), ("a", 3.0)]
    assert longest_rise(readings) == {"a": 3, "b": 2}
