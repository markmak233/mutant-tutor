# Monthly average temperature per station (PySpark DataFrame, file input/output)

> learn mode · implementation · easy

**Contents:** [Concept](#concept) · [Task](#task) · [Input](#input) · [Output](#output) · [Example](#example) · [Constraints](#constraints) · [Slides](#slides) · [How to run](#how-to-run)

---

## Concept

### Why it is needed

The DataFrame API describes the result, not the steps: you say which rows to keep, how
to group them and how to order them, and Spark plans how to compute it, cleaning
included.

### The operations (general Spark knowledge; a course exercise names its slides here)

| Operation | What it does |
|---|---|
| read CSV with a header | named columns, every value a string |
| `trim` | removes the spaces around a value |
| cast to a number | a value it cannot parse becomes `null`: one "not null" filter drops them |
| `groupBy(...).agg(...)` | per-group values (`count`, `avg`); filter on them after the `agg` |
| `orderBy` | sorts across all partitions, one direction per column |
| write | one part file per partition of the DataFrame being written |

> The number of partitions at the moment of writing decides how many files appear.

---

## Task

Write a Spark program with the DataFrame API that writes the average temperature of
every station in every month, in a fixed order, to one CSV file.

## Input

| Argument | Meaning |
|---|---|
| `inputPath` | a directory; the first line of every file in it is the header |
| `outputPath` | the directory to create for the result |
| `min_readings` | a positive integer: the fewest usable readings a (station, month) needs |

Each line after the header is

```text
station,date,temperature          e.g.  s1,2024-01-05,12.5
```

Dates are `yyyy-MM-dd`; the month is the first 7 characters (`2024-01`). Fields may have
spaces around them (` s1 , 2024-01-05 , 12.5 `). Skip the lines that are not usable:

- an empty or missing station or date;
- a temperature that is empty, missing or not a number (`n/a`).

No line has more than 3 fields.

## Output

CSV in `outputPath`, no header, all rows in **one** part file:

```text
station,month,average             e.g.  s1,2024-01,16.25
```

`average` is the plain mean of the month's usable temperatures, unrounded. Order: station
ascending, then average descending, then month ascending for equal averages.

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

`s2` has only one usable reading in 2024-01, so that month is left out.

## Constraints

1. **DataFrame API only**: no `spark.sql("...")`, no `.rdd`, no `collect()`; no pandas or
   numpy.
2. The ordering is done **by Spark** (`orderBy`), not in Python.

## Slides

None: a synthetic example, not from a course.

---

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-monthly-df/exercise"
pytest test_student.py      # the tests
rm -rf sample_output && python student.py sample_input sample_output 2
cat sample_output/part-*
```

`sample_input/` holds the example above; Spark refuses to write into an existing output directory.

> **Stuck?** `hints.md` in this folder has three levels of hints, each more specific; or ask
> for a hint from the repository's folder:
>
> ```bash
> python -m tutor.hint "examples/station-monthly-df/exercise"
> ```
