"""Plain-Python specification of the program, for differential testing (validation rule 4),
written from the question alone: what it prints for an input, and its exit status.
"""


def expected(text: str) -> tuple[int, str, str]:
    """(exit status, standard output, standard error) for the input `text`."""
    words = text.split()
    try:
        n = int(words[0])
        readings = [int(word) for word in words[1:n + 1]] if n > 0 else []
    except (IndexError, ValueError):
        return 1, "", "invalid input\n"
    if not 1 <= n <= 1000 or len(readings) < n:
        return 1, "", "invalid input\n"
    average = sum(readings) / n
    above = "".join(f" {r}" for r in readings if r > average)
    return 0, f"average {average:.2f}\nabove{above}\n", ""
