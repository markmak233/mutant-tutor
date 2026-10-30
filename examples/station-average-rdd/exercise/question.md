# Average temperature per station (PySpark RDD)

## Concept

An average per key needs every value of the key, but shipping all values to one place  
(groupByKey) moves the whole dataset across the network. reduceByKey avoids that by  
combining values before the shuffle, as long as the combining function allows it.  
How it works (general Spark knowledge; a course exercise names its slides here):  
reduceByKey(f) first combines the values of each key inside every partition, then sends  
one partial result per key and partition across the network, and combines those. Spark  
may group a key's values in any way and in any order, so f must be associative and  
commutative: f(f(a, b), c) == f(a, f(b, c)) and f(a, b) == f(b, a). A sum or a maximum  
qualifies; "the average of two values" does not, since averages of averages weigh the  
values unevenly. The values do not have to be plain numbers: any value type works if f  
combines it correctly in any grouping. The result has exactly one record per key.

## Task

Implement average_by_station(readings): the average temperature of every station.

## Input

readings  an RDD of (station_id, temperature) pairs, (str, float); a station may  
appear any number of times

## Output

an RDD of (station_id, average_temperature) pairs, exactly one per station present  
in the input, in any order. An empty input gives an empty output.

## Example

```text
input   [("s1", 10.0), ("s2", 4.0), ("s1", 20.0), ("s1", 30.0)]
output  [("s1", 20.0), ("s2", 4.0)]
```

## Constraints

- Do not use groupByKey: it ships every single reading across the network.  
Combine readings locally first.
- Do not call collect inside the function; the result must stay distributed.
- The result must be correct for any number of partitions.

## Slides

None: a synthetic example, not from a course.

## Run

```text
python student.py
pytest test_student.py
```

Stuck? hints.md has three levels of hints.

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-average-rdd/exercise"
pytest test_student.py      # the tests
python student.py           # the program, with the arguments Run shows, if any
```

Stuck? `hints.md` in this folder has three levels of hints, each more specific; or, from the repository's folder: `python -m tutor.hint "examples/station-average-rdd/exercise"`
