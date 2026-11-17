/* Minimal CSV reader for the F1 tables: no external libraries.
 *
 * The whole file is read into one buffer and parsed in place: every field
 * becomes a NUL-terminated string inside that buffer, so a table costs one
 * read, one buffer and one array of pointers (no per-field malloc).
 *
 * Handles what the F1 CSVs contain:
 *   - a header row, comma-separated fields, \n or \r\n line ends
 *   - quoted fields ("Monaco"), with "" as an escaped quote inside them
 *   - the missing-value sentinel \N and empty fields ("" or nothing),
 *     both stored as NULL, like pandas' read_csv(na_values=["\\N"])
 */
#ifndef F1_CSV_H
#define F1_CSV_H

#include <stddef.h>

typedef struct {
    char *buffer;      /* file contents; every field points into it */
    char **header;     /* ncols column names */
    char **cells;      /* nrows * ncols fields, row-major; NULL = missing */
    size_t nrows;
    size_t ncols;
} CsvTable;

/* Read and parse path. Exits with a message on any error (missing file,
 * a row with the wrong number of fields, an unterminated quote). */
CsvTable csv_read(const char *path);

void csv_free(CsvTable *t);

/* Index of column name; exits if the table has no such column. */
size_t csv_col(const CsvTable *t, const char *name);

/* Field (row, col), NULL when missing. */
static inline const char *csv_get(const CsvTable *t, size_t row, size_t col)
{
    return t->cells[row * t->ncols + col];
}

/* Field as an integer / a double; `missing` when the field is NULL.
 * Exits if the field is not a number. */
long csv_long(const CsvTable *t, size_t row, size_t col, long missing);
double csv_double(const CsvTable *t, size_t row, size_t col, double missing);

#endif
