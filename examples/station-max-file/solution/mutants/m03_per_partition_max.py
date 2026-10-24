"""Mutant: takes the maximum inside each partition and never merges partitions.

Looks right when all of a station's readings land in one partition; with several input
files a station appears once per file it occurs in.
"""

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


def local_maxima(partition):
    maxima = {}
    for station, temperature in partition:
        maxima[station] = max(maxima.get(station, temperature), temperature)
    return iter(maxima.items())


class StationMax:
    def run(self, inputPath, outputPath):
        sc = SparkContext(conf=SparkConf().setAppName("station max"))
        readings = sc.textFile(inputPath).map(parse_line).filter(lambda r: r is not None)
        maxima = readings.mapPartitions(local_maxima)
        maxima.map(lambda kv: f"{kv[0]}\t{kv[1]:.1f}").saveAsTextFile(outputPath)
        sc.stop()


if __name__ == "__main__":
    StationMax().run(sys.argv[1], sys.argv[2])
