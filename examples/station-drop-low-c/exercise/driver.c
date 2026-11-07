/* Runs drop_below on one test read from standard input: the limit, n, then the n readings.
 * Prints the count drop_below returned, then the kept readings on one line. Given with the
 * exercise: do not change it. */
#include <stdio.h>
#include <stdlib.h>

#include "student.h"

int main(void)
{
    int limit, n;
    if (scanf("%d %d", &limit, &n) != 2 || n < 0) {
        fprintf(stderr, "driver: expected the limit and n\n");
        return 2;
    }
    /* Exactly n readings (room for one when n is 0, as malloc(0) may give NULL), so that
     * reading or writing past them is an error the sanitizer reports. */
    int *readings = malloc((n > 0 ? (size_t) n : 1) * sizeof *readings);
    if (readings == NULL)
        return 2;
    for (int i = 0; i < n; i++) {
        if (scanf("%d", &readings[i]) != 1) {
            fprintf(stderr, "driver: expected %d readings\n", n);
            free(readings);
            return 2;
        }
    }
    int kept = drop_below(readings, n, limit);
    printf("%d\n", kept);
    for (int i = 0; i < kept; i++) {
        if (i > 0)
            putchar(' ');
        printf("%d", readings[i]);
    }
    putchar('\n');
    free(readings);
    return 0;
}
