/* Mutant: room for 100 readings only, so more overflow the array on the stack. */
#include <stdio.h>

#define MAX_READINGS 1000

/* Reads the count and that many readings into `readings`, which holds `max`. Returns the
 * count, or 0 when the input is invalid. */
static int read_readings(int readings[], int max)
{
    int n;
    if (scanf("%d", &n) != 1 || n < 1 || n > max)
        return 0;
    for (int i = 0; i < n; i++)
        if (scanf("%d", &readings[i]) != 1)
            return 0;
    return n;
}

int main(void)
{
    int readings[100];
    int n = read_readings(readings, MAX_READINGS);
    if (n == 0) {
        fprintf(stderr, "invalid input\n");
        return 1;
    }
    long sum = 0;  /* at most 1000 * 100000, which fits a long anywhere */
    for (int i = 0; i < n; i++)
        sum += readings[i];
    double average = (double) sum / n;
    printf("average %.2f\n", average);
    printf("above");
    for (int i = 0; i < n; i++)
        if (readings[i] > average)
            printf(" %d", readings[i]);
    printf("\n");
    return 0;
}
