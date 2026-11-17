/* F1 port in plain C: notebook Section 2 (load), the cleaning that 6.4
 * needs, 6.4 (driver/constructor form) and 5.1 (Q1).
 *
 * Usage:
 *   make -C impl/c
 *   impl/c/build/f1 data/
 */
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "csv.h"
#include "pairmap.h"

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

static const CsvTable *get_table(const Tables *tables, const char *name)
{
    for (size_t i = 0; i < N_TABLES; i++)
        if (strcmp(TABLE_NAMES[i], name) == 0)
            return &tables->t[i];
    fprintf(stderr, "no table '%s'\n", name);
    exit(1);
}

static void *xmalloc(size_t n)
{
    void *p = malloc(n ? n : 1);
    if (!p) {
        fprintf(stderr, "out of memory\n");
        exit(1);
    }
    return p;
}

/* Lookup from an id (raceId, statusId, ...) to the row with that id.
 * Ids are small non-negative integers (also in the scaled sets), so a flat
 * array indexed by the id is the simplest hash table: one memory access. */
typedef struct {
    long *row;     /* row[id] = row index, -1 = no such id */
    long size;     /* max id + 1 */
} IdIndex;

static IdIndex id_index(const CsvTable *t, const char *column)
{
    size_t col = csv_col(t, column);
    IdIndex ix = {NULL, 0};
    for (size_t r = 0; r < t->nrows; r++) {
        long id = csv_long(t, r, col, -1);
        if (id + 1 > ix.size)
            ix.size = id + 1;
    }
    ix.row = xmalloc((size_t)ix.size * sizeof *ix.row);
    for (long i = 0; i < ix.size; i++)
        ix.row[i] = -1;
    for (size_t r = 0; r < t->nrows; r++) {
        long id = csv_long(t, r, col, -1);
        if (id < 0 || ix.row[id] != -1) {
            fprintf(stderr, "%s: missing or duplicate id %ld\n", column, id);
            exit(1);
        }
        ix.row[id] = (long)r;
    }
    return ix;
}

static long id_lookup(const IdIndex *ix, long id)
{
    return (id >= 0 && id < ix->size) ? ix->row[id] : -1;
}

/* "2009-03-29" -> 20090329: sorts like the date, no time library needed */
static int parse_date(const char *s)
{
    int y, m, d;
    if (!s || sscanf(s, "%d-%d-%d", &y, &m, &d) != 3) {
        fprintf(stderr, "bad date '%s'\n", s ? s : "(missing)");
        exit(1);
    }
    return y * 10000 + m * 100 + d;
}

/* ---------------------------------------------------------------------------
 * 4.9: results_clean, only the part that 6.4 and Q1 need
 * ------------------------------------------------------------------------- */

/* One row of results_clean, joined with its race (circuit, year, date) */
typedef struct {
    long resultId, raceId, driverId, constructorId, circuitId;
    int year, round, date;     /* date as yyyymmdd */
    int grid, positionOrder, podium;
} Result;

typedef struct {
    Result *rows;
    size_t n;
} Results;

/* 4.8: the status names that mean the car never started the race */
static const char *DID_NOT_START[] = {"Did not qualify", "Did not prequalify", "Withdrew"};

#define MISSING LONG_MIN

/* Sort key for the duplicate resolution: (raceId, driverId, file order) */
typedef struct {
    long raceId, driverId;
    size_t row;
} DupKey;

static int cmp_dupkey(const void *pa, const void *pb)
{
    const DupKey *a = pa, *b = pb;
    if (a->raceId != b->raceId)
        return a->raceId < b->raceId ? -1 : 1;
    if (a->driverId != b->driverId)
        return a->driverId < b->driverId ? -1 : 1;
    return a->row < b->row ? -1 : a->row > b->row;
}

