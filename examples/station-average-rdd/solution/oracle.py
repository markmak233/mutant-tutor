"""Plain-Python specification of average_by_station, for differential testing.

No Spark: obviously correct by reading, so the Spark reference can be checked against it
on random inputs.
"""

from collections import defaultdict


def average_by_station(readings: list[tuple[str, float]]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for station, temperature in readings:
        totals[station] += temperature
        counts[station] += 1
    return {station: totals[station] / counts[station] for station in totals}
