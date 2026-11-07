# Drop the low readings

> learn mode · implementation · easy

**Contents:** [Concept](#concept) · [Task](#task) · [Input](#input) · [Output](#output) · [Example](#example) · [Constraints](#constraints) · [Slides](#slides) · [How to run](#how-to-run)

---

## Concept

### Why it is needed

A function that gets an array in C gets a pointer to its first element: what it writes there,
the caller sees. So an array can be filtered in place, without a second one, as long as the
function says how many elements are left.

### Filtering in place (general programming knowledge; a course exercise names its slides here)

1. One index goes through every element; a second one marks where the next kept element goes.
2. A kept element is copied to the second index, which then moves on; the second index never
   passes the first.
3. At the end the second index is the count kept.

> Reading `readings[n]` is one past the end: the loop runs while the index is below `n`.

---

## Task

Implement `drop_below(readings, n, limit)` in `student.c`: remove every reading below `limit`
from the first `n` elements of the array, in place, and return how many are kept.

## Input

| Parameter | Meaning |
|---|---|
| `readings` | the array, changed in place |
| `n` | how many readings it holds; may be 0 |
| `limit` | readings below it are dropped; one equal to it is kept |

## Output

The count of kept readings, which are at the front of the array in their order. What the array
holds after them does not matter.

## Example

```text
input   readings {4, 1, 7, 3, 9}, n 5, limit 4
output  returns 3; the array starts with {4, 7, 9}
```

## Constraints

1. **In place**: no `malloc`, `calloc` or `realloc`.
2. **Order kept**: the kept readings stay in the order they were.

## Slides

None: a synthetic example, not from a course.

---

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-drop-low-c/exercise"
pytest test_student.py      # the tests
cc -std=c11 -Wall student.c driver.c -o drop && echo "4 5  4 1 7 3 9" | ./drop
```

`driver.c` reads one test from standard input (the limit, `n`, then the `n` readings), calls `drop_below`, and prints the count and the kept readings.

> **Stuck?** `hints.md` in this folder has three levels of hints, each more specific; or ask
> for a hint from the repository's folder:
>
> ```bash
> python -m tutor.hint "examples/station-drop-low-c/exercise"
> ```
