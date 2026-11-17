#include "csv.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void die(const char *path, size_t line, const char *msg)
{
    fprintf(stderr, "%s:%zu: %s\n", path, line, msg);
    exit(1);
}

static char *read_file(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        exit(1);
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0)
        die(path, 0, "cannot get the file size");

    char *buf = malloc((size_t)n + 1);
    if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n)
        die(path, 0, "cannot read the file");
    fclose(f);
    buf[n] = '\0';
    *size = (size_t)n;
    return buf;
}

/* Parse one field starting at *p. The field is NUL-terminated in place and
 * *p is left on the character after the delimiter. *end_of_row is set when
 * the field was the last one of its row (or of the file). Returns the field,
 * or NULL for \N and empty fields. */
static char *parse_field(char **p, int *end_of_row, const char *path, size_t line)
{
    char *s = *p;
    char *field = s;
    char *w;           /* where the (unescaped) field ends */

    if (*s == '"') {
        /* Quoted: copy over the "" escapes. The unescaped text is never
         * longer than the raw text, so writing behind the reader is safe. */
        s++;
        field = w = s;
        for (;;) {
            if (*s == '\0')
                die(path, line, "unterminated quoted field");
            if (*s == '"') {
                if (s[1] == '"') {
                    *w++ = '"';
                    s += 2;
                    continue;
                }
                s++;   /* closing quote */
                break;
            }
            *w++ = *s++;
        }
    } else {
        while (*s != ',' && *s != '\n' && *s != '\r' && *s != '\0')
            s++;
        w = s;
    }

    /* After the field: a comma, a line end or the end of the file */
    if (*s == ',') {
        *end_of_row = 0;
        s++;
    } else if (*s == '\r' || *s == '\n' || *s == '\0') {
        *end_of_row = 1;
        if (*s == '\r')
            s++;
        if (*s == '\n')
            s++;
    } else {
        die(path, line, "text after a closing quote");
    }
    *w = '\0';     /* w <= the delimiter's position, so this never cuts the next field */
    *p = s;

    if (field[0] == '\0' || strcmp(field, "\\N") == 0)
        return NULL;
    return field;
}

CsvTable csv_read(const char *path)
{
    CsvTable t = {0};
    size_t size;
    t.buffer = read_file(path, &size);

    /* Upper bound for the row count: one row per line end, plus one */
    size_t lines = 1;
    for (size_t i = 0; i < size; i++)
        lines += t.buffer[i] == '\n';

    char *p = t.buffer;
    int end_of_row = 0;

    /* Header: count and keep the column names */
    size_t cap = 16;
    t.header = malloc(cap * sizeof *t.header);
    while (!end_of_row) {
        if (t.ncols == cap)
            t.header = realloc(t.header, (cap *= 2) * sizeof *t.header);
        char *name = parse_field(&p, &end_of_row, path, 1);
        if (!name)
            die(path, 1, "empty column name in the header");
        t.header[t.ncols++] = name;
    }

    t.cells = malloc(lines * t.ncols * sizeof *t.cells);
    if (!t.cells)
        die(path, 0, "out of memory");

    size_t line = 1;
    while (*p != '\0') {
        line++;
        char **row = t.cells + t.nrows * t.ncols;
        size_t col = 0;
        end_of_row = 0;
        while (!end_of_row) {
            if (col == t.ncols)
                die(path, line, "more fields than header columns");
            row[col++] = parse_field(&p, &end_of_row, path, line);
        }
        if (col != t.ncols)
            die(path, line, "fewer fields than header columns");
        t.nrows++;
    }
    return t;
}

void csv_free(CsvTable *t)
{
    free(t->buffer);
    free(t->header);
    free(t->cells);
    *t = (CsvTable){0};
}

size_t csv_col(const CsvTable *t, const char *name)
{
    for (size_t c = 0; c < t->ncols; c++)
        if (strcmp(t->header[c], name) == 0)
            return c;
    fprintf(stderr, "no column '%s'\n", name);
    exit(1);
}

long csv_long(const CsvTable *t, size_t row, size_t col, long missing)
{
    const char *s = csv_get(t, row, col);
    if (!s)
        return missing;
    char *end;
    long v = strtol(s, &end, 10);
    if (*end != '\0') {
        fprintf(stderr, "row %zu, column '%s': '%s' is not an integer\n",
                row + 1, t->header[col], s);
        exit(1);
    }
    return v;
}

double csv_double(const CsvTable *t, size_t row, size_t col, double missing)
{
    const char *s = csv_get(t, row, col);
    if (!s)
        return missing;
    char *end;
    double v = strtod(s, &end);
    if (*end != '\0') {
        fprintf(stderr, "row %zu, column '%s': '%s' is not a number\n",
                row + 1, t->header[col], s);
        exit(1);
    }
    return v;
}
