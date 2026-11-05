"""Mutant: compares with the last reading of any station, not the same one."""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    previous = None
    current: dict[str, int] = {}
    best: dict[str, int] = {}
    for station, temperature in readings:
        if previous is not None and station in current and temperature > previous:
            current[station] += 1
        else:
            current[station] = 1
        previous = temperature
        best[station] = max(best.get(station, 0), current[station])
    return best
