/* Mutant: the loop reads one past the end of the array. */
#include "student.h"

int drop_below(int *readings, int n, int limit)
{
    int kept = 0;  /* readings[0 .. kept-1] are the ones kept so far */
    for (int i = 0; i <= n; i++)
        if (readings[i] >= limit)
            readings[kept++] = readings[i];
    return kept;
}
