/*
# Readings above the average

## Concept

### Why it is needed

The average is known only after the last reading, yet the readings above it are wanted in the
order they came. So every reading has to be kept until the end: in C, in an array sized for the
most the input may hold.

### Keeping readings in an array (general programming knowledge; a course exercise names its slides here)

1. Read the count first and check it, so the array is never written past its end.
2. Read the readings into the array, checking that each one is really there.
3. Add them up, divide as a `double`, then go through the array again.

> `sum / n` with two `int`s is whole-number division: one side must be a `double` first.

## Task

Write the program in `student.c`: read one station's readings from standard input and print
their average and the readings above it.

## Input

A count `n` (1 ≤ n ≤ 1000), then `n` whole numbers between -100000 and 100000, all separated by
any white space (spaces or new lines).

## Output

On standard output, then exit with status 0:

| Line | Meaning |
|---|---|
| `average A` | the average, with two decimals |
| `above X Y ...` | the readings strictly greater than the average, in input order, each after one space; just `above` when there is none |

If `n` is missing, outside 1..1000, or fewer than `n` numbers follow it, print `invalid input` on
standard error, nothing on standard output, and exit with status 1.

## Example

```text
input   4
        10 20
        30 5
output  average 16.25
        above 20 30
```

## Constraints

1. **Strictly greater**: a reading equal to the average is not above it.
2. **Input order**: the readings above are printed in the order they were read.

## Slides

None: a synthetic example, not from a course.

## Run

```bash
cc -std=c11 -Wall student.c -o readings && ./readings < input.txt
```
*/
#include <stdio.h>

int main(void)
{
    /* TODO: read n and the readings, then print the two lines. */
    return 0;
}
