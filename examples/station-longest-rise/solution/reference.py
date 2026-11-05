"""Reference solution for longest_rise: the worked answer to compare with."""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    # Input records are (station, temperature), in time order, with the stations mixed:
    #   ('s1', 1.0), ('s2', 9.0), ('s1', 2.0), ('s1', 2.0)

    # Step 1: what each station remembers from one of its readings to the next.
    previous: dict[str, float] = {}  # {'s1': 2.0, 's2': 9.0}
    current: dict[str, int] = {}  # {'s1': 1, 's2': 1}
    best: dict[str, int] = {}  # {'s1': 2, 's2': 1}

    for station, temperature in readings:
        # Step 2: a strictly higher reading of the same station makes the run one longer;
        # anything else, including a station's first reading, starts a run of one.
        if station in previous and temperature > previous[station]:
            current[station] += 1
        else:
            current[station] = 1
        previous[station] = temperature

        # Step 3: keep the best run apart, as the last run may be a short one.
        best[station] = max(best.get(station, 0), current[station])

    return best


if __name__ == "__main__":
    # python student.py: runs the example from the question and prints the result.
    readings = [("s1", 1.0), ("s2", 9.0), ("s1", 2.0), ("s2", 8.0), ("s1", 3.0), ("s1", 3.0)]
    print(longest_rise(readings))  # {'s1': 3, 's2': 1}
