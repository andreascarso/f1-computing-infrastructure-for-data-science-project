# f1-computing-infrastructure-for-data-science-project

## Python environment (local development)

The pandas scripts, the notebook and the Streamlit app run in a local virtual
environment. The loader and benchmark containers get their own copy of the same
pinned versions (see `loader/requirements.txt` and `bench/Dockerfile`).

**Requirements:** Python 3.11 or newer (developed on 3.13).

**Set up (once):**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Every new terminal:** run `source .venv/bin/activate` again. The prompt shows
`(.venv)` when it is active. Leave the environment with `deactivate`.

**Use it in Jupyter / VS Code:** select the `.venv` interpreter (VS Code:
*Select Kernel → Python Environments → .venv*).

**Check that it works:**

```bash
python -c "import pandas, numpy, matplotlib, seaborn, psycopg, streamlit; print('ok')"
```

**Adding a library:** install it, then add it to `requirements.txt` with a pinned
version (`pip freeze | grep <name>`) in the same commit, so everyone stays in sync.
The `.venv/` folder is never committed.

## Database (Docker)

PostgreSQL 16 runs in a container defined in `docker-compose.yml`. Its data lives
in the named volume `dbdata`, so it survives restarts.

**Requirements:** Docker with Compose v2.

**Start (once per laptop):**

```bash
cp .env.example .env             # then edit .env and set your own password
docker compose up -d
docker compose ps                # wait until `db` shows (healthy)
```

`.env` holds the real password and is never committed; `.env.example` is the
template.

**Open a SQL prompt:**

```bash
docker compose exec db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB'
```

From your laptop the database is on `localhost:5432`; from other containers in
the compose file it is on host `db`.

**Load the data:**

```bash
docker compose run --rm --build loader
```

The `loader` container (`loader/`) runs `sql/schema.sql`, which drops and
recreates the 14 tables, then copies each CSV from `data/` with
`COPY ... WITH (FORMAT csv, HEADER, NULL '\N')`, so the dataset's `\N` becomes
NULL. It prints the seconds per table and in total, and checks every table's
row count against its CSV. Everything happens in one transaction: if any table
fails, nothing is kept.

It starts `db` if needed and waits until it is healthy. It is not part of
`docker compose up`, because each run resets the database. Run it again
whenever the CSVs change; `--build` is only needed after editing
`loader/load_db.py`.

To load a scaled dataset (see below) instead:

```bash
docker compose run --rm -e DATA_DIR=/data/scaled/x10 loader
```

**Clean the data in SQL** (after loading):

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/clean.sql
```

`sql/clean.sql` is notebook Section 4 in SQL, with the same decisions as
`impl/python/clean.py`. It builds new `<table>_clean` tables and leaves the raw
tables untouched. The checks the notebook makes with `assert` are `DO` blocks
that stop the script with an error, and at the end it prints each table's rows
and columns next to the notebook's (a warning, not an error, on the scaled
datasets). It runs in one transaction and can be re-run; loading again resets
the raw tables but not the `_clean` ones, so re-run it after every load.
Besides the `_clean` tables it creates `nationality_country`, the mapping from a
driver's nationality to the country names `circuits` uses ('British' → 'UK'),
which gives `drivers_clean` its `home_country` columns (notebook 4.11.2).

**Answer the questions in SQL** (after cleaning):

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/questions.sql
```

`sql/questions.sql` is notebook Section 5 in SQL, with the same logic as
`impl/python/questions.py`. Each question is a view (Q1: `q1_conversion`,
`q1_rank_correlation`; Q2: `q2_home_by_grid`, `q2_home_advantage`; Q3:
`q3_mechanical_rate`), so it can be queried again from psql. The script prints
the answer and compares the key numbers with the notebook, like `clean.sql`.
Re-running `clean.sql` drops the views, so run `questions.sql` again after it.

**Q1 with and without an index** (`sql/q1_index_timing.sql`, run after
`questions.sql`): it times `SELECT * FROM q1_conversion` (median of 20 runs)
without and with an index on `qualifying_clean (raceId, driverId)`, the one Q1
join key that has no index. The index is dropped again at the end.

| data | no index | with index |
|---|---|---|
| `data/` | 6.6 ms | 6.6 ms |
| `x10` | 103 ms | 103 ms |
| `x50` | 198 ms | 199 ms |

The planner never uses the index (`idx_scan` stays 0; the plan is the same).
Q1 reads every result row from 1994 on and joins about 85% of them to
qualifying, so one sequential scan and a hash join beat looking up each row in
an index. An index helps when a query needs only a few rows.

