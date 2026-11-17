/* F1 port in plain C: notebook Section 2 (load), the cleaning that 6.4
 * needs, 6.4 (driver/constructor form) and 5.1 (Q1).
 *
 * Usage:
 *   make -C impl/c
 *   impl/c/build/f1 data/
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "csv.h"

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

/* ---------------------------------------------------------------------------
 * Section 2: load the 14 tables (same list and order as impl/python/load.py)
 * ------------------------------------------------------------------------- */

static const char *TABLE_NAMES[] = {
    "results", "races", "drivers", "constructors", "qualifying", "status",
    "circuits", "constructor_results", "constructor_standings",
    "driver_standings", "lap_times", "pit_stops", "sprint_results", "seasons",
};
enum { N_TABLES = sizeof TABLE_NAMES / sizeof TABLE_NAMES[0] };

typedef struct {
    CsvTable t[N_TABLES];
} Tables;

static double load_tables(const char *data_dir, Tables *tables)
{
    double total = 0;
    char path[4096];
    for (size_t i = 0; i < N_TABLES; i++) {
        snprintf(path, sizeof path, "%s/%s.csv", data_dir, TABLE_NAMES[i]);
        double start = now();
        tables->t[i] = csv_read(path);
        double seconds = now() - start;
        total += seconds;
        printf("%-22s %9zu rows  %3zu cols  %8.4f s\n",
               TABLE_NAMES[i], tables->t[i].nrows, tables->t[i].ncols, seconds);
    }
    printf("%-22s %34.4f s\n", "load total", total);
    return total;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s DATA_DIR\n", argv[0]);
        return 2;
    }
    Tables tables;
    load_tables(argv[1], &tables);

    for (size_t i = 0; i < N_TABLES; i++)
        csv_free(&tables.t[i]);
    return 0;
}
