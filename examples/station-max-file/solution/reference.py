"""Reference solution for StationMax."""

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
        readings = sc.textFile(inputPath).map(parse_line).filter(lambda r: r is not None)
        maxima = readings.reduceByKey(max)
        # Format each pair as text first; saving the tuples would write "('s1', 20.0)".
        maxima.map(lambda kv: f"{kv[0]}\t{kv[1]:.1f}").saveAsTextFile(outputPath)
        sc.stop()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python student.py <input_dir> <output_dir>")
        sys.exit(-1)
    StationMax().run(sys.argv[1], sys.argv[2])
