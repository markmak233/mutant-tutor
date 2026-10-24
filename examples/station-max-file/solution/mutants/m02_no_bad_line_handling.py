"""Mutant: only skips the header, assuming every other line is well formed.

Crashes on blank lines and non-numeric temperatures, which real input files contain.
"""

import sys

from pyspark import SparkConf, SparkContext

HEADER = "station,date,temperature"


class StationMax:
    def run(self, inputPath, outputPath):
        sc = SparkContext(conf=SparkConf().setAppName("station max"))
        readings = (
            sc.textFile(inputPath)
            .filter(lambda line: line != HEADER)
            .map(lambda line: [field.strip() for field in line.split(",")])
            .map(lambda fields: (fields[0], float(fields[2])))
        )
        maxima = readings.reduceByKey(max)
        maxima.map(lambda kv: f"{kv[0]}\t{kv[1]:.1f}").saveAsTextFile(outputPath)
        sc.stop()


if __name__ == "__main__":
    StationMax().run(sys.argv[1], sys.argv[2])
