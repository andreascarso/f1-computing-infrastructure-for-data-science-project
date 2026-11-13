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
