/* F1 port in plain C: notebook Section 2 (load), the cleaning that 6.4
 * needs, 6.4 (driver/constructor form) and 5.1 (Q1).
 *
 * Usage:
 *   make -C impl/c
 *   impl/c/build/f1 data/
 *   impl/c/build/f1 data/ --out bench/outputs/c.csv   # for bench/compare_outputs.py
 *   impl/c/build/f1 data/ --q1-out bench/outputs/c_q1.csv
 */
#include <limits.h>
#include <math.h>
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

/* ---------------------------------------------------------------------------
 * 6.4: driver and constructor form, from past races only
 * ------------------------------------------------------------------------- */

/* "Last 5 races", as FORM_WINDOW in impl/python/features.py */
#define FORM_WINDOW 5

/* The 6.4 features of one results row. NAN = no past race (empty in the CSV). */
typedef struct {
    double drv_prev_races;
    double drv_podium_rate_last5;
    double drv_avg_finish_last5;
    double drv_career_podium_rate;
    double drv_circuit_podium_rate;
    double cons_podiums_last5;
    double cons_avg_finish_last5;
} Form;

/* Mean of the last (up to) FORM_WINDOW values of one group, kept as a
 * sliding sum: each new value is added and the value that leaves the
 * window is subtracted, so every step is O(1) instead of re-adding 5
 * values. This is pandas' shift(1).rolling(5, min_periods=1).mean(): the
 * window holds only past values, and the mean is NAN before the first one. */
typedef struct {
    double values[FORM_WINDOW];
    double sum;
    int count;     /* values in the window, at most FORM_WINDOW */
    int next;      /* ring-buffer slot the next value goes into */
} Window;

static double window_mean(const Window *w)
{
    return w->count ? w->sum / w->count : NAN;
}

static void window_push(Window *w, double v)
{
    if (w->count == FORM_WINDOW)
        w->sum -= w->values[w->next];   /* the oldest value leaves */
    else
        w->count++;
    w->values[w->next] = v;
    w->sum += v;
    w->next = (w->next + 1) % FORM_WINDOW;
}

/* Sort row indices by (group id, date, raceId): the order in which a
 * group's races happened. `group` is the driverId or constructorId. */
typedef struct {
    long group, raceId;
    int date;
    size_t row;
} TimeKey;

static int cmp_timekey(const void *pa, const void *pb)
{
    const TimeKey *a = pa, *b = pb;
    if (a->group != b->group)
        return a->group < b->group ? -1 : 1;
    if (a->date != b->date)
        return a->date < b->date ? -1 : 1;
    if (a->raceId != b->raceId)
        return a->raceId < b->raceId ? -1 : 1;
    return a->row < b->row ? -1 : a->row > b->row;
}

/* Driver form: one pass over the rows sorted by (driverId, date). */
static void driver_form(const Results *res, Form *form)
{
    size_t n = res->n;
    TimeKey *keys = xmalloc(n * sizeof *keys);
    for (size_t i = 0; i < n; i++)
        keys[i] = (TimeKey){res->rows[i].driverId, res->rows[i].raceId, res->rows[i].date, i};
    qsort(keys, n, sizeof *keys, cmp_timekey);

    /* (driverId, circuitId) -> slot in circuit_podiums / circuit_races:
     * a driver's record at one circuit, over the whole career */
    PairMap circuit_slot = pairmap_new(n);
    long *circuit_podiums = xmalloc(n * sizeof *circuit_podiums);
    long *circuit_races = xmalloc(n * sizeof *circuit_races);
    size_t n_slots = 0;

    Window podiums = {0}, finishes = {0};
    long career_races = 0, career_podiums = 0;
    for (size_t k = 0; k < n; k++) {
        const Result *r = &res->rows[keys[k].row];
        Form *f = &form[keys[k].row];
        if (k == 0 || keys[k].group != keys[k - 1].group) {
            /* a new driver: start from no history */
            podiums = (Window){0};
            finishes = (Window){0};
            career_races = career_podiums = 0;
        }

        /* features first, from the races before this one ... */
        f->drv_prev_races = (double)career_races;
        f->drv_podium_rate_last5 = window_mean(&podiums);
        f->drv_avg_finish_last5 = window_mean(&finishes);
        f->drv_career_podium_rate =
            career_races ? (double)career_podiums / (double)career_races : NAN;

        long slot = pairmap_get(&circuit_slot, r->driverId, r->circuitId);
        if (slot < 0) {
            slot = (long)n_slots++;
            pairmap_put(&circuit_slot, r->driverId, r->circuitId, slot);
            circuit_podiums[slot] = circuit_races[slot] = 0;
        }
        f->drv_circuit_podium_rate = circuit_races[slot]
            ? (double)circuit_podiums[slot] / (double)circuit_races[slot] : NAN;

        /* ... then add this race to the history (the shift(1) of pandas) */
        window_push(&podiums, r->podium);
        window_push(&finishes, r->positionOrder);
        career_races++;
        career_podiums += r->podium;
        circuit_races[slot]++;
        circuit_podiums[slot] += r->podium;
    }
    free(keys);
    free(circuit_podiums);
    free(circuit_races);
    pairmap_free(&circuit_slot);
}

