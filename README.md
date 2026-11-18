# One F1 analysis, five tools

**The question:** if you run the same Formula 1 data analysis in pandas, in a
PostgreSQL database, and in C, Rust and R, how much faster or slower does it
get, and how much memory does it need, as the data grows from a quarter of
the real size to fifty times it?

The analysis is the data work of our course notebook (`team9.ipynb`): load 75
seasons of race results, clean them, answer three questions about the races
and build the features for a podium-prediction model. The answers are shown in
a dashboard next to the measurements.

## Team (Group I)

| name | GitHub | role |
|---|---|---|
| Andrea Scarso | [@andreascarso](https://github.com/andreascarso) | Database: Docker Compose, schema, loader, the SQL cleaning, questions and features; project plan |
| Ece Zortuna | [@ecezortuna](https://github.com/ecezortuna) | pandas: cleaning, questions and features (Sections 4–6); the C and Rust ports |
| Cedric Kopp | [@cedric-kopp](https://github.com/cedric-kopp) | Python environment and pandas load; output check, scaled datasets, benchmark harness and bench image; dashboard |
| Anna Luna Orso | [@annaluna2807](https://github.com/annaluna2807) | The R port (data.table) |

## Design: six questions before writing code

**1. What question do we answer?** Mainly the one above: for each step of the
pipeline (load, clean, features, questions), how do time and memory compare
across tools and data sizes, and why? The pipeline itself answers three F1
questions (notebook Section 5):

- **Q1:** which circuits turn a front-row start into a podium most often, and
  did that change after 2014?
- **Q2:** do drivers finish on the podium more often at home, once starting
  position is taken into account?
- **Q3:** which constructors retire most often for mechanical reasons, in
  2014–2021 compared with 2022–2024?

**2. What data?**

- **Source:** [Formula 1 World Championship (1950 – 2024)](https://www.kaggle.com/datasets/rohanrao/formula-1-world-championship-1950-2020)
  on Kaggle, compiled from the Ergast API.
- **Licence:** CC0 (public domain).
- **Size:** 14 CSV files, 22 MB, about 701,000 rows. `lap_times` makes up
  589,000 of them; `results` has 26,759, one per driver per race, for 1,125
  races.
- **Copies:** they are in `data/`. For the scaling runs, `bench/scale_data.py`
  writes 25%, 50%, ×10 and ×50 versions (5.1 MB to 1.3 GB).

**3. Which database, and why?** PostgreSQL 16, in Docker.

- **The data is relational:** 14 tables linked by ids (results → races →
  circuits, results → drivers, …). Primary and foreign keys check those links
  when the data is loaded.
- **The work is SQL work:**
  - the questions are joins and group-bys
  - the rolling driver form (6.4) is a window function
  - `COPY` loads a CSV in one command
- **One container, many users:** Postgres runs as a server that the loader,
  `psql` and the dashboard all connect to at the same time.
- **Not chosen:**
  - SQLite has no server, so the containers would have to share one file.
  - DuckDB is a column store, built for exactly this kind of analysis, and
    would likely be faster. We picked the row store the course uses and
    measure what that costs.

**4. How is the data processed?** The same notebook sections in several tools:

| step | notebook | pandas | SQL | C, Rust, R |
|---|---|---|---|---|
| load the 14 CSVs | 2 | `read_csv` | `COPY` (loader container) | own CSV reading |
| clean | 4 | all 14 tables | the 11 tables the questions and features use | only `results`, as 6.4 needs it |
| features | 6 | all 22, in two versions: `transform(lambda)` and vectorised | 18 of 22, window functions | only form (6.4): 7 features |
| Q1–Q3 | 5 | all three | all three, as views | Q1 only |

pandas is the reference. Every other implementation writes its feature table
in one shared format, and the benchmark only stores its numbers if that table
matches pandas' (and, for the ports, Q1 too).

**5. What does the user see?** A Streamlit dashboard on
<http://localhost:8501> (its own container), with:

- the answers to Q1–Q3, read from the database
- the benchmark: time per step and implementation, memory, disk space, and how
  the times grow with the data

**6. What do we measure?** For every implementation and dataset (25%, 50%,
100%, ×10, ×50):

- **Time:** split into startup (starting the process or container), parse
  (reading the CSVs) and compute (each cleaning, feature and question step),
  plus wall-clock total. Each number is the median of at least 3 fresh runs.
- **Memory:** the process's max RSS and the container's peak (`docker stats`).
- **Disk:** the CSV files next to their size inside Postgres.

All runs are on one laptop, with its CPU and Docker resources stored next to
every number. `bench/benchmark.py` does the measuring and writes everything to
the `benchmark_results` table.


## Scope: which notebook sections run where

pandas and SQL are the **core**: the whole data part of the notebook. C, Rust
and R are **ports** of one slice of it: the CSV loading, the rolling form
(6.4) and Q1. Form is a loop over each driver's past races; written as a
per-group `transform(lambda)` in pandas it takes 1.16 s on `data/`, against
56 ms vectorised, so it shows best what the choice of tool changes. Sections 7
onwards (the models) stay in the notebook.

| notebook section | pandas (core) | SQL (core) | C (port) | Rust (port) | R (port) |
|---|---|---|---|---|---|
| 2 load the 14 CSVs | ✓ [#16](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/16) | ✓ `COPY` [#6](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/6) [#7](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/7) | ✓ [#26](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/26) | ✓ [#27](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/27) | ✓ [#25](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/25) |
| 4.2–4.8 dates, times in seconds, status categories | ✓ [#17](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/17) | ✓ [#8](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/8) | – | – | – |
| 4.9 `results_clean` (one row per driver and race) | ✓ [#17](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/17) | ✓ [#9](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/9) | ✓ | ✓ | ✓ |
| 4.10–4.17 the other tables | ✓ [#17](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/17) | 4.11, 4.13, 4.15, 4.16 (not constructor_results, sprint_results, seasons) [#8](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/8) | – | – | – |
| 5.1 Q1 front row → podium | ✓ [#18](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/18) | ✓ [#10](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/10) | ✓ | ✓ | ✓ |
| 5.2 Q2 home podium rate | ✓ [#18](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/18) | ✓ [#11](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/11) | – | – | – |
| 5.3 Q3 mechanical retirements | ✓ [#18](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/18) | ✓ [#12](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/12) | – | – | – |
| 6.1–6.3 base table, grid, qualifying | ✓ [#19](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/19) | ✓ [#13](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/13) | – | – | – |
| 6.4 driver and constructor form | ✓ two versions [#19](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/19) | ✓ window functions [#14](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/14) | ✓ | ✓ | ✓ |
| 6.5 pre-race standings | ✓ [#19](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/19) | ✓ [#15](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/15) | – | – | – |
| 6.6 context features | ✓ [#19](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/19) | – | – | – | – |
| 6.7 leakage check | ✓ | ✓ | – | – | ✓ |

Around them: the output check [#20](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/20), the scaled datasets [#22](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/22), the
benchmark harness [#21](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/21) and its image [#28](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/28), and the dashboard [#23](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/23).

## Architecture

![Architecture: data/*.csv goes into the loader, which copies rows into the db (PostgreSQL); the app (Streamlit) reads the answers and benchmark_results with SQL and serves them on :8501; the bench container reads the CSVs too; bench/benchmark.py on the laptop drives bench, the loader and psql](docs/architecture.png)

Source: `docs/architecture.svg`; after editing it, `python3 docs/render_architecture.py`
writes the PNG again (needs Chrome or Chromium).

- **db** is the only service that keeps state. `docker compose up -d` starts
  it and the **app**, which connects with `host=db` and is published on port
  8501.
- **loader** runs once per load (`docker compose run --rm loader`): it copies
  the CSVs into the 14 raw tables. The `sql/` scripts then build the clean
  tables, the features and the question views inside the database.
- **bench** holds Python with pandas, R with data.table and the compiled C and
  Rust programs. It idles until `bench/benchmark.py`, running on the laptop,
  starts one implementation after another in it and watches the memory of
  every container.
- The arrows carry data. Dotted arrows are control: commands sent from the
  laptop, not data flowing between the services.

## Repository layout

```
.
├── team9.ipynb              the course notebook: the reference for every number
├── data/                    the 14 CSVs; data/scaled/ is written by bench/scale_data.py (not committed)
├── docker-compose.yml       services db, app, loader (profile load), bench (profile bench)
├── .env.example             database name, user, password; copy to .env (not committed)
├── requirements.txt         local Python environment
├── impl/
│   ├── python/              pandas: load.py, clean.py, questions.py, features.py (Sections 2, 4, 5, 6)
│   ├── c/                   C port: f1.c, CSV parser, hash map, Makefile
│   ├── rust/                Rust port: Cargo project, csv crate
│   └── r/                   R port: data.table scripts, run.R, validation
├── sql/
│   ├── schema.sql           the 14 raw tables with primary and foreign keys
│   ├── clean.sql            *_clean tables (Section 4)
│   ├── features.sql         features_* tables (6.1–6.5)
│   ├── questions.sql        Q1–Q3 as views (Section 5)
│   ├── export_features.sql  the feature table in the shared output format
│   ├── q1_index_timing.sql  Q1 with and without an index
│   └── benchmark_results.sql  the results table of the benchmark
├── loader/                  loader image: load_db.py (COPY), timed
├── bench/
│   ├── benchmark.py         the harness: runs everything, stores benchmark_results
│   ├── run_pandas.py        one timed pandas run (used by the harness)
│   ├── compare_outputs.py   checks a feature table against pandas'
│   ├── scale_data.py        writes the 25%, 50%, ×10 and ×50 datasets
│   ├── Dockerfile           bench image: builds C and Rust, adds R
│   └── outputs/             feature tables written by the checks (not committed)
├── app/                     dashboard image: app.py (Streamlit), port 8501
└── docs/                    architecture.svg and .png, render_architecture.py
```

## How to run

Needs Docker with Compose v2 and Python 3.11+ (only the standard library; the
pandas scripts themselves run in containers). From a fresh clone, in order:

```bash
# 1. Settings: the database password stays on your laptop
cp .env.example .env                     # then set POSTGRES_PASSWORD in .env

# 2. Start the database and the dashboard (waits until both are healthy)
docker compose up -d --build --wait

# 3. Load the 14 CSVs into Postgres
docker compose run --rm --build loader

# 4. Clean, build the features and answer Q1-Q3 inside the database
for script in clean features questions; do
  docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/$script.sql
done

# 5. Look at the answers
open http://localhost:8501               # Linux: xdg-open, or paste the address into a browser

# 6. Measure: write the scaled datasets, run every implementation on them
python3 bench/scale_data.py data/
python3 bench/benchmark.py --datasets 25pct 50pct data x10 x50
#    then reload the dashboard's Benchmark tab

# 7. Stop (the data stays in the dbdata volume; add -v to delete it too)
docker compose down
```

Step 6 takes a while: `x50` alone is several minutes of SQL loading, and pandas
needs about 6.4 GB there (see Benchmark). `--datasets data` is a quick first run.
The benchmark reloads the database for every dataset, so afterwards it holds the
last one; repeat steps 3 and 4 to get `data/` back. Each step is explained in
the sections below.

## Measurement plan

**What:** every implementation on five sizes of the same data: 25%, 50%,
100% (`data/`), ×10 and ×50 ([#22](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/22)). For each run:

| what | how |
|---|---|
| startup | a process that only starts (interpreter, libraries, container), timed like a real run |
| parse | reading the 14 CSVs (`read_csv`, `COPY`, the ports' own readers) |
| compute | each step after that, timed on its own: clean, features, form (6.4), Q1–Q3 |
| total | wall clock of the whole run, measured from outside the process |
| memory | the process's max RSS; for SQL the db container's peak (`docker stats`) |
| disk | the CSV files next to the same tables in Postgres, and the whole database |

**Fairness rules:**

1. **One machine.** Every number comes from the same laptop with the same Docker
   limits, stored with the result (CPU, number of CPUs, memory). Nothing else
   heavy runs at the same time.
2. **Same conditions for everyone.** Every implementation runs in a container
   and reads the same files from the same mount. Each repetition is a fresh
   process, so no run profits from data loaded by the previous one. The output
   check reads the CSVs before any timed run, so no implementation pays for
   reading them from a cold disk.
3. **Median of 3 or more.** Every number is the median of at least three runs,
   with the minimum and maximum kept next to it.
4. **Startup, parse and compute apart.** A C program starts in milliseconds,
   Python with pandas in about a quarter of a second. Kept apart, the startup
   difference doesn't hide the difference in the work itself.
5. **Correct first.** A number only counts if the implementation's output matches
   pandas (`bench/compare_outputs.py`, and Q1 for the ports). Otherwise nothing
   of that dataset is stored.
6. **Like with like.** The ports do less than pandas and SQL (form and Q1, not
   all of Sections 4–6), so they are compared step by step, not by total.
7. **Threads as shipped.** pandas, C, Rust and R (one data.table thread) use
   one core. Postgres may use parallel workers for a query, as it does by
   default; we leave that on and say so.

**Results** (run `2026-11-18T12:38:25`, all in `benchmark_results`). MacBook Pro
14" with an Apple M4 (10 cores) and 32 GB of RAM, macOS 26.6; Docker Engine 29.8
with 10 CPUs and 7.7 GiB. Every number is the median of 3 runs (the slowest run
was at most 1.27× the fastest, except C on ×50 at 1.47×), and every output
matched pandas: the feature table and, for the ports, Q1.

**Total, wall clock** (one whole run, including startup; not the same work in
every column, see the steps below)

| dataset | pandas | pandas (lambda) | SQL | C | Rust | R |
|---|---|---|---|---|---|---|
| 25% | 400 ms | 1.02 s | 1.60 s | 43 ms | 47 ms | 386 ms |
| 50% | 390 ms | 1.21 s | 2.71 s | 55 ms | 64 ms | 454 ms |
| 100% (`data/`) | 499 ms | 1.58 s | 5.03 s | 81 ms | 90 ms | 603 ms |
| ×10 | 2.46 s | 14.3 s | 64.8 s | 541 ms | 577 ms | 3.56 s |
| ×50 | 13.1 s | 76.8 s | 346.9 s | 2.67 s | 3.25 s | 17.5 s |

**Startup** (a process that does nothing yet: interpreter, libraries, `docker exec`;
SQL: `psql` plus a new loader container)

| dataset | pandas | pandas (lambda) | SQL | C | Rust | R |
|---|---|---|---|---|---|---|
| 25% | 254 ms | 215 ms | 404 ms | 31 ms | 30 ms | 127 ms |
| 100% (`data/`) | 212 ms | 214 ms | 405 ms | 34 ms | 34 ms | 145 ms |
| ×50 | 282 ms | 221 ms | 403 ms | 39 ms | 39 ms | 137 ms |

**Load: parse the 14 CSVs**

| dataset | pandas | pandas (lambda) | SQL | C | Rust | R |
|---|---|---|---|---|---|---|
| 25% | 36 ms | 35 ms | 732 ms | 9.8 ms | 14 ms | 52 ms |
| 50% | 52 ms | 53 ms | 1.54 s | 18 ms | 26 ms | 88 ms |
| 100% (`data/`) | 97 ms | 97 ms | 3.22 s | 38 ms | 48 ms | 168 ms |
| ×10 | 704 ms | 724 ms | 49.4 s | 377 ms | 440 ms | 1.49 s |
| ×50 | 3.86 s | 3.82 s | 263.4 s | 1.94 s | 2.63 s | 7.82 s |

**Form (6.4)**, the same seven features everywhere

| dataset | pandas | pandas (lambda) | SQL | C | Rust | R |
|---|---|---|---|---|---|---|
| 25% | 29 ms | 703 ms | 18 ms | 0.9 ms | 0.9 ms | 90 ms |
| 50% | 40 ms | 875 ms | 34 ms | 1.7 ms | 1.7 ms | 146 ms |
| 100% (`data/`) | 53 ms | 1.14 s | 72 ms | 3.2 ms | 3.2 ms | 182 ms |
| ×10 | 520 ms | 12.5 s | 851 ms | 39 ms | 37 ms | 1.49 s |
| ×50 | 2.83 s | 66.5 s | 3.96 s | 204 ms | 217 ms | 7.53 s |

**Q1 (5.1)** (pandas-lambda skips Q1–Q3: they don't depend on the form version)

| dataset | pandas | pandas (lambda) | SQL | C | Rust | R |
|---|---|---|---|---|---|---|
| 25% | 4.8 ms | – | 2.6 ms | 0.1 ms | 0.1 ms | 13 ms |
| 100% (`data/`) | 6.8 ms | – | 9.4 ms | 0.4 ms | 0.4 ms | 17 ms |
| ×10 | 32 ms | – | 103 ms | 3.0 ms | 5.1 ms | 55 ms |
| ×50 | 212 ms | – | 691 ms | 24 ms | 37 ms | 275 ms |

**Peak memory** (MiB; the process's max RSS; SQL: the db container's peak in `docker stats`)

| dataset | pandas | pandas (lambda) | SQL | C | Rust | R |
|---|---|---|---|---|---|---|
| 25% | 104 | 122 | 188 | 16 | 16 | 89 |
| 50% | 152 | 173 | 190 | 31 | 31 | 99 |
| 100% (`data/`) | 225 | 255 | 202 | 63 | 60 | 148 |
| ×10 | 1,297 | 1,606 | 404 | 636 | 592 | 493 |
| ×50 | 5,778 | 7,194 | 1,180 | 3,276 | 3,016 | 2,218 |

**Disk** (MB)

| dataset | 14 CSVs | 14 tables in Postgres | whole database |
|---|---|---|---|
| 25% | 5.1 | 19.2 (3.7×) | 50.5 |
| 50% | 10.6 | 38.7 (3.7×) | 89.9 |
| 100% (`data/`) | 22.0 | 79.8 (3.6×) | 173.3 |
| ×10 | 244.9 | 772.3 (3.2×) | 1,577.3 |
| ×50 | 1,297.2 | 3,850.0 (3.0×) | 7,813.8 |

The whole database also holds the `_clean` and `features_` tables.

**What the numbers mean**

- **Scaling:** from `data/` to ×50 (50 times the rows), form grows 41 to
  68 times in every tool (53× in pandas, 55× in SQL, 64× in C) and pandas'
  cleaning 46×: close to linear. The ranking hardly changes with size; only
  SQL and vectorised pandas swap places on form (SQL is faster on 25% and
  50%, pandas from `data/` on). The exception is SQL's cleaning, 86× (530 ms to 45.4 s), which
  writes 11 new tables to disk. The totals of the small sets hardly grow,
  because startup is a fixed cost: pandas takes 400 ms on 25% and 499 ms on
  `data/`, four times the data.
- **Startup cost:** fixed per tool and independent of the data: about 35 ms
  for C and Rust (mostly `docker exec` itself), 135 ms for R, 215 ms for
  Python with pandas, 400 ms for SQL (a new loader container plus `psql`). On
  `data/`, startup is 42% of the total for both pandas and C; on ×50 it no
  longer matters.
- **Loading is where SQL pays:** `COPY` writes every row to disk and checks
  23 foreign keys and every primary key, in one transaction, at 5 to 7 MB/s.
  `read_csv` only builds arrays in memory. So the database loads 20 to 70 times
  slower than pandas, and stores the data at 3 to 3.7 times its CSV size. In
  return its memory stays small (1,180 MiB on ×50, against 5,778 MiB for
  pandas), and once the data is in, form in SQL is close to pandas (3.96 s
  against 2.83 s on ×50).
- **Index effect:** none for Q1. With an index on the one unindexed join key,
  `qualifying_clean (raceId, driverId)`, Q1 takes the same time (`data/`
  6.6 ms, ×10 103 ms, ×50 198 ms; see Database) and the planner never uses
  it. Q1 reads every result row from 1994 on and joins about 85% of them to
  qualifying; for that, one sequential scan plus a hash join is cheaper than
  one index lookup per row.
- **C vs Rust:** the compute steps are equal: form 204 ms in C against
  217 ms in Rust on ×50. C parses faster (1.94 s against 2.63 s), because it
  splits the file buffer in place, while Rust's `csv` crate copies each field
  and checks that it is valid UTF-8. Both are about 14× faster than vectorised
  pandas on form and over 300× faster than the `transform(lambda)` version (66.5 s), the
  slowest compute step in the whole benchmark. R with data.table is between
  the two pandas versions on form, and on ×10 and ×50 needs the least memory of
  the ports.

## Calendar

Two milestones: **Checkpoint, 12 Nov** (a working demo end to end: compose up,
data in the database, one result, one timing) and **Final, 19 Nov**
(measurements, README, slides). What was merged when:

| dates | milestone | done |
|---|---|---|
| 30 Oct – 6 Nov | Checkpoint | repository, notebook and data; project plan and issues; Python environment; pandas load and cleaning; PostgreSQL service ([#31](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/31), [#33](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/33)–[#36](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/36)) |
| 7 – 12 Nov | Checkpoint | pandas questions and features in both versions ([#37](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/37), [#38](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/38)) |
| 13 – 16 Nov | Final | output check, scaled datasets ([#39](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/39), [#40](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/40)); schema, loader, SQL cleaning and questions ([#41](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/41)–[#44](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/44)) |
| 17 Nov | Final | SQL features; C, Rust and R ports; benchmark harness ([#45](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/45)–[#49](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/49)) |
| 18 Nov | Final | bench image with all ports, dashboard, README ([#50](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/50)–[#53](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/53) and this one) |
| 18 – 19 Nov | Final | architecture diagram [#24](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/24), measurements table [#29](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/29), slides and demo video [#30](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/issues/30) |

The SQL part of the checkpoint (loader, SQL cleaning, Q1 from the database)
was merged on 15–16 Nov, after the checkpoint date.

## Workflow

1. **One issue, one branch:** `<issue number>-<short-name>`, e.g.
   `21-benchmark-harness`, branched from an up-to-date `main`.
2. **Commits** say what changed, with the issue number:
   `bench: harness with startup/parse/compute (#21)`.
3. **Pull request** into `main`, with `Closes #N` in the description, what was
   tested, and anything the reviewer should look at.
4. **Review:** another member reads it and merges it on GitHub. Nobody pushes to
   `main` directly.
5. **Never committed:** `.env` (passwords), `data/scaled/` and `bench/outputs/`
   (generated), build output; `.gitignore` keeps them out.

## Who did what

From the merged pull requests; each member adds anything that is missing.

- **Andrea Scarso:** project plan and issues ([#31](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/31)); PostgreSQL service
  ([#36](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/36)); schema, loader, SQL cleaning, questions and features
  ([#41](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/41)–[#45](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/45)).
- **Ece Zortuna:** pandas cleaning, Q1–Q3 and features ([#35](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/35), [#37](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/37),
  [#38](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/38)); C port ([#46](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/46)); Rust port ([#47](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/47)).
- **Cedric Kopp:** Python environment and pandas load ([#33](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/33), [#34](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/34)); output
  check, scaled datasets ([#39](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/39), [#40](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/40)); benchmark harness and bench image
  ([#49](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/49), [#50](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/50)); dashboard ([#51](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/51)); README ([#52](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/52), [#53](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/53)).
- **Anna Luna Orso:** R port ([#48](https://github.com/andreascarso/f1-computing-infrastructure-for-data-science-project/pull/48)).

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

## Benchmark

`bench/benchmark.py` runs the implementations in their containers, times them
and stores every number in the `benchmark_results` table. It runs on the laptop
and needs only Docker and Python 3.11+ (no packages). All queries go through
`psql` inside the `db` container, so a Postgres installed on the laptop itself
on port 5432 does not get in the way.

```bash
docker compose up -d --wait                          # the database must be running
python3 bench/benchmark.py                           # data/, all implementations, 3 runs
python3 bench/benchmark.py --datasets data x10 --runs 5
python3 bench/benchmark.py --impl sql --datasets x50
python3 bench/benchmark.py --impl c rust r --datasets data x10
```

Implementations:

| name | what runs | notebook sections |
|---|---|---|
| `pandas` | `bench/run_pandas.py`, version B (vectorised) | load, clean, features (6.1–6.6), Q1–Q3 |
| `pandas-lambda` | the same, version A (`transform(lambda)`) | load, clean, features (Q1–Q3 do not depend on the version) |
| `sql` | loader + `clean.sql` + `features.sql` + `questions.sql` | load, clean, features (18 of 22), Q1–Q3 |
| `c` | `impl/c`, compiled into the bench image | load, the cleaning that 6.4 needs, form (6.4), Q1 |
| `rust` | `impl/rust`, compiled into the bench image | the same as C |
| `r` | `Rscript impl/r/run.R`, one data.table thread | the same as C |

`pandas` is always run (`bench/run_pandas.py --out`), since it is the reference
the other outputs are checked against.
Scaled datasets must be written first (see [Scaled datasets](#scaled-datasets)).

**The bench image** (`bench/Dockerfile`) is a multi-stage build. Two builder
stages compile the ports, C with `gcc -O2` on Debian and Rust with
`cargo build --release --locked` on `rust:1.96-slim`, and only the two binaries
(73 KB and 715 KB) are copied into the final image, which is `python:3.13-slim`
with pandas, R and data.table (Debian's `r-base-core` and `r-cran-data.table`)
and GNU `time`. The final image is 642 MB: 204 MB of Python base, about 215 MB
of pandas and numpy, about 225 MB of R. Keeping the compilers in it would add
about 1.2 GB (the Rust toolchain image alone is 1.14 GB, gcc about 180 MB) for
nothing the benchmark runs. The price is a rebuild after every change to the C
or Rust source; the harness builds the image each time it starts, and Docker's
layer cache makes that a no-op otherwise. The Rust dependencies sit in their own
layer, so a change to `src/` does not download and compile the `csv` crate
again. The build context is the repository root, and
`bench/Dockerfile.dockerignore` lets in only `impl/c`, `impl/rust` and
`bench/requirements.txt`, so `.env` and `data/` never reach the build.

**What it measures**, per dataset and implementation:

| kind | how |
|---|---|
| startup | a no-op process in the same container, timed the same way as a real run: `python -c "import pandas, numpy"` in `bench`; `psql -c 'SELECT 1'` and the loader container with `python -c "import psycopg"` for SQL; the C and Rust binaries without arguments (they print their usage and exit); `Rscript -e 'library(data.table)'` |
| parse | reading the 14 CSVs: pandas `read_csv` (the `load_tables` timer); SQL `COPY` (the loader's "total (wall clock)"); the ports' own load timer |
| compute | clean, features and Q1–Q3. pandas: the script's own timers (`bench/run_pandas.py`). SQL: the scripts run with `\timing` on for every statement and the `Time:` lines are added up; Q1–Q3 are `EXPLAIN ANALYZE` of the question views (planning + execution inside Postgres). `form` (6.4) is stored too, as a part of `features`. Ports: the times they print, for clean, form and Q1; R's form is its join + sort plus the rolling windows, since C and Rust sort inside their form step (the join + sort is stored on its own as a detail) |
| total | wall clock of all processes of one repetition, measured from outside |
| memory | the container's peak in `docker stats` (sampled every 0.5 s in the background; needs at least two samples, so runs under about a second have none), its idle value before the benchmark, and the process's own max RSS: `getrusage` for pandas, GNU `time` for C, Rust and R |
| storage | after the SQL runs: every table's `pg_total_relation_size()` next to its CSV, the 14 tables together and `pg_database_size()` |

Each repetition is a fresh process (`docker exec` into the idle `bench`
container, a new loader container, new `psql` processes); every number is the
median of `--runs` repetitions (default 3), with min and max next to it.
Before anything is stored, each implementation's feature table is compared with
the pandas one (`bench/compare_outputs.py`, written to `bench/outputs/<dataset>/`;
the ports have 7 of the 22 columns, which the check allows). The ports' Q1
(`--q1-out`) is compared with the pandas Q1 too: the same circuits and eras,
equal counts, rates within 1e-6. If anything differs, nothing is stored for that
dataset; `--skip-check` stores the results anyway, with `output_checked = false`.

The table is created by the harness (`sql/benchmark_results.sql`) and survives
reloading the data: one row per number, in long format (`implementation`,
`step`, `kind`, `unit`, `value` = median, `min_value`, `max_value`, `runs`,
`container`, `output_checked`), plus `run_id`, `dataset` and `machine`.

```bash
docker compose exec db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT dataset, implementation, step, kind, value FROM benchmark_results ORDER BY 1, 2, 4"'
```

The numbers of a full run, on all five datasets, are under
[Measurement plan → Results](#measurement-plan).

**Keep in mind:**

- The containers read `data/` through Docker Desktop's file sharing, which is
  slower than the laptop's disk, so pandas loads more slowly here than
  `impl/python/load.py` run directly.
- `docker stats` counts the page cache of files the container has read recently,
  so a container's peak is at least as large as the data it has just read.
  Max RSS is the process alone.
- pandas needs about 6.4 GB on `x50` (measured outside Docker). Docker Desktop's
  default memory limit may be too small; raise it under *Settings → Resources*
  if a run stops with exit code 137.
- R in the image is Debian's 4.5.0 with data.table 1.17.0; `impl/r` was
  written with R 4.6.1 and data.table 1.18.6.1. The outputs match pandas with
  both.
- `--impl sql` resets the database (the loader drops and reloads the 14 raw
  tables), but not `benchmark_results`.

## Dashboard

`app/app.py` is a Streamlit app in its own container (`app` in
`docker-compose.yml`). It starts with the database and connects to it with
`host=db`, like the loader:

```bash
docker compose up -d --build --wait   # db + app; --build picks up changes to app/app.py
open http://localhost:8501
```

| tab | what it shows | read from |
|---|---|---|
| Q1 | per circuit, the podium rate from the front row in 1994–2013 and 2014–2024 (by grid or by qualifying), the rank correlation between the eras | views `q1_conversion`, `q1_rank_correlation` |
| Q2 | home vs away podium rate per starting-position group, the weighted home advantage | views `q2_home_by_grid`, `q2_home_advantage` |
| Q3 | mechanical-retirement rate per constructor in both periods | view `q3_mechanical_rate` |
| Benchmark | per dataset: startup / parse / compute per implementation, each step side by side, memory, storage (CSV vs Postgres), and how the times grow from `25pct` to `x50` | table `benchmark_results` |

The views come from `sql/questions.sql` (after `sql/clean.sql`); the benchmark
creates them too when it runs SQL. Without them, or without benchmark results,
the tab says which command to run. The answers are for the dataset that was
loaded last: a benchmark run on a scaled dataset leaves that dataset in the
database (the page header shows the number of races; `data/` has 1,125). The
Benchmark tab shows the latest run of every implementation per dataset and, by
default, only results whose output matched pandas. Queries are cached for
30 seconds.


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

## Rust port

`impl/rust/` does the same work as the C port (`impl/c/`) with the same
algorithm: notebook Section 2 (load), the results cleaning that 6.4 needs (4.9),
the driver/constructor form (6.4) and Q1 (5.1). The only dependency is the
`csv` crate (no polars); everything else is the standard library.

**Requirements:** Rust with Cargo, installed with [rustup](https://rustup.rs)
(`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`, then open a
new terminal or run `source "$HOME/.cargo/env"`).

**Build and run:**

```bash
cargo build --release --manifest-path impl/rust/Cargo.toml   # writes impl/rust/target/release/f1
impl/rust/target/release/f1 data/                            # prints row counts, Q1 and timings
impl/rust/target/release/f1 data/ --out bench/outputs/rust.csv --q1-out bench/outputs/rust_q1.csv
python bench/compare_outputs.py bench/outputs/pandas.csv bench/outputs/rust.csv
```

Always build with `--release`: a debug build has no optimisation and runs many
times slower.

| file | what it does |
|---|---|
| `src/table.rs` | a table read with the `csv` crate: all fields in one `String` plus their end offsets; `\N` and empty fields come back as `None` |
| `src/main.rs` | load, cleaning (4.9), the qualifying `HashMap`, timing and options |
| `src/form.rs` | form (6.4): sort by (driverId, date), ring buffer with a sliding sum per driver and per team; output in the shared format |
| `src/q1.rs` | Q1 (5.1): `HashMap` join with qualifying on (raceId, driverId), counts per circuit and era, Spearman rank correlation |

**Same algorithm as C, in safe Rust:** the steps match `impl/c/f1.c` one to one
(sort + sliding sum for 6.4, a hash join for Q1). The differences are in the
language: missing values are `Option<f64>` instead of `NAN`, so the compiler
makes every use check for "no value"; there is no `malloc`/`free` and no
manual index bookkeeping; `HashMap` and `sort_unstable_by_key` come from the
standard library instead of a hand-written hash map and `qsort`. Out-of-range
indexing stops the program with an error instead of reading wrong memory.

**Checked:** `compare_outputs.py` gives MATCH against pandas and C on the 7
form columns, also with `--tol 0`, and against pandas on `x10`. `--q1-out` is
byte-for-byte the same file as the C port's and equals `q1.csv` from
`impl/python/questions.py --out` (58 rows). Rank correlations: −0.07 (grid) and
+0.20 (qualifying), as in the notebook.

**Timing** (median of 3, MacBook Air M-series, C and Rust in the same session;
pandas numbers from the C port measurement):

| step | data | Rust | C | pandas B (vectorised) |
|---|---|---|---|---|
| parse / load 14 tables | `data/` | 34 ms | 19 ms | 34 ms |
| clean (4.9) | `data/` | 3.3 ms | 4.9 ms | |
| form (6.4) | `data/` | 2.0 ms | 3.3 ms | 46 ms |
| Q1 (5.1) | `data/` | 0.3 ms | 0.3 ms | 8.8 ms |
| parse / load 14 tables | `x10` | 345 ms | 195 ms | 224 ms |
| clean (4.9) | `x10` | 33 ms | 50 ms | |
| form (6.4) | `x10` | 26 ms | 41 ms | 427 ms |
| Q1 (5.1) | `x10` | 3.9 ms | 3.1 ms | 35 ms |

Safety does not cost speed here: Rust's compute steps are as fast as C's or
faster. The likely reason is the sorting: `sort_unstable_by_key` is compiled
for the exact key type, so comparisons are inlined, while C's `qsort` calls the
comparison function through a pointer for every pair. Parsing is slower in Rust
because the `csv` crate copies every field into the table's buffer and checks
UTF-8, while the C parser splits the file buffer in place without copying. An
earlier version that kept one `StringRecord` per row was about twice as slow
again; one buffer for the whole table, as in C, closed most of that gap.
