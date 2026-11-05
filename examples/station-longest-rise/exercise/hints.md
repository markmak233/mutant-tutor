# Hints

## Useful functions

The examples use different data from the exercise, so they show the tool, not the answer.

### `dict.get(key, default)`: a value that may not be there yet
```python
seen = {"a": 2}
seen.get("a", 0), seen.get("b", 0)
# (2, 0)
```

### `max(a, b)`: the larger of two values
```python
max(3, 5)
# 5
```

## Hint 1
Going through the readings once is enough, but one number per station is not: what does each
station have to remember from one reading to its next?

## Hint 2
Keep three dicts keyed by station: the previous reading, the current run and the best run.

## Hint 3
For each reading: if the station has a previous reading and this one is higher, the current run
grows by one, otherwise it is 1. Then update the previous reading and the best run.
