"""Plain-Python specification of longest_rise, for differential testing.

A different method from the reference: split the readings by station, then try every stretch
of each station's readings. Slow, and obviously correct by reading.
"""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    by_station: dict[str, list[float]] = {}
    for station, temperature in readings:
        by_station.setdefault(station, []).append(temperature)

    result = {}
    for station, values in by_station.items():
        best = 0
        for start in range(len(values)):
            for end in range(start, len(values)):
                stretch = values[start:end + 1]
                if all(stretch[i] < stretch[i + 1] for i in range(len(stretch) - 1)):
                    best = max(best, len(stretch))
        result[station] = best
    return result
