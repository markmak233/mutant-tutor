# Readings above the average

> learn mode · implementation · easy

**Contents:** [Task](#task) · [Input](#input) · [Output](#output) · [Example](#example) · [How to run](#how-to-run)

---

## Task

Write the program in `student.c`: read one station's temperature readings from standard
input, and print their average and the readings above it.

## Input

A count `n` (1 ≤ n ≤ 1000), then `n` whole numbers between -100000 and 100000, all separated
by any white space (spaces or new lines).

## Output

On standard output, then exit with status 0:

| Line | Meaning |
|---|---|
| `average A` | the average, with two decimals |
| `above X Y ...` | the readings strictly greater than the average, in input order, each after one space; just `above` when there is none |

If `n` is missing, outside 1..1000, or fewer than `n` numbers follow it, print
`invalid input` on standard error, nothing on standard output, and exit with status 1.

## Example

The input

```text
4
10 20
30 5
```

prints

```text
average 16.25
above 20 30
```

## How to run

```bash
cc -std=c11 -Wall student.c -o readings && ./readings < input.txt
pytest test_student.py
```
