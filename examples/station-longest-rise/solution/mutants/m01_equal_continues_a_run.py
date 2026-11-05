"""Mutant: an equal reading continues a run, so 1.0, 1.0, 2.0 is a run of 3."""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    previous: dict[str, float] = {}
    current: dict[str, int] = {}
    best: dict[str, int] = {}
    for station, temperature in readings:
        if station in previous and temperature >= previous[station]:
            current[station] += 1
        else:
            current[station] = 1
        previous[station] = temperature
        best[station] = max(best.get(station, 0), current[station])
    return best