/* Constructor form: first one entry per team per race (podiums of both
 * cars, mean finish of the team's cars), then the same sliding window over
 * each team's races. Every car of the team gets its team's value. */
static void constructor_form(const Results *res, Form *form)
{
    size_t n = res->n;
    TimeKey *keys = xmalloc(n * sizeof *keys);
    for (size_t i = 0; i < n; i++)
        keys[i] = (TimeKey){res->rows[i].constructorId, res->rows[i].raceId, res->rows[i].date, i};
    qsort(keys, n, sizeof *keys, cmp_timekey);

    Window podiums = {0}, finishes = {0};
    for (size_t a = 0; a < n;) {
        /* rows a..b-1: one team in one race */
        size_t b = a;
        long podium_sum = 0, finish_sum = 0;
        while (b < n && keys[b].group == keys[a].group && keys[b].raceId == keys[a].raceId) {
            podium_sum += res->rows[keys[b].row].podium;
            finish_sum += res->rows[keys[b].row].positionOrder;
            b++;
        }
        if (a == 0 || keys[a].group != keys[a - 1].group) {
            podiums = (Window){0};
            finishes = (Window){0};
        }

        double podiums_last5 = window_mean(&podiums);
        double finish_last5 = window_mean(&finishes);
        for (size_t k = a; k < b; k++) {
            form[keys[k].row].cons_podiums_last5 = podiums_last5;
            form[keys[k].row].cons_avg_finish_last5 = finish_last5;
        }

        window_push(&podiums, (double)podium_sum);
        window_push(&finishes, (double)finish_sum / (double)(b - a));
        a = b;
    }
    free(keys);
}

/* ---------------------------------------------------------------------------
 * 5.1 Q1: which circuits convert a front-row start into a podium most
 * often, and did that change after 2014?
 * ------------------------------------------------------------------------- */

#define Q1_FIRST_YEAR 1994   /* qualifying data starts in 1994 */
#define Q1_HYBRID_YEAR 2014
#define MIN_STARTS_Q1 10     /* front-row starts per circuit in EACH era */

static const char *ERAS[2] = {"1994–2013", "2014–2024"};
static const char *FRONT_ROW_BY[2] = {"grid", "qualifying"};

/* Starts and podiums of front-row starters, per definition, circuit and era.
 * Circuits are few (77), so a dense array indexed by the circuit's row in
 * circuits.csv is enough for the GROUP BY. */
typedef struct {
    size_t n_circuits;
    const char **name;        /* circuit name per circuit row */
    long *starts, *podiums;   /* [definition][circuit][era], flattened */
} Q1;

static size_t q1_at(const Q1 *q, int def, size_t circuit, int era)
{
    return ((size_t)def * q->n_circuits + circuit) * 2 + (size_t)era;
}

static int q1_kept(const Q1 *q, int def, size_t c)
{
    return q->starts[q1_at(q, def, c, 0)] >= MIN_STARTS_Q1 &&
           q->starts[q1_at(q, def, c, 1)] >= MIN_STARTS_Q1;
}

static double q1_rate(const Q1 *q, int def, size_t c, int era)
{
    size_t i = q1_at(q, def, c, era);
    /* same operation order as pandas: podiums / starts * 100 */
    return (double)q->podiums[i] / (double)q->starts[i] * 100;
}

static Q1 q1(const Tables *tables, const Results *res, const PairMap *quali_by_pair)
{
    const CsvTable *circuits = get_table(tables, "circuits");
    const CsvTable *quali = get_table(tables, "qualifying");
    IdIndex circuit_ix = id_index(circuits, "circuitId");
    size_t c_name = csv_col(circuits, "name");
    size_t q_position = csv_col(quali, "position");

    Q1 q = {circuits->nrows, xmalloc(circuits->nrows * sizeof(char *)), NULL, NULL};
    for (size_t c = 0; c < q.n_circuits; c++)
        q.name[c] = csv_get(circuits, c, c_name);
    size_t cells = 2 * q.n_circuits * 2;
    q.starts = calloc(cells, sizeof *q.starts);
    q.podiums = calloc(cells, sizeof *q.podiums);

    for (size_t i = 0; i < res->n; i++) {
        const Result *r = &res->rows[i];
        if (r->year < Q1_FIRST_YEAR)
            continue;
        int era = r->year >= Q1_HYBRID_YEAR;
        long c = id_lookup(&circuit_ix, r->circuitId);

        /* Hash join with qualifying on (raceId, driverId): probe the map
         * built once from qualifying. LEFT JOIN: no qualifying row means
         * no qualifying position, so not on the front row by qualifying. */
        long qrow = pairmap_get(quali_by_pair, r->raceId, r->driverId);
        long quali_position = qrow >= 0 ? csv_long(quali, (size_t)qrow, q_position, -1) : -1;

        int front[2] = {r->grid == 1 || r->grid == 2,
                        quali_position == 1 || quali_position == 2};
        for (int def = 0; def < 2; def++)
            if (front[def]) {
                q.starts[q1_at(&q, def, (size_t)c, era)]++;
                q.podiums[q1_at(&q, def, (size_t)c, era)] += r->podium;
            }
    }
    free(circuit_ix.row);
    return q;
}

