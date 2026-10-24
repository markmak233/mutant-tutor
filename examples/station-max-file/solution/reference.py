"""Reference solution for StationMax: the worked answer to compare with."""

import sys

from pyspark import SparkConf, SparkContext


def parse_line(line):
    """(station, temperature) for a valid line, None for anything to skip.
    The header fails the float() check like any other non-numeric temperature."""
    fields = [field.strip() for field in line.split(",")]
    if len(fields) != 3 or not fields[0]:
        return None
    try:
        return fields[0], float(fields[2])
    except ValueError:
        return None


class StationMax:
    def run(self, inputPath, outputPath):
        sc = SparkContext(conf=SparkConf().setAppName("station max"))

        # Step 1: read every file in inputPath; each element is one line of text.
        #   'station,date,temperature', 's1,2024-01-01,12.5', ' s1 , 2024-01-02 , 20.0 ', ''
        lines = sc.textFile(inputPath)

        # Step 2: parse each line into (station, temperature), or None for a line to skip,
        # then drop the Nones. All skip rules live in parse_line.
        #   ('s1', 12.5), ('s2', -3.0), ('s1', 20.0), ('s2', -1.5)
        readings = lines.map(parse_line).filter(lambda r: r is not None)

        # Step 3: the maximum per station. reduceByKey(max) keeps the larger of two values,
        # first inside each partition, then across partitions.
        #   ('s1', 20.0), ('s2', -1.5)
        maxima = readings.reduceByKey(max)

        # Step 4: turn each pair into the required text before saving; saving the tuples
        # would write "('s1', 20.0)".
        #   's1\t20.0', 's2\t-1.5'
        output = maxima.map(lambda kv: f"{kv[0]}\t{kv[1]:.1f}")
        output.saveAsTextFile(outputPath)

        sc.stop()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python student.py <input_dir> <output_dir>")
        sys.exit(-1)
    StationMax().run(sys.argv[1], sys.argv[2])
