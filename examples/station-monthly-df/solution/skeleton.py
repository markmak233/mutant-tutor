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
            .getOrCreate()
        )

        # TODO: read inputPath, compute the monthly averages, write one ordered CSV file
        raise NotImplementedError

        spark.stop()


if __name__ == "__main__":
    StationMonthly().run(sys.argv[1], sys.argv[2], sys.argv[3])
