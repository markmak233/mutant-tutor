"""Mutant: averages two values at a time, so it averages averages.

Wrong as soon as a station has three or more readings: ((10 + 20) / 2 + 30) / 2 = 22.5, not 20.
"""

from pyspark import RDD


def average_by_station(readings: RDD) -> RDD:
    return readings.reduceByKey(lambda a, b: (a + b) / 2)
