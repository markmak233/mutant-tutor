"""Mutant: prints the float as-is, so 3.14159 stays 3.14159 instead of 3.1."""

import sys

from pyspark import SparkConf, SparkContext


def parse_line(line):
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
        maxima.map(lambda kv: f"{kv[0]}\t{kv[1]}").saveAsTextFile(outputPath)
        sc.stop()


if __name__ == "__main__":
    StationMax().run(sys.argv[1], sys.argv[2])
