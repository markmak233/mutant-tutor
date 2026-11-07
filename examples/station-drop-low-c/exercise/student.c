/*
 * Drop the low readings
 *
 * Write drop_below: remove every reading below `limit` from the first n elements of the
 * array `readings`, in place, keeping the others in their order at the front of the array,
 * and return how many are kept. What the array holds after the kept readings does not
 * matter. Do not allocate memory (no malloc, calloc or realloc); n may be 0.
 *
 * Example: readings {4, 1, 7, 3, 9}, n 5, limit 4: the array starts with {4, 7, 9}, and
 * drop_below returns 3.
 *
 * driver.c reads one test from standard input (the limit, n, then the n readings), calls
 * drop_below, and prints the count and the kept readings. Build and try it:
 *   cc -std=c11 -Wall student.c driver.c -o drop && echo "4 5  4 1 7 3 9" | ./drop
 */
#include "student.h"

int drop_below(int *readings, int n, int limit)
{
    /* TODO */
    return 0;
}
