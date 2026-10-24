"""Mutant: floor division drops the fractional part, so 4.5 becomes 4.0."""

from pyspark import RDD


def average_by_station(readings: RDD) -> RDD:
    sums_counts = readings.mapValues(lambda t: (t, 1)).reduceByKey(
        lambda a, b: (a[0] + b[0], a[1] + b[1])
    )
    return sums_counts.mapValues(lambda sc: sc[0] // sc[1])
