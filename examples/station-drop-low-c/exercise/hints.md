# Hints

## Useful functions

The examples use different data from the exercise, so they show the tool, not the answer.

### Writing through a pointer: the caller sees the change
```c
void double_first(int *values)
{
    values[0] = 2 * values[0];   /* the caller's array changes */
}
```

## Hint 1
No second array is needed: the kept readings can be written over the front of the same array.

## Hint 2
Keep two positions: `i` goes through every reading, and `kept` is where the next kept reading
goes. `kept` is never ahead of `i`.

## Hint 3
For each reading at `i`: if it is not below the limit, copy it to `readings[kept]` and move
`kept` on by one. At the end, `kept` is the count to return.
