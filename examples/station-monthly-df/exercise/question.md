# Monthly average temperature per station (PySpark DataFrame, file input/output)

## Concept

The DataFrame API describes the result, not the steps: you say which rows to keep,  
how to group them and how to order them, and Spark plans how to compute it, cleaning  
included.  
How it works (general Spark knowledge; a course exercise names its slides here):  
reading CSV with a header gives named string columns; trim removes spaces around values.  
Casting a column to a number turns every value it cannot parse into null, so one filter  
on "not null" drops all unusable rows at once. groupBy(...).agg(...) computes per-group  
values such as count and avg; a condition on an aggregate is a filter after the agg.  
orderBy sorts across all partitions, with one direction per column. Writing produces one  
part file per partition of the DataFrame being written, so the number of partitions at  
that moment decides how many files appear.

## Task

Write a Spark program with the DataFrame API that writes the average temperature of every  
station in every month, in a fixed order, to one CSV file.

## Input

Every file in inputPath. The first line of every file is the header  
station,date,temperature          e.g.  s1,2024-01-05,12.5

Dates are yyyy-MM-dd; the month is the first 7 characters ("2024-01").  
Fields may have spaces around them (" s1 , 2024-01-05 , 12.5 "). Some lines are not  
usable; skip them:
- an empty or missing station or date
- a temperature that is empty, missing or not a number ("n/a")  
No line has more than 3 fields.

## Parameter

min_readings (a positive integer, the third command-line argument). Keep a  
(station, month) only if it has at least min_readings usable readings.

## Output

Write to outputPath as CSV, no header, all rows in ONE part file:  
station,month,average             e.g.  s1,2024-01,16.25

average is the plain mean of the month's usable temperatures, unrounded.

## Order

station ascending, then average descending, then month ascending for equal averages.

## Example

```text
input file (min_readings = 2)
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
output lines
    s1,2024-01,16.25
    s1,2024-02,13.0
    s2,2024-03,4.0
```

s2 has only one usable reading in 2024-01, so that month is left out.

## Constraints

- DataFrame API only: no spark.sql("..."), no .rdd, no collect(); no pandas or numpy.
- The ordering must be done by Spark (orderBy), not in Python.

## Slides

None: a synthetic example, not from a course.

## Run

```text
rm -rf sample_output && python student.py sample_input sample_output 2
cat sample_output/part-*
pytest test_student.py
```

sample_input/ holds the example above; Spark refuses to write into an existing output dir.  
Stuck? hints.md has useful functions and three levels of hints.

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-monthly-df/exercise"
pytest test_student.py      # the tests
python student.py           # the program, with the arguments Run shows, if any
```

Stuck? `hints.md` in this folder has three levels of hints, each more specific; or, from the repository's folder: `python -m tutor.hint "examples/station-monthly-df/exercise"`