/* Average ranks (ties share the mean of their ranks, like pandas'
 * rank(method="average")): rank[i] for values[i], 1 = smallest. */
static void average_ranks(const double *values, double *rank, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        size_t below = 0, equal = 0;
        for (size_t j = 0; j < n; j++) {
            below += values[j] < values[i];
            equal += values[j] == values[i];
        }
        /* the tied block takes ranks below+1 .. below+equal */
        rank[i] = (double)below + (double)(equal + 1) / 2.0;
    }
}

/* Spearman correlation of the circuits' rates between the two eras
 * (1 = same ranking): the Pearson correlation of the average ranks. */
static double q1_rank_correlation(const Q1 *q, int def, size_t *n_kept)
{
    double *before = xmalloc(q->n_circuits * sizeof *before);
    double *after = xmalloc(q->n_circuits * sizeof *after);
    size_t n = 0;
    for (size_t c = 0; c < q->n_circuits; c++)
        if (q1_kept(q, def, c)) {
            before[n] = q1_rate(q, def, c, 0);
            after[n] = q1_rate(q, def, c, 1);
            n++;
        }
    double *rb = xmalloc((n ? n : 1) * sizeof *rb), *ra = xmalloc((n ? n : 1) * sizeof *ra);
    average_ranks(before, rb, n);
    average_ranks(after, ra, n);

    double mb = 0, ma = 0;
    for (size_t i = 0; i < n; i++) {
        mb += rb[i];
        ma += ra[i];
    }
    mb /= (double)n;
    ma /= (double)n;
    double sab = 0, saa = 0, sbb = 0;
    for (size_t i = 0; i < n; i++) {
        sab += (rb[i] - mb) * (ra[i] - ma);
        sbb += (rb[i] - mb) * (rb[i] - mb);
        saa += (ra[i] - ma) * (ra[i] - ma);
    }
    free(before);
    free(after);
    free(rb);
    free(ra);
    *n_kept = n;
    return sab / sqrt(sbb * saa);
}

/* Q1 in the long format of impl/python/questions.py --out (q1.csv):
 * front_row_by, circuit, era, starts, podiums, rate; sorted by
 * (front_row_by, circuit, era). Rates with 6 decimals. */
static const Q1 *g_sort_q1;

static int cmp_circuit_name(const void *pa, const void *pb)
{
    /* strcmp on UTF-8 bytes sorts like Python's str comparison */
    return strcmp(g_sort_q1->name[*(const size_t *)pa], g_sort_q1->name[*(const size_t *)pb]);
}

static void write_q1(const char *path, const Q1 *q)
{
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "%s: cannot write\n", path);
        exit(1);
    }
    size_t *order = xmalloc(q->n_circuits * sizeof *order);
    for (size_t c = 0; c < q->n_circuits; c++)
        order[c] = c;
    g_sort_q1 = q;
    qsort(order, q->n_circuits, sizeof *order, cmp_circuit_name);

    fputs("front_row_by,circuit,era,starts,podiums,rate\n", f);
    for (int def = 0; def < 2; def++)
        for (size_t k = 0; k < q->n_circuits; k++) {
            size_t c = order[k];
            if (!q1_kept(q, def, c))
                continue;
            for (int era = 0; era < 2; era++) {
                size_t i = q1_at(q, def, c, era);
                /* circuit names contain no comma or quote, so no CSV quoting */
                fprintf(f, "%s,%s,%s,%ld,%ld,%.6f\n", FRONT_ROW_BY[def], q->name[c],
                        ERAS[era], q->starts[i], q->podiums[i], q1_rate(q, def, c, era));
            }
        }
    free(order);
    fclose(f);
}

/* ---------------------------------------------------------------------------
 * Output in the shared format (README, "Output equivalence check")
 * ------------------------------------------------------------------------- */

/* Sort by (raceId, driverId) for the output */
static const Results *g_sort_results;

