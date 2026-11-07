/* Mutant: counts the kept readings but never moves them to the front. */
#include "student.h"

int drop_below(int *readings, int n, int limit)
{
    int kept = 0;  /* readings[0 .. kept-1] are the ones kept so far */
    for (int i = 0; i < n; i++)
        if (readings[i] >= limit)
            kept++;
    return kept;
}
