"""Mutant: count("*") also counts rows whose temperature is not a
number, so such months can pass min_readings."""

import sys

from pyspark.sql import SparkSession
from pyspark.sql.functions import avg, col, count, substring, trim


class StationMonthly:
    def run(self, inputPath, outputPath, min_readings):
        # Command-line arguments arrive as strings.
        min_readings = int(min_readings)
        spark = (
            SparkSession.builder.master("local[2]").appName("station-monthly")
            .config("spark.sql.shuffle.partitions", "4")
            .getOrCreate()
        )

        # Step 1: read every CSV file in inputPath. header=True takes the first line of each
        # file as the column names, so headers never become data. Every value is a string.
        #   station | date           | temperature
        #   " s1 "  | " 2024-02-01 " | " 9.0 "
        #   "s1"    | "2024-02-14"   | "n/a"
        readings = spark.read.csv(inputPath, header=True)

        # Step 2: clean each column.
        #   station:     trim the spaces around it
        #   month:       the first 7 characters of the trimmed date ("2024-02")
        #   temperature: trim, then cast to double; "n/a" or "" becomes NULL instead of an error
        #   station | month     | temperature
        #   "s1"    | "2024-02" | 9.0
        #   "s1"    | "2024-02" | NULL
        cleaned = readings.select(
            trim(col("station")).alias("station"),
            substring(trim(col("date")), 1, 7).alias("month"),
            trim(col("temperature")).cast("double").alias("temperature"),
        )

        # Step 3: drop rows without a station or a date. A missing field is NULL, a field of
        # only spaces is "" after trim, so both are checked. Rows with a NULL temperature stay
        # for now: Step 4 ignores them.
        has_station = col("station").isNotNull() & (col("station") != "")
        has_month = col("month").isNotNull() & (col("month") != "")
        usable = cleaned.filter(has_station & has_month)

        # Step 4: one row per (station, month) with the mean temperature and the number of
        # usable readings. avg() and count("temperature") both skip NULL temperatures;
        # count("*") would also count the "n/a" rows.
        #   station | month     | average | n
        #   "s1"    | "2024-02" | 13.0    | 2
        monthly = usable.groupBy("station", "month").agg(
            avg("temperature").alias("average"),
            count("*").alias("n"),
        )

        # Step 5: keep the months with at least min_readings usable readings.
        enough = monthly.filter(col("n") >= min_readings)

        # Step 6: the required order, done by Spark: station ascending, then average
        # descending, then month ascending when two averages are equal.
        ordered = enough.orderBy(col("station").asc(), col("average").desc(), col("month").asc())

        # Step 7: keep the three output columns and write one CSV file without a header.
        # coalesce(1) after the sort joins the sorted partitions in order, so the file stays
        # sorted.
        #   s1,2024-01,16.25
        #   s1,2024-02,13.0
        ordered.select("station", "month", "average").coalesce(1).write.csv(outputPath)

        spark.stop()


if __name__ == "__main__":
    StationMonthly().run(sys.argv[1], sys.argv[2], sys.argv[3])
