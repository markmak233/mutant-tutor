"""Mutant: averages inside each partition, then averages those partial averages.

Correct with one partition. Wrong once a station's readings are split unevenly across
partitions, e.g. [10] and [20, 30] give (10 + 25) / 2 = 17.5, not 20. This is the bug the
partition check (validation rule 5) exists for.
"""

from pyspark import RDD


def average_by_station(readings: RDD) -> RDD:
    def local_averages(partition):
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for station, temperature in partition:
            totals[station] = totals.get(station, 0.0) + temperature
            counts[station] = counts.get(station, 0) + 1
        for station, total in totals.items():
            yield station, (total / counts[station], 1)

    return (
        readings.mapPartitions(local_averages)
        .reduceByKey(lambda a, b: (a[0] + b[0], a[1] + b[1]))
        .mapValues(lambda sc: sc[0] / sc[1])
    )
