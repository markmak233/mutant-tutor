# Average temperature per station (PySpark RDD)

> learn mode · implementation · easy

**Contents:** [Concept](#concept) · [Task](#task) · [Input](#input) · [Output](#output) · [Example](#example) · [Constraints](#constraints) · [Slides](#slides) · [How to run](#how-to-run)

---

## Concept

### Why it is needed

An average per key needs every value of the key, but shipping all values to one place
(`groupByKey`) moves the whole dataset across the network. `reduceByKey` avoids that by
combining values before the shuffle, as long as the combining function allows it.

### How reduceByKey combines (general Spark knowledge; a course exercise names its slides here)

1. Inside every partition, the values of each key are combined with $f$.
2. One partial result per key and partition crosses the network.
3. The partial results of a key are combined with $f$ again.

Spark may group a key's values in any way and in any order, so $f$ must be:

| Property | Meaning |
|---|---|
| associative | $f(f(a, b), c) = f(a, f(b, c))$ |
| commutative | $f(a, b) = f(b, a)$ |

> A sum or a maximum qualifies; "the average of two values" does not, since averages of
> averages weigh the values unevenly.

The values do not have to be plain numbers: any value type works if $f$ combines it
correctly in any grouping. The result has exactly one record per key.

---

## Task

Implement `average_by_station(readings)`: the average temperature of every station.

## Input

| Parameter | Meaning |
|---|---|
| `readings` | an RDD of `(station_id, temperature)` pairs, `(str, float)` |

A station may appear any number of times.

## Output

An RDD of `(station_id, average_temperature)` pairs, exactly one per station present in
the input, in any order. An empty input gives an empty output.

## Example

```text
input   [("s1", 10.0), ("s2", 4.0), ("s1", 20.0), ("s1", 30.0)]
output  [("s1", 20.0), ("s2", 4.0)]
```

## Constraints

1. **No `groupByKey`**: it ships every single reading across the network. Combine
   readings locally first.
2. **No `collect`** inside the function: the result must stay distributed.
3. The result must be correct for **any number of partitions**.

## Slides

None: a synthetic example, not from a course.

---

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-average-rdd/exercise"
pytest test_student.py      # the tests
python student.py           # the program
```

> **Stuck?** `hints.md` in this folder has three levels of hints, each more specific; or ask
> for a hint from the repository's folder:
>
> ```bash
> python -m tutor.hint "examples/station-average-rdd/exercise"
> ```
