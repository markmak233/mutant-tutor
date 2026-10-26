# Hints

## Useful functions

The examples use different data from the exercise (shop sales), so they show the tool, not
the answer. `sales.csv`:
```
shop,day,amount
A,2024-03-01,10
 B ,2024-03-02, 4.5
A,2024-04-01,abc
A,2024-04-02,6
```

### `spark.read.csv(path, header=True)` — columns named by the header, every value a string
```python
df = spark.read.csv("sales.csv", header=True)   # a directory works too: every file in it
df.columns    # ['shop', 'day', 'amount']
df.show()
# +----+----------+------+
# |shop|       day|amount|
# +----+----------+------+
# |   A|2024-03-01|    10|
# |  B |2024-03-02|   4.5|
# |   A|2024-04-01|   abc|
# |   A|2024-04-02|     6|
# +----+----------+------+
```
With `header=True` the first line of every file is taken as the header, not as data.

### `select` with `trim`, `substring` and `cast`
```python
from pyspark.sql.functions import col, substring, trim

clean = df.select(
    trim(col("shop")).alias("shop"),                     # " B " -> "B"
    substring(col("day"), 1, 7).alias("ym"),             # 1-based: "2024-03"
    trim(col("amount")).cast("double").alias("amount"),  # "abc" -> NULL, no error
)
clean.show()
# +----+-------+------+
# |shop|     ym|amount|
# +----+-------+------+
# |   A|2024-03|  10.0|
# |   B|2024-03|   4.5|
# |   A|2024-04|  NULL|
# |   A|2024-04|   6.0|
# +----+-------+------+
```
A failed `cast` gives NULL instead of crashing, so bad numbers become NULLs you can handle.

### `groupBy(...).agg(...)` — `count(column)` skips NULLs, `count("*")` does not
```python
from pyspark.sql.functions import avg, count

clean.groupBy("shop").agg(
    avg("amount").alias("mean"), count("amount").alias("n"), count("*").alias("rows")
).orderBy("shop").show()
# +----+----+---+----+
# |shop|mean|  n|rows|
# +----+----+---+----+
# |   A| 8.0|  2|   3|
# |   B| 4.5|  1|   1|
# +----+----+---+----+
```
`avg` ignores NULLs too: shop A's mean is (10 + 6) / 2.

### `filter` and `orderBy` with a direction per column
```python
clean.filter(col("amount") >= 5).show()               # NULL rows never pass a comparison
clean.orderBy(col("shop").asc(), col("amount").desc()).show()
# +----+-------+------+
# |shop|     ym|amount|
# +----+-------+------+
# |   A|2024-03|  10.0|
# |   A|2024-04|   6.0|
# |   A|2024-04|  NULL|
# |   B|2024-03|   4.5|
# +----+-------+------+
```

### Writing one ordered CSV file
```python
result.orderBy(...).coalesce(1).write.csv("out")      # out/part-00000-....csv, no header
```
`coalesce(1)` after `orderBy` joins the sorted partitions in order into one file. The
output directory must not exist yet.

## Level 1
Four stages: clean each row (trim, take the month, turn the temperature into a number or
NULL), drop rows without a station or date, aggregate per station and month (mean and the
number of usable readings), then filter, order and write.

## Level 2
`select(trim(...), substring(trim(col("date")), 1, 7), trim(...).cast("double"))`, then
filter out empty stations and months. `groupBy("station", "month").agg(avg(...),
count("temperature"))`: counting the temperature column skips the NULLs left by bad numbers.

## Level 3
After the aggregation: `.filter(col("n") >= min_readings)`, then
`.orderBy(col("station").asc(), col("average").desc(), col("month").asc())`,
`.select("station", "month", "average")`, `.coalesce(1).write.csv(outputPath)`, and finally
`spark.stop()`.
