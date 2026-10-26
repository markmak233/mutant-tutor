"""Monthly average temperature per station (PySpark DataFrame, file input/output)

Write a Spark program with the DataFrame API that reads weather readings and writes the
average temperature of every station in every month, in a fixed order, to one CSV file.

Input: every file in inputPath. The first line of every file is the header
    station,date,temperature          e.g.  s1,2024-01-05,12.5
Dates are yyyy-MM-dd; the month is the first 7 characters ("2024-01").
Fields may have spaces around them (" s1 , 2024-01-05 , 12.5 "). Some lines are not
usable; skip them:
- an empty or missing station or date
- a temperature that is empty, missing or not a number ("n/a")
No line has more than 3 fields.

Parameter: min_readings (a positive integer, the third command-line argument). Keep a
(station, month) only if it has at least min_readings usable readings.

Output: write to outputPath as CSV, no header, all rows in ONE part file:
    station,month,average             e.g.  s1,2024-01,16.25
average is the plain mean of the month's usable temperatures, unrounded.
Order: station ascending, then average descending, then month ascending for equal averages.

Example input file (min_readings = 2):
    station,date,temperature
    s1,2024-01-03,12.5
    s1,2024-01-20,20.0
     s1 , 2024-02-01 , 9.0
    s1,2024-02-14,n/a
    s1,2024-02-15,17.0
    s2,2024-01-09,4.0
    s2,2024-01-10,
    ,2024-01-11,30.0
    s2,2024-03-01,-1.0
    s2,2024-03-02,9.0
Output lines:
    s1,2024-01,16.25
    s1,2024-02,13.0
    s2,2024-03,4.0
(s2 has only one usable reading in 2024-01, so that month is left out.)

Constraints:
- DataFrame API only: no spark.sql("..."), no .rdd, no collect(); no pandas or numpy.
- The ordering must be done by Spark (orderBy), not in Python.

Run it yourself:  python student.py <input_dir> <output_dir> 2   (output_dir must not exist yet)
Run the tests:    pytest test_student.py
Stuck? hints.md has useful functions and three levels of hints.
"""

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
