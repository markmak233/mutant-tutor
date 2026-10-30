# Average temperature per station (PySpark RDD)

## Concept

reduceByKey combines a key's values inside each partition before the shuffle, so its  
function must give the same answer however the values are grouped.

## Task

Implement average_by_station(readings): the average temperature of every station.

## Input

```text
readings  an RDD of (station_id, temperature) pairs, (str, float); a station may
          appear any number of times
```

## Output

```text
an RDD of (station_id, average_temperature) pairs, exactly one per station present
in the input, in any order. An empty input gives an empty output.
```

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

Stuck? From the repository's folder: `python -m tutor.hint "examples/station-average-rdd/exercise"`
