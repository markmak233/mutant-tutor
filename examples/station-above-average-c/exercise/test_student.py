"""Tests for the readings program. student.c is compiled once, below; each test runs the
program on one input and compares what it prints.
"""

from tutor.testkit_c import build

program = build("student.c")


def run(text: str) -> list[str]:
    """The lines the program prints for the input `text` (it must exit with status 0)."""
    return program.run(stdin=text).lines()


def test_example_from_question():
    assert run("4\n10 20\n30 5\n") == ["average 16.25", "above 20 30"]


def test_no_reading_is_above_when_all_are_equal():
    assert run("3\n5 5 5\n") == ["average 5.00", "above"]


def test_negative_readings_on_one_line():
    assert run("5 -3 -1 -4 -1 -5") == ["average -2.80", "above -1 -1"]


def test_a_single_reading():
    assert run("1\n7\n") == ["average 7.00", "above"]


def test_a_thousand_readings():
    """The most the question allows: every reading must be kept until the average is known."""
    readings = [i % 7 for i in range(1000)]
    average = sum(readings) / 1000
    above = " ".join(str(r) for r in readings if r > average)
    assert run(f"1000\n{' '.join(map(str, readings))}\n") == [f"average {average:.2f}",
                                                              f"above {above}"]


def test_invalid_input_prints_the_message_and_exits_1():
    for text in ["", "0\n", "1001\n" + "1 " * 1001, "-2\n1 2\n", "3\n1 2\n", "x\n"]:
        result = program.run(stdin=text, check=False)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "invalid input\n"), \
            repr(text)
