"""Mutant: returns the current run at the end instead of the best one."""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    previous: dict[str, float] = {}
    current: dict[str, int] = {}
    for station, temperature in readings:
        if station in previous and temperature > previous[station]:
            current[station] += 1
        else:
            current[station] = 1
        previous[station] = temperature
    return current
