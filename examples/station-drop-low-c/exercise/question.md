# Drop the low readings

> learn mode · implementation · easy

**Contents:** [Task](#task) · [Input](#input) · [Output](#output) · [Example](#example) · [Constraints](#constraints) · [How to run](#how-to-run)

---

## Task

Implement `drop_below(readings, n, limit)` in `student.c`: remove every reading below `limit`
from the first `n` elements of the array, in place, keeping the others in their order at the
front of the array, and return how many are kept.

## Input

| Parameter | Meaning |
|---|---|
| `readings` | the array, changed in place |
| `n` | how many readings it holds; may be 0 |
| `limit` | readings below it are dropped; one equal to it is kept |

## Output

The count of kept readings. What the array holds after the kept readings does not matter.

## Example

`readings` {4, 1, 7, 3, 9}, `n` 5, `limit` 4: the array starts with {4, 7, 9}, and
`drop_below` returns 3.

## Constraints

- Do not allocate memory: no `malloc`, `calloc` or `realloc`.

## How to run

`driver.c` reads one test from standard input (the limit, `n`, then the `n` readings), calls
`drop_below`, and prints the count and the kept readings.

```bash
cc -std=c11 -Wall student.c driver.c -o drop && echo "4 5  4 1 7 3 9" | ./drop
pytest test_student.py
```
