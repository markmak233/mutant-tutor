/*
 * Readings above the average
 *
 * Read one station's temperature readings from standard input, and print their average
 * and the readings above it.
 *
 * Input: a count n (1 <= n <= 1000), then n whole numbers between -100000 and 100000, all
 * separated by any white space (spaces or new lines).
 *
 * Output, on standard output, then exit with status 0:
 *   average A       the average, with two decimals
 *   above X Y ...   the readings strictly greater than the average, in input order, each
 *                   after one space; just "above" when there is none
 *
 * If n is missing, outside 1..1000, or fewer than n numbers follow it, print
 * "invalid input" on standard error, nothing on standard output, and exit with status 1.
 *
 * Example: the input "4\n10 20\n30 5\n" prints
 *   average 16.25
 *   above 20 30
 *
 * Compile and run:  cc -std=c11 -Wall student.c -o readings && ./readings < input.txt
 */
#include <stdio.h>

int main(void)
{
    /* TODO: read n and the readings, then print the two lines. */
    return 0;
}
