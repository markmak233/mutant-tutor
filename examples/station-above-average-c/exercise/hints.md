# Hints

## Useful functions

The examples use different data from the exercise, so they show the tool, not the answer.

### `scanf("%d", &x)`: read one whole number, skipping white space
```c
int x;
if (scanf("%d", &x) != 1) {
    /* no number left, or something that is not a number */
}
```

### `printf("%.2f", value)`: a number with two decimals
```c
printf("%.2f\n", 2.0 / 3);   /* 0.67 */
```

## Hint 1
The average is known only after the last reading, but the readings above it must be printed in
input order: what has to be kept while reading?

## Hint 2
An array of 1000 ints holds the most the question allows. Read the count first and check it
before reading into the array.

## Hint 3
`sum / n` with two ints is whole-number division. Make one side a double before dividing, and
compare each reading with that double.
