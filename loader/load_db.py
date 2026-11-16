"""Fill the Postgres database from the 14 F1 CSVs with COPY and time each table.

Runs inside the `loader` container (see docker-compose.yml). It reaches the
database by its service name `db`, not localhost: each container has its own
localhost.

Steps:
  1. run sql/schema.sql, which drops and recreates the 14 tables (empty database)
  2. COPY each CSV into its table, parents first so foreign keys are satisfied;
     the literal text \\N becomes NULL (notebook Section 2.1)
  3. compare the row count of each table with its CSV (not timed)

Everything runs in one transaction: if any step fails, nothing is kept.

Settings (environment variables):
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD   from .env
    PGHOST    database host (default: db)
    DATA_DIR  folder holding the CSVs (default: /data)
    SCHEMA    schema file (default: /sql/schema.sql)
"""

import csv
import os
import sys
import time
from pathlib import Path

import psycopg

# Load order matters: a row can only reference rows that already exist.
LOAD_ORDER = [
    # parent tables (no foreign keys)
    "seasons",
    "circuits",
    "status",
    "drivers",
    "constructors",
    # depends on seasons and circuits
    "races",
    # depend on races, drivers, constructors, status
    "results",
    "sprint_results",
    "qualifying",
    "constructor_results",
    "constructor_standings",
    "driver_standings",
    "lap_times",
    "pit_stops",
]

# FORMAT csv handles the quoted fields; HEADER skips the first line;
# NULL '\N' turns the dataset's missing-value marker into a real NULL.
COPY_SQL = r"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER, NULL '\N')"

CHUNK_BYTES = 1 << 20  # send the file to the server 1 MB at a time


def csv_rows(path):
    """Number of data rows in a CSV (header excluded).

    csv.reader is used instead of counting lines so that a quoted field
    containing a newline would still count as one row.
    """
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def copy_table(cur, table, path):
    """Stream one CSV into its table and return the seconds it took."""
    start = time.perf_counter()
    with open(path, "rb") as f, cur.copy(COPY_SQL.format(table=table)) as copy:
        while chunk := f.read(CHUNK_BYTES):
            copy.write(chunk)
    return time.perf_counter() - start


def main():
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    schema = Path(os.environ.get("SCHEMA", "/sql/schema.sql"))

    missing = [t for t in LOAD_ORDER if not (data_dir / f"{t}.csv").is_file()]
    if missing:
        sys.exit(f"Missing CSV files in {data_dir}: {', '.join(missing)}")
    if not schema.is_file():
        sys.exit(f"Schema file not found: {schema}")

    # Host from PGHOST (set in docker-compose.yml); database and login from .env.
    conninfo = {
        "host": os.environ.get("PGHOST", "db"),
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }

    print(f"Loading {len(LOAD_ORDER)} tables from {data_dir} into "
          f"{conninfo['dbname']} on {conninfo['host']}\n")

    timings = {}
    with psycopg.connect(**conninfo) as conn:  # commits on success, rolls back on error
        with conn.cursor() as cur:
            cur.execute(schema.read_text())

            total_start = time.perf_counter()
            for table in LOAD_ORDER:
                timings[table] = copy_table(cur, table, data_dir / f"{table}.csv")
            total = time.perf_counter() - total_start

            # Check: rows in the database == rows in the CSV.
            print(f"{'table':<22} {'db rows':>9} {'csv rows':>9}  {'ok':<3} {'seconds':>8}")
            print("-" * 56)
            bad = []
            for table in LOAD_ORDER:
                cur.execute(f"SELECT count(*) FROM {table}")
                db_rows = cur.fetchone()[0]
                file_rows = csv_rows(data_dir / f"{table}.csv")
                ok = db_rows == file_rows
                if not ok:
                    bad.append(table)
                print(f"{table:<22} {db_rows:>9,} {file_rows:>9,}  "
                      f"{'ok' if ok else 'NO':<3} {timings[table]:8.4f}")

            print("-" * 56)
            print(f"{'sum of table loads':<46} {sum(timings.values()):8.4f} s")
            print(f"{'total (wall clock)':<46} {total:8.4f} s")

            # Exiting inside the `with` rolls the load back: no half-checked data is kept.
            if bad:
                sys.exit(f"Row counts differ for: {', '.join(bad)}")


if __name__ == "__main__":
    main()
