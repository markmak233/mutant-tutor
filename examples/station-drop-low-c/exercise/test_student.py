"""Tests for drop_below. student.c and the given driver.c are compiled once, below; each test
runs the driver on one array and reads the count and the kept readings it prints.
"""

from tutor.testkit_c import build

program = build("student.c", "driver.c")


def drop(limit: int, readings: list[int]) -> tuple[int, list[int]]:
    """(the count drop_below returned, the readings at the front of the array after it)."""
    text = f"{limit} {len(readings)} {' '.join(map(str, readings))}\n"
    count, kept = program.run(stdin=text).lines()
    return int(count), [int(word) for word in kept.split()]


def test_example_from_question():
    assert drop(4, [4, 1, 7, 3, 9]) == (3, [4, 7, 9])


def test_a_reading_equal_to_the_limit_is_kept():
    assert drop(5, [5, 4, 5, 6]) == (3, [5, 5, 6])


def test_nothing_or_everything_dropped():
    assert drop(-10, [3, -2, 0]) == (3, [3, -2, 0])
    assert drop(100, [3, -2, 0]) == (0, [])


def test_no_readings():
    assert drop(0, []) == (0, [])


def test_many_readings_keep_their_order():
    readings = [(i * 37) % 101 - 50 for i in range(500)]
    kept = [r for r in readings if r >= 0]
    assert drop(0, readings) == (len(kept), kept)
