"""Mutant: a fall does not start the run again, so it counts every rise."""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    previous: dict[str, float] = {}
    current: dict[str, int] = {}
    best: dict[str, int] = {}
    for station, temperature in readings:
        if station not in previous:
            current[station] = 1
        elif temperature > previous[station]:
            current[station] += 1
        previous[station] = temperature
        best[station] = max(best.get(station, 0), current[station])
    return best
