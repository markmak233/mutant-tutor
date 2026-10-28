"""Pristine skeleton of station-monthly-df (the code part of exercise/student.py)."""

import sys

from pyspark.sql import SparkSession
from pyspark.sql.functions import avg, col, count, substring, trim


class StationMonthly:
    def run(self, inputPath, outputPath, min_readings):
        min_readings = int(min_readings)
        spark = (
            SparkSession.builder.master("local[2]").appName("station-monthly")
            .config("spark.sql.shuffle.partitions", "4")
            # As on a cluster with real data: adaptive execution would merge these tiny
            # partitions into one and hide a missing coalesce(1).
            .config("spark.sql.adaptive.enabled", "false")
            .getOrCreate()
        )

        # TODO: read inputPath, compute the monthly averages, write one ordered CSV file
        raise NotImplementedError

        spark.stop()


if __name__ == "__main__":
    StationMonthly().run(sys.argv[1], sys.argv[2], sys.argv[3])