static Results clean_results(const Tables *tables, const PairMap *quali_by_pair)
{
    const CsvTable *res = get_table(tables, "results");
    const CsvTable *races = get_table(tables, "races");
    const CsvTable *status = get_table(tables, "status");
    const CsvTable *quali = get_table(tables, "qualifying");
    size_t n = res->nrows;

    size_t c_result = csv_col(res, "resultId"), c_race = csv_col(res, "raceId"),
           c_driver = csv_col(res, "driverId"), c_cons = csv_col(res, "constructorId"),
           c_number = csv_col(res, "number"), c_grid = csv_col(res, "grid"),
           c_order = csv_col(res, "positionOrder"), c_status = csv_col(res, "statusId");
    size_t q_number = csv_col(quali, "number"), q_cons = csv_col(quali, "constructorId");

    /* 4.9.4 One row per driver per race. A driver can have several rows in a
     * race (shared drives in the 1950s). Keep the row whose car number and
     * constructor match the driver's qualifying entry, if exactly one row
     * matches; otherwise the first row in the file. The race result itself is
     * never used, so the choice does not leak the target. */
    DupKey *keys = xmalloc(n * sizeof *keys);
    for (size_t r = 0; r < n; r++)
        keys[r] = (DupKey){csv_long(res, r, c_race, -1), csv_long(res, r, c_driver, -1), r};
    qsort(keys, n, sizeof *keys, cmp_dupkey);

    size_t *chosen = xmalloc(n * sizeof *chosen);
    size_t n_chosen = 0, n_single = 0, n_matched = 0, n_fallback = 0;
    for (size_t a = 0; a < n;) {
        size_t b = a;
        while (b < n && keys[b].raceId == keys[a].raceId && keys[b].driverId == keys[a].driverId)
            b++;

        size_t pick = keys[a].row;   /* default: earliest row in the file */
        if (b - a == 1) {
            n_single++;
        } else {
            long q = pairmap_get(quali_by_pair, keys[a].raceId, keys[a].driverId);
            size_t matches = 0, match_row = 0;
            for (size_t k = a; q >= 0 && k < b; k++) {
                long number = csv_long(res, keys[k].row, c_number, MISSING);
                long qnumber = csv_long(quali, (size_t)q, q_number, MISSING);
                /* a missing number never matches, like NaN == NaN in pandas */
                if (number != MISSING && number == qnumber &&
                    csv_long(res, keys[k].row, c_cons, -1) == csv_long(quali, (size_t)q, q_cons, -2)) {
                    if (matches++ == 0)
                        match_row = keys[k].row;
                }
            }
            if (matches == 1) {
                pick = match_row;
                n_matched++;
            } else {
                n_fallback++;
            }
        }
        chosen[n_chosen++] = pick;
        a = b;
    }
    free(keys);
    size_t after_dedup = n_chosen;

    /* 4.9.7 Remove non-starters and the Indianapolis 500 (1950-1960, a
     * separate race with its own field) */
    IdIndex status_ix = id_index(status, "statusId");
    IdIndex race_ix = id_index(races, "raceId");
    size_t s_name = csv_col(status, "status");
    size_t r_name = csv_col(races, "name"), r_circuit = csv_col(races, "circuitId"),
           r_year = csv_col(races, "year"), r_round = csv_col(races, "round"),
           r_date = csv_col(races, "date");

    Results out = {xmalloc(n_chosen * sizeof(Result)), 0};
    size_t after_starters = 0;
    for (size_t i = 0; i < n_chosen; i++) {
        size_t r = chosen[i];
        long sr = id_lookup(&status_ix, csv_long(res, r, c_status, -1));
        if (sr < 0) {
            fprintf(stderr, "results row %zu: unknown statusId\n", r + 1);
            exit(1);
        }
        const char *status_name = csv_get(status, (size_t)sr, s_name);
        int non_starter = 0;
        for (size_t k = 0; k < sizeof DID_NOT_START / sizeof *DID_NOT_START; k++)
            non_starter |= status_name && strcmp(status_name, DID_NOT_START[k]) == 0;
        if (non_starter)
            continue;
        after_starters++;

        long raceId = csv_long(res, r, c_race, -1);
        long rr = id_lookup(&race_ix, raceId);
        if (rr < 0) {
            fprintf(stderr, "results row %zu: unknown raceId %ld\n", r + 1, raceId);
            exit(1);
        }
        const char *race_name = csv_get(races, (size_t)rr, r_name);
        if (race_name && strcmp(race_name, "Indianapolis 500") == 0)
            continue;

        /* 4.9.11 Target: a podium is a finishing position of 1-3 */
        int position_order = (int)csv_long(res, r, c_order, -1);
        out.rows[out.n++] = (Result){
            .resultId = csv_long(res, r, c_result, -1),
            .raceId = raceId,
            .driverId = csv_long(res, r, c_driver, -1),
            .constructorId = csv_long(res, r, c_cons, -1),
            .circuitId = csv_long(races, (size_t)rr, r_circuit, -1),
            .year = (int)csv_long(races, (size_t)rr, r_year, -1),
            .round = (int)csv_long(races, (size_t)rr, r_round, -1),
            .date = parse_date(csv_get(races, (size_t)rr, r_date)),
            .grid = (int)csv_long(res, r, c_grid, -1),
            .positionOrder = position_order,
            .podium = position_order >= 1 && position_order <= 3,
        };
    }
    free(chosen);
    free(status_ix.row);
    free(race_ix.row);

    printf("results: raw results                  %8zu rows\n", n);
    printf("results: after duplicate resolution   %8zu rows  (%zu single, %zu qualifying match, %zu fallback)\n",
           after_dedup, n_single, n_matched, n_fallback);
    printf("results: after removing non-starters  %8zu rows\n", after_starters);
    printf("results: after excluding Indy 500     %8zu rows\n", out.n);
    return out;
}

/* Qualifying lookup by (raceId, driverId), shared by 4.9.4 and Q1 */
static PairMap index_qualifying(const Tables *tables)
{
    const CsvTable *q = get_table(tables, "qualifying");
    size_t c_race = csv_col(q, "raceId"), c_driver = csv_col(q, "driverId");
    PairMap m = pairmap_new(q->nrows);
    for (size_t r = 0; r < q->nrows; r++)
        if (pairmap_put(&m, csv_long(q, r, c_race, -1), csv_long(q, r, c_driver, -1), (long)r)) {
            fprintf(stderr, "qualifying: duplicate (raceId, driverId) in row %zu\n", r + 1);
            exit(1);
        }
    return m;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s DATA_DIR\n", argv[0]);
        return 2;
    }
    Tables tables;
    double t_parse = load_tables(argv[1], &tables);

    double start = now();
    PairMap quali_by_pair = index_qualifying(&tables);
    Results results = clean_results(&tables, &quali_by_pair);
    double t_clean = now() - start;

    printf("\n%-22s %8.4f s\n", "parse (Section 2)", t_parse);
    printf("%-22s %8.4f s\n", "clean (4.9)", t_clean);

    free(results.rows);
    pairmap_free(&quali_by_pair);
    for (size_t i = 0; i < N_TABLES; i++)
        csv_free(&tables.t[i]);
    return 0;
}
