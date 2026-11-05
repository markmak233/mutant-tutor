r"""# Longest rising run per station

## Concept

### Why it is needed

How long temperatures kept rising says more about a warm spell than any single reading. The
readings of all stations arrive mixed together, in time order, so each station's run has to be
followed on its own, one reading at a time.

### Following a run (general programming knowledge; a course exercise names its slides here)

Each station needs its own previous reading, its current run and its best run so far:

| New reading against the previous one | Current run |
|---|---|
| higher | one longer |
| equal or lower | starts again at 1: this reading alone |

> The best run is kept apart from the current one, as the last run may be a short one.

## Task

Implement `longest_rise(readings)`: the length of every station's longest rising run.

## Input

| Parameter | Meaning |
|---|---|
| `readings` | a list of `(station_id, temperature)` pairs in time order, stations mixed |

## Output

A `dict` mapping each station in the input to the number of readings in its longest run. An empty
input gives an empty `dict`.

## Example

```text
input   [("s1", 1.0), ("s2", 9.0), ("s1", 2.0), ("s2", 8.0), ("s1", 3.0), ("s1", 3.0)]
output  {"s1": 3, "s2": 1}
```

| Station | Readings | Current run after each | Best |
|---|---|---|---|
| `s1` | 1.0, 2.0, 3.0, 3.0 | 1, 2, 3, 1 | 3 |
| `s2` | 9.0, 8.0 | 1, 1 | 1 |

## Constraints

1. **Strictly rising**: an equal reading ends a run, as in the last step of `s1`.
2. **Each station on its own**: a reading is compared only with the same station's previous one.

## Slides

None: a synthetic example, not from a course.
"""


def longest_rise(readings: list[tuple[str, float]]) -> dict[str, int]:
    """Return {station_id: number of readings in its longest strictly rising run}."""
    raise NotImplementedError


if __name__ == "__main__":
    # python student.py: runs the example from the question and prints the result.
    readings = [("s1", 1.0), ("s2", 9.0), ("s1", 2.0), ("s2", 8.0), ("s1", 3.0), ("s1", 3.0)]
    print(longest_rise(readings))  # {'s1': 3, 's2': 1}
