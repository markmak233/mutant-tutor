"""Reference solution for average_by_station: the worked answer to compare with."""

from pyspark import RDD


def average_by_station(readings: RDD) -> RDD:
    # Input records are (station, temperature):
    #   ('s1', 10.0), ('s1', 20.0), ('s2', 5.0)

    # Step 1: pair every temperature with a count of 1. (sum, count) pairs can be added up in
    # any order and across partitions; averages cannot (the average of averages is wrong).
    #   ('s1', (10.0, 1)), ('s1', (20.0, 1)), ('s2', (5.0, 1))
    sum_count_pairs = readings.mapValues(lambda t: (t, 1))

    # Step 2: add up the pairs of each station. reduceByKey combines inside each partition
    # before the shuffle and then merges the partial results, which is why it is used
    # instead of groupByKey (forbidden here).
    #   ('s1', (30.0, 2)), ('s2', (5.0, 1))
    totals = sum_count_pairs.reduceByKey(lambda a, b: (a[0] + b[0], a[1] + b[1]))

    # Step 3: divide once, at the end.
    #   ('s1', 15.0), ('s2', 5.0)
    return totals.mapValues(lambda sum_count: sum_count[0] / sum_count[1])


if __name__ == "__main__":
    # python student.py: runs the example from the question and prints the result.
    import os
    import sys

    from pyspark import SparkContext

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)  # Spark workers use this Python
    sc = SparkContext("local[2]", "station-average-example")
    sc.setLogLevel("ERROR")
    try:
        readings = sc.parallelize([("s1", 10.0), ("s2", 4.0), ("s1", 20.0), ("s1", 30.0)])
        print(sorted(average_by_station(readings).collect()))  # [('s1', 20.0), ('s2', 4.0)]
    finally:
        sc.stop()
