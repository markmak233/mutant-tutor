"""Reference solution for average_by_station."""

from pyspark import RDD


def average_by_station(readings: RDD) -> RDD:
    """Carry (sum, count) per station: pairs add up correctly in any order and across
    partitions, unlike averages. reduceByKey combines inside each partition before the
    shuffle, which is what the no-groupByKey constraint asks for."""
    sums_counts = readings.mapValues(lambda t: (t, 1)).reduceByKey(
        lambda a, b: (a[0] + b[0], a[1] + b[1])
    )
    return sums_counts.mapValues(lambda sc: sc[0] / sc[1])
