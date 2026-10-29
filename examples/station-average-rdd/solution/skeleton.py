"""Average temperature per station (PySpark RDD)

A network of weather stations sends readings as (station_id, temperature) pairs.
Implement average_by_station(readings) below.

Input:  an RDD of (str, float) pairs. A station may appear any number of times.
Output: an RDD of (station_id, average_temperature) pairs, exactly one per station
        present in the input. Order does not matter.

Example:
    input   [("s1", 10.0), ("s2", 4.0), ("s1", 20.0), ("s1", 30.0)]
    output  [("s1", 20.0), ("s2", 4.0)]
An empty input gives an empty output.

Constraints:
- Do not use groupByKey: it ships every single reading across the network.
  Combine readings locally first.
- Do not call collect inside the function; the result must stay distributed.
- The result must be correct for any number of partitions.

Run the example:  python student.py   (prints the result for the example above)
Run the tests:    pytest test_student.py
Stuck? hints.md has three levels of hints.
"""

from pyspark import RDD


def average_by_station(readings: RDD) -> RDD:
    """Return an RDD of (station_id, average temperature) pairs."""
    raise NotImplementedError


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