**Build the features in SQL** (after cleaning):

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/features.sql
```

`sql/features.sql` is notebook Section 6 in SQL, with the same logic as
`impl/python/features.py`. Each step is a table with one row per driver per
race: `features_grid` holds the base table with both splits (6.1), the grid
features (6.2) and the qualifying features (6.3); `features_form` the driver
and constructor form from past races only (6.4), with window functions whose
frame ends at `1 PRECEDING` (the SQL form of `shift(1)`); `features_standings`
the championship standings before the race (6.5), taken with `LAG` from the
previous race of the season in `driver_standings_clean` /
`constructor_standings_clean`. The script prints the notebook's numbers next to
its own, checks that no form or standings feature uses the race itself or a
later one, shows where the standings come from (6.5.1) and prints how long
`features_form` and `features_standings` take to build. The 6.6 context
features are not in SQL yet, so the SQL output has 18 of the 22 features.

Form features (6.4) on `data/`, median of 3:

| implementation | time |
|---|---|
| pandas A, `transform(lambda ...)` | 1,169 ms |
| pandas B, vectorised | 60 ms |
| SQL, window functions (`CREATE TABLE features_form`) | 83 ms |

The SQL time includes writing the table; the pandas times build columns in memory. The feature tables are copies, not views:
re-running `clean.sql` leaves them out of date, so run `features.sql` again
after it.

To compare with pandas, write both outputs and diff them (see
[Output equivalence check](#output-equivalence-check)):

```bash
docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/export_features.sql > bench/outputs/sql.csv
python impl/python/features.py data/ --out bench/outputs/pandas.csv
python bench/compare_outputs.py bench/outputs/pandas.csv bench/outputs/sql.csv
```

**Stop:**

```bash
docker compose down              # stops the container, keeps the data
docker compose down -v           # also deletes the data volume (fresh start)
```

## Scaled datasets

The original data (26.8k results rows) is so small that a run mostly measures
process startup. `bench/scale_data.py` writes smaller and larger copies, so we
can check whether time doubles when the data doubles.

```bash
python bench/scale_data.py data/                    # writes data/scaled/{25pct,50pct,x10,x50}/
python bench/scale_data.py data/ --sets 25pct x10   # only some sets
```

Any script that takes a data folder runs on a scaled set, e.g.
`python impl/python/features.py data/scaled/x10`. The 100% set is `data/` itself.

| set | how | rows (all tables) | size |
|---|---|---|---|
| `25pct` | every 4th season (1953, 1957, ... 2021): 18 seasons | 0.23 × | 5 MB |
| `50pct` | every 2nd season (1951, 1953, ... 2023): 37 seasons | 0.48 × | 11 MB |
| `data/` | original | 701k | 22 MB |
| `x10` | original + 9 copies | 10 × | 245 MB |
| `x50` | original + 49 copies | 50 × | 1.3 GB |

- **Smaller sets** keep whole seasons spread over 1950–2024, with only the
  drivers, constructors and circuits those seasons use. That way every table
  shrinks by about the same share and both Q1 eras stay in. The most recent
  seasons alone would be almost all lap times.
- **Larger sets** repeat the whole history. Copy *k* gets new ids for races,
  drivers, constructors and all row-level tables: `id + k × 10ⁿ`, with 10ⁿ just
  above the largest id (race 18 → 10018, 20018, ...; driver 1 → 1001, 2001, ...).
  `driverRef` / `constructorRef` get a `_k` suffix. Seasons, circuits and status
  are shared. Each copy is its own field of drivers and teams racing on the same
  dates at the same circuits. A copy's feature table is identical to the
  original's, the Q1–Q3 rates stay the same and every count grows k-fold.
  Exception: filters with a fixed minimum count (`MIN_STARTS_Q1 = 10`,
  `MIN_STARTS_Q3 = 30`) let more groups through at ×10, so the Q1 circuit list
  and its rank correlation change. At 25% too few circuits remain for the Q1
  correlation.
- Lines are copied byte for byte and only the leading id fields change. Quoting
  and the `\N` sentinel stay as in `data/`, so load times stay comparable.
- After writing, the script reads every set back: primary keys must be unique
  and every foreign key must exist in its parent table (exit code 1 otherwise).
- The notebook comparisons (`--check`) only hold for `data/`. On a scaled set
  the counts differ by design.

`data/scaled/` is generated (about 45 s for all four sets) and not committed.

## Output equivalence check

Before comparing run times, every implementation has to prove it builds the same
feature table (notebook Section 6). Each one writes its table in one fixed
format, and `bench/compare_outputs.py` diffs any two of these files.

**Output format** (the same for pandas, SQL, R, C and Rust):

- CSV with a header row, comma-separated, no index column, UTF-8
- Columns: `raceId`, `driverId`, then the feature columns in this order:
  `grid_filled, grid_zero_flag, field_size, quali_position_filled, has_qualifying,
  quali_gap_pct, drv_prev_races, drv_podium_rate_last5, drv_avg_finish_last5,
  drv_career_podium_rate, drv_circuit_podium_rate, cons_podiums_last5,
  cons_avg_finish_last5, drv_pre_points_share, drv_pre_wins, drv_pre_position,
  cons_pre_points_share, cons_pre_position, driver_age, home_race, round, hybrid_era`.
  An implementation that builds only some features (the ports, #25–#27) writes
  only those columns, in the same order.
- Rows sorted by `raceId`, then `driverId`, ascending; one row per pair
- `raceId` and `driverId` as integers. Every feature value with exactly 6
  decimals (`printf("%.6f")`), also 0/1 flags and counts: `1.000000`, `18.000000`
- NULL / NaN as an empty field

In SQL: `round(x::numeric, 6)` for decimals and `x::numeric(20,6)` for integers.
Quote the column aliases (`AS "raceId"`), because PostgreSQL lowercases unquoted
names. Export with
`\copy (SELECT ... ORDER BY "raceId", "driverId") TO 'file.csv' WITH (FORMAT csv, HEADER)`;
in CSV mode NULL is already written as an empty field.

**Write the pandas output and compare:**

```bash
python impl/python/features.py data/ --out bench/outputs/pandas.csv
python bench/compare_outputs.py bench/outputs/pandas.csv bench/outputs/sql.csv
```

Values match when both are empty or they differ by at most `--tol` (default
`1e-6`, one unit in the 6th decimal, since languages can round the last digit
differently). The script lists columns that are only in one file, rows that are
only in one file, values not written with 6 decimals, and the first differing
rows (`--max-rows N`). It exits with 0 on a match and 1 otherwise. Files in
`bench/outputs/` are generated and not committed.

## C port

`impl/c/` does notebook Section 2 (load), the results cleaning that 6.4 needs
(4.9), the driver/constructor form (6.4) and Q1 (5.1) in plain C11. It uses no
external libraries, only the C standard library and POSIX `clock_gettime`.

**Requirements:** a C compiler and `make` (macOS: `xcode-select --install`;
Linux: `gcc`, `make`).

**Build and run:**

```bash
make -C impl/c                                  # -O2, writes impl/c/build/f1
impl/c/build/f1 data/                           # prints row counts, Q1 and timings
impl/c/build/f1 data/ --out bench/outputs/c.csv --q1-out bench/outputs/c_q1.csv
python bench/compare_outputs.py bench/outputs/pandas.csv bench/outputs/c.csv
```

| file | what it does |
|---|---|
| `csv.c`, `csv.h` | CSV parser: reads a file into one buffer and splits the fields in place; quoted fields with `""` escapes; `\N` and empty fields become NULL |
| `pairmap.h` | hash map from an id pair, e.g. (raceId, driverId), to a row (open addressing) |
| `f1.c` | load, cleaning, form, Q1, output and timing |

**How it works:**

- **Cleaning (4.9):** sort the results by (raceId, driverId) and keep one row
  per pair (the unique qualifying match, else the first row in the file), then
  drop non-starters and the Indianapolis 500. Row counts are the notebook's:
  26759 → 26668 → 25067 → 24686.
- **Form (6.4):** sort the rows by (driverId, date) and walk through each
  driver's races once. The last 5 races sit in a ring buffer with a running
  sum: the new race is added, the oldest is subtracted. So each row costs O(1)
  and the whole pass is O(n) after the sort. The features of a race are read
  before the race is added, which is the `shift(1)` of pandas. Circuit history
  is kept per (driverId, circuitId) in the hash map. For the constructor form,
  the rows are first grouped into one entry per team per race.
- **Q1 (5.1):** a hash join. Qualifying is put into the hash map once, keyed by
  (raceId, driverId), and each results row from 1994 on looks up its
  qualifying position there (a LEFT JOIN: no entry means not on the front row).
  The starts and podiums are then counted per circuit and era.

**Checked:** `compare_outputs.py` gives MATCH against pandas, also with
`--tol 0`, and against SQL, on the 7 form columns (24,686 rows); also MATCH
against pandas on `x10`. The `--q1-out` table equals `q1.csv` from
`impl/python/questions.py --out` (58 rows, rates within 1e-6, since C writes
them with 6 decimals). Rank correlations: −0.07 (grid) and +0.20 (qualifying),
as in the notebook.

**Timing** (median of 3, MacBook Air M-series; pandas: `load.py`,
`features.py --repeat 3`, `questions.py`):

| step | data | C | pandas B (vectorised) | pandas A (lambda) |
|---|---|---|---|---|
| parse / load 14 tables | `data/` | 19 ms | 34 ms | |
| form (6.4) | `data/` | 3.5 ms | 46 ms | 796 ms |
| Q1 (5.1) | `data/` | 0.4 ms | 8.8 ms | |
| parse / load 14 tables | `x10` | 201 ms | 224 ms | |
| form (6.4) | `x10` | 43 ms | 427 ms | 8,428 ms |
| Q1 (5.1) | `x10` | 2.6 ms | 35 ms | |

The C cleaning step takes 4.9 ms (`data/`) and 48 ms (`x10`); pandas cleans
all 14 tables in one step, so there is no matching pandas number.

Loading is almost as fast in pandas as in C, because `read_csv` is itself
written in C. The form step is 10–13 times faster in C than vectorised pandas:
pandas builds a new column (a full pass over memory) for every
`groupby`/`shift`/`rolling` call, while C computes all seven features in two
passes over compact structs. C time grows about 10× from `data/` to `x10`, as
an O(n) pass should (the sorts are O(n log n), which is close at this size).
On `x10`, Q1 keeps 21 circuits instead of 14–15: every count is 10 times
larger, so more circuits reach the minimum of 10 front-row starts.