static int cmp_output(const void *pa, const void *pb)
{
    const Result *a = &g_sort_results->rows[*(const size_t *)pa];
    const Result *b = &g_sort_results->rows[*(const size_t *)pb];
    if (a->raceId != b->raceId)
        return a->raceId < b->raceId ? -1 : 1;
    return (a->driverId > b->driverId) - (a->driverId < b->driverId);
}

static void write_value(FILE *f, double v)
{
    if (isnan(v))
        fputs(",", f);            /* NULL / NaN: an empty field */
    else
        fprintf(f, ",%.6f", v);
}

static void write_output(const char *path, const Results *res, const Form *form)
{
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "%s: cannot write\n", path);
        exit(1);
    }
    size_t *order = xmalloc(res->n * sizeof *order);
    for (size_t i = 0; i < res->n; i++)
        order[i] = i;
    g_sort_results = res;
    qsort(order, res->n, sizeof *order, cmp_output);

    /* the 6.4 columns, in the canonical order of bench/compare_outputs.py */
    fputs("raceId,driverId,drv_prev_races,drv_podium_rate_last5,drv_avg_finish_last5,"
          "drv_career_podium_rate,drv_circuit_podium_rate,cons_podiums_last5,"
          "cons_avg_finish_last5\n", f);
    for (size_t k = 0; k < res->n; k++) {
        const Result *r = &res->rows[order[k]];
        const Form *x = &form[order[k]];
        fprintf(f, "%ld,%ld", r->raceId, r->driverId);
        write_value(f, x->drv_prev_races);
        write_value(f, x->drv_podium_rate_last5);
        write_value(f, x->drv_avg_finish_last5);
        write_value(f, x->drv_career_podium_rate);
        write_value(f, x->drv_circuit_podium_rate);
        write_value(f, x->cons_podiums_last5);
        write_value(f, x->cons_avg_finish_last5);
        fputc('\n', f);
    }
    free(order);
    fclose(f);
}

int main(int argc, char **argv)
{
    const char *data_dir = NULL, *out_path = NULL, *q1_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--out") == 0 && i + 1 < argc)
            out_path = argv[++i];
        else if (strcmp(argv[i], "--q1-out") == 0 && i + 1 < argc)
            q1_path = argv[++i];
        else if (!data_dir && argv[i][0] != '-')
            data_dir = argv[i];
        else
            data_dir = NULL, i = argc;   /* unknown option: print usage */
    }
    if (!data_dir) {
        fprintf(stderr, "usage: %s DATA_DIR [--out CSV] [--q1-out CSV]\n", argv[0]);
        return 2;
    }
    Tables tables;
    double t_parse = load_tables(data_dir, &tables);

    double start = now();
    PairMap quali_by_pair = index_qualifying(&tables);
    Results results = clean_results(&tables, &quali_by_pair);
    double t_clean = now() - start;

    start = now();
    Form *form = xmalloc(results.n * sizeof *form);
    driver_form(&results, form);
    constructor_form(&results, form);
    double t_form = now() - start;

    size_t with_driver_form = 0, with_team_form = 0;
    for (size_t i = 0; i < results.n; i++) {
        with_driver_form += !isnan(form[i].drv_podium_rate_last5);
        with_team_form += !isnan(form[i].cons_podiums_last5);
    }
    printf("6.4 rows with driver form %8zu\n", with_driver_form);
    printf("6.4 rows with team form   %8zu\n", with_team_form);

    start = now();
    Q1 answer = q1(&tables, &results, &quali_by_pair);
    double t_q1 = now() - start;

    for (int def = 0; def < 2; def++) {
        size_t n_kept;
        double corr = q1_rank_correlation(&answer, def, &n_kept);
        printf("Q1 by %-10s %2zu circuits, rank correlation between eras %+.2f\n",
               FRONT_ROW_BY[def], n_kept, corr);
    }

    printf("\n%-22s %8.4f s\n", "parse (Section 2)", t_parse);
    printf("%-22s %8.4f s\n", "clean (4.9)", t_clean);
    printf("%-22s %8.4f s\n", "form (6.4)", t_form);
    printf("%-22s %8.4f s\n", "Q1 (5.1)", t_q1);
    printf("%-22s %8.4f s\n", "compute total", t_clean + t_form + t_q1);

    if (out_path) {
        write_output(out_path, &results, form);
        printf("\nWrote %s (output format for bench/compare_outputs.py)\n", out_path);
    }
    if (q1_path) {
        write_q1(q1_path, &answer);
        printf("Wrote %s (same layout as q1.csv from impl/python/questions.py --out)\n", q1_path);
    }

    free(answer.name);
    free(answer.starts);
    free(answer.podiums);
    free(form);
    free(results.rows);
    pairmap_free(&quali_by_pair);
    for (size_t i = 0; i < N_TABLES; i++)
        csv_free(&tables.t[i]);
    return 0;
}
