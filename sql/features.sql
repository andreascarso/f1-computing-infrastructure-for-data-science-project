-- Feature engineering in SQL: notebook Section 6, same logic as
-- impl/python/features.py.
--
-- Every feature must be known after qualifying and before the race starts.
-- Each step is its own table (features_grid, ...), one row per driver per
-- race; sql/export_features.sql writes them in the shared output format that
-- bench/compare_outputs.py diffs against the pandas version.
--
-- Run after sql/clean.sql, from the repo root:
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/features.sql
--
-- Safe to re-run: one transaction, and every features_ table is dropped first.
-- The features_ tables are copies, not views: re-running clean.sql leaves them
-- as they were (out of date), so run this script again after it.

BEGIN;

DROP TABLE IF EXISTS features_grid, features_form, features_standings CASCADE;


-- ---------------------------------------------------------------------------
-- 6.3 Q1 gap to the fastest qualifier, per qualifying row
-- ---------------------------------------------------------------------------
-- Computed on qualifying_clean BEFORE joining the results: the fastest Q1
-- time of a race can belong to a driver who then did not start, and so is
-- not in results_clean. Q1 only, because every driver runs Q1 in the same
-- session (before 2006 the single session is stored as Q1).
-- A temp table, because the feature and the audit below both read it.

CREATE TEMP TABLE quali_gap ON COMMIT DROP AS
SELECT raceId, driverId, position, q1_seconds,
       min(q1_seconds) OVER (PARTITION BY raceId) AS fastest_q1_seconds,
       (q1_seconds / min(q1_seconds) OVER (PARTITION BY raceId) - 1) * 100 AS gap_pct
FROM qualifying_clean;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM quali_gap WHERE gap_pct < 0) THEN
        RAISE EXCEPTION 'quali_gap: negative gap to the fastest Q1 time';
    END IF;
END $$;

-- Gaps above 30 % are an interrupted lap or a recording error, not
-- competitive pace: they become NULL in the feature only. The notebook
-- lists the same three races (Blundell 1995, Sargeant 2023, Baumgartner 2004).
SELECT g.raceId, g.driverId, round(g.q1_seconds, 3) AS q1_seconds,
       round(g.gap_pct, 3) AS quali_gap_pct,
       round(g.fastest_q1_seconds, 3) AS fastest_q1_seconds,
       ra.year, ra.name, d.surname
FROM quali_gap g
JOIN races_clean ra USING (raceId)
JOIN drivers_clean d USING (driverId)
WHERE g.gap_pct > 30
ORDER BY g.gap_pct DESC;


-- ---------------------------------------------------------------------------
-- 6.1 base table, 6.2 grid features, 6.3 qualifying features: features_grid
-- ---------------------------------------------------------------------------
-- 6.1 One row per driver per race. positionOrder and podium are the race
-- result: they stay in the table because the form features (6.4) are built
-- from PAST rows of them, but they are never a feature of the same race.
--
-- Two splits, whole seasons each:
--   split      chronological: train <= 2019, validation 2020-21, test 2022-24
--   era_split  five 15-season eras shuffled with a fixed seed (numpy). SQL
--              can't replay numpy's random generator, so the seasons it drew
--              are listed here; they are the EXPECTED_VAL_SEASONS /
--              EXPECTED_TEST_SEASONS that features.py asserts.
-- ambiguous_shared_drive: rows picked among several cars (4.9.4), left out
-- of modeling later.

CREATE TABLE features_grid AS
WITH base AS (
    SELECT r.resultId, r.raceId, r.driverId, r.constructorId,
           r.grid, r.positionOrder, r.podium,
           ra.year, ra.round, ra.date, ra.circuitId,
           (r.selection_method <> 'single_record')::INT AS ambiguous_shared_drive,
           CASE
               WHEN ra.year <= 2019 THEN 'train'
               WHEN ra.year <= 2021 THEN 'validation'
               ELSE                      'test'
           END AS split,
           CASE
               WHEN ra.year IN (1951, 1962, 1971, 1976, 1992, 1993, 1999, 2009, 2017, 2019)
                   THEN 'validation'
               WHEN ra.year IN (1958, 1963, 1974, 1979, 1980, 1983, 1998, 2006, 2015, 2022)
                   THEN 'test'
               ELSE 'train'
           END AS era_split,
           -- 6.2 starters in the race: a window, so every row keeps its own line
           count(*) OVER (PARTITION BY r.raceId) AS field_size
    FROM results_clean r
    JOIN races_clean ra USING (raceId)
),
grid AS (
    SELECT base.*,
           (grid = 0)::INT AS grid_zero_flag,
           -- Assumption: grid 0 (no slot recorded) starts from the back
           CASE WHEN grid > 0 THEN grid ELSE field_size END AS grid_filled
    FROM base
)
-- 6.3 LEFT JOIN keeps every result row; rows without a qualifying record
-- (all of them before 1994) get NULLs from qualifying, and COALESCE fills
-- the position from the grid. has_qualifying records that it was filled.
-- The gap stays NULL there (imputed later on training data only).
SELECT g.resultId, g.raceId, g.driverId, g.constructorId, g.grid, g.positionOrder,
       g.podium, g.year, g.round, g.date, g.circuitId, g.ambiguous_shared_drive,
       g.split, g.era_split, g.field_size, g.grid_zero_flag, g.grid_filled,
       q.position AS quali_position,
       CASE WHEN q.gap_pct > 30 THEN NULL ELSE q.gap_pct END AS quali_gap_pct,
       (q.position IS NOT NULL)::INT AS has_qualifying,
       COALESCE(q.position, g.grid_filled) AS quali_position_filled
FROM grid g
LEFT JOIN quali_gap q USING (raceId, driverId)
ORDER BY g.date, g.raceId, g.driverId;

-- The primary key also checks one row per driver per race (a LEFT JOIN with
-- a duplicate on the right would multiply rows)
ALTER TABLE features_grid ADD PRIMARY KEY (raceId, driverId);

DO $$
BEGIN
    IF (SELECT count(*) FROM features_grid) <> (SELECT count(*) FROM results_clean) THEN
        RAISE EXCEPTION 'features_grid: not one row per results_clean row';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 6.4 Driver and constructor form, past races only: features_form
-- ---------------------------------------------------------------------------
-- Form = how the driver or team did in their previous races. pandas sorts by
-- date and uses shift(1), so a race only sees earlier races. In SQL the
-- window frame does the same: it ends at 1 PRECEDING, the row before the
-- current one, so a race never sees its own result (no leakage).
--   last 5 races   ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
--                  = shift(1).rolling(5, min_periods=1).mean()
--   whole career   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
--                  = shift(1).expanding().mean()
-- On a driver's first race the frame is empty: avg() gives NULL (pandas'
-- NaN) and count() gives 0 (pandas' cumcount()). ORDER BY date, raceId:
-- raceId only breaks ties, no driver races twice on one date.
--
-- Constructor form first makes one row per team per race (its podiums and
-- its average finish), then runs the same 5-race window over those rows.
-- The *_source_date columns hold the date of the previous race used, for
-- the leakage check below.

\timing on

CREATE TABLE features_form AS
WITH driver_form AS (
    SELECT raceId, driverId, constructorId,
           count(*)           OVER drv_career AS drv_prev_races,
           avg(podium)        OVER drv_last5  AS drv_podium_rate_last5,
           avg(positionOrder) OVER drv_last5  AS drv_avg_finish_last5,
           avg(podium)        OVER drv_career AS drv_career_podium_rate,
           -- the same frame, but per driver AND circuit
           avg(podium) OVER (PARTITION BY driverId, circuitId ORDER BY date, raceId
                             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
               AS drv_circuit_podium_rate,
           lag(date) OVER (PARTITION BY driverId ORDER BY date, raceId)
               AS drv_form_source_date
    FROM features_grid
    -- named windows: written once, used by several features (and sorted once)
    WINDOW drv_last5  AS (PARTITION BY driverId ORDER BY date, raceId
                          ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
           drv_career AS (PARTITION BY driverId ORDER BY date, raceId
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
team_races AS (
    -- one row per team per race
    SELECT constructorId, raceId, date,
           sum(podium)        AS team_podiums,
           avg(positionOrder) AS team_avg_finish
    FROM features_grid
    GROUP BY constructorId, raceId, date
),
team_form AS (
    SELECT constructorId, raceId,
           avg(team_podiums)    OVER team_last5 AS cons_podiums_last5,
           avg(team_avg_finish) OVER team_last5 AS cons_avg_finish_last5,
           lag(date) OVER (PARTITION BY constructorId ORDER BY date, raceId)
               AS cons_form_source_date
    FROM team_races
    WINDOW team_last5 AS (PARTITION BY constructorId ORDER BY date, raceId
                          ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
)
SELECT d.raceId, d.driverId,
       d.drv_prev_races, d.drv_podium_rate_last5, d.drv_avg_finish_last5,
       d.drv_career_podium_rate, d.drv_circuit_podium_rate,
       t.cons_podiums_last5, t.cons_avg_finish_last5,
       d.drv_form_source_date, t.cons_form_source_date
FROM driver_form d
JOIN team_form t USING (constructorId, raceId)
ORDER BY d.raceId, d.driverId;

\timing off

ALTER TABLE features_form ADD PRIMARY KEY (raceId, driverId);

-- 6.7 leakage check: every past race used is strictly before the race
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM features_form f JOIN features_grid g USING (raceId, driverId)
               WHERE f.drv_form_source_date >= g.date OR f.cons_form_source_date >= g.date) THEN
        RAISE EXCEPTION 'features_form: a form feature uses a race on or after the race date';
    END IF;
    IF (SELECT count(*) FROM features_form) <> (SELECT count(*) FROM features_grid) THEN
        RAISE EXCEPTION 'features_form: not one row per features_grid row';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 6.5 Pre-race championship standings: features_standings
-- ---------------------------------------------------------------------------
-- The standings tables hold the championship AFTER each race, so a race
-- takes the standings of the driver's (team's) previous standings row in
-- the same season. LAG(x) OVER season reads x from the row before, like
-- pandas' shift(1); a self-join to "the previous row" would first need a
-- row number to join on.
--   season start: no row before, LAG gives NULL. 0 points and 0 wins is a
--                 fact (COALESCE), but there is no position yet (stays NULL).
--   points share: points systems changed over the years, so points are a
--                 share of the race's leader (0 when the leader has 0 too).
-- This runs on the standings tables, BEFORE joining the race rows: the
-- leader is the leader of everyone in the standings, and a race row
-- without a standings row stays NULL in every column (LEFT JOIN).

\timing on

CREATE TABLE features_standings AS
WITH drv_pre AS (
    SELECT s.raceId, s.driverId,
           COALESCE(lag(s.points) OVER season, 0) AS pre_points,
           COALESCE(lag(s.wins)   OVER season, 0) AS pre_wins,
           lag(s.position) OVER season            AS pre_position,
           lag(ra.date)    OVER season            AS source_date
    FROM driver_standings_clean s
    JOIN races_clean ra USING (raceId)
    WINDOW season AS (PARTITION BY s.driverId, ra.year ORDER BY ra.date, s.raceId)
),
drv AS (
    -- a window can't be nested in another, so the leader is a second step
    SELECT p.*,
           CASE WHEN max(pre_points) OVER (PARTITION BY raceId) > 0
                THEN pre_points / max(pre_points) OVER (PARTITION BY raceId)
                ELSE 0
           END AS pre_points_share
    FROM drv_pre p
),
cons_pre AS (
    SELECT s.raceId, s.constructorId,
           COALESCE(lag(s.points) OVER season, 0) AS pre_points,
           lag(s.position) OVER season            AS pre_position,
           lag(ra.date)    OVER season            AS source_date
    FROM constructor_standings_clean s
    JOIN races_clean ra USING (raceId)
    WINDOW season AS (PARTITION BY s.constructorId, ra.year ORDER BY ra.date, s.raceId)
),
cons AS (
    SELECT p.*,
           CASE WHEN max(pre_points) OVER (PARTITION BY raceId) > 0
                THEN pre_points / max(pre_points) OVER (PARTITION BY raceId)
                ELSE 0
           END AS pre_points_share
    FROM cons_pre p
)
SELECT g.raceId, g.driverId,
       d.pre_points_share AS drv_pre_points_share,
       d.pre_wins         AS drv_pre_wins,
       d.pre_position     AS drv_pre_position,
       c.pre_points_share AS cons_pre_points_share,
       c.pre_position     AS cons_pre_position,
       d.source_date      AS drv_standings_source_date,
       c.source_date      AS cons_standings_source_date
FROM features_grid g
LEFT JOIN drv d  ON d.raceId = g.raceId AND d.driverId = g.driverId
-- both drivers of a team get the team's standings
LEFT JOIN cons c ON c.raceId = g.raceId AND c.constructorId = g.constructorId
ORDER BY g.raceId, g.driverId;

\timing off

ALTER TABLE features_standings ADD PRIMARY KEY (raceId, driverId);

-- 6.7 leakage check: every standings row used is from before the race
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM features_standings s JOIN features_grid g USING (raceId, driverId)
               WHERE s.drv_standings_source_date >= g.date
                  OR s.cons_standings_source_date >= g.date) THEN
        RAISE EXCEPTION 'features_standings: standings from on or after the race date';
    END IF;
END $$;


-- 6.5.1 Do the standings come from the immediately previous race? LAG again,
-- now on races_clean: the previous race of the season. A source older than
-- that would still be before the race (no leakage) but out of date.

CREATE TEMP TABLE standings_source ON COMMIT DROP AS
WITH previous_race AS (
    SELECT raceId, lag(date) OVER (PARTITION BY year ORDER BY date) AS prev_race_date
    FROM races_clean
),
sources AS (
    SELECT 'drv' AS standings, g.year, p.prev_race_date, s.drv_standings_source_date AS source_date
    FROM features_standings s
    JOIN features_grid g USING (raceId, driverId)
    JOIN previous_race p USING (raceId)
    UNION ALL
    SELECT 'cons', g.year, p.prev_race_date, s.cons_standings_source_date
    FROM features_standings s
    JOIN features_grid g USING (raceId, driverId)
    JOIN previous_race p USING (raceId)
)
SELECT standings,
       CASE WHEN year <= 1979 THEN '1950–1979'
            WHEN year <= 2009 THEN '1980–2009'
            ELSE                   '2010–2024' END AS period,
       CASE WHEN prev_race_date IS NULL        THEN 'season start'
            WHEN source_date IS NULL           THEN 'no earlier standings'
            WHEN source_date = prev_race_date  THEN 'previous race'
            WHEN source_date < prev_race_date  THEN 'older race (stale)'
            ELSE                                    'other' END AS source
FROM sources;

-- The notebook's two tables (rows per period and source)
SELECT standings, period,
       count(*) FILTER (WHERE source = 'no earlier standings') AS "no earlier standings",
       count(*) FILTER (WHERE source = 'previous race')        AS "previous race",
       count(*) FILTER (WHERE source = 'season start')         AS "season start",
       count(*) FILTER (WHERE source NOT IN ('no earlier standings', 'previous race',
                                             'season start'))  AS "older / other"
FROM standings_source
GROUP BY standings, period
ORDER BY standings DESC, period;


-- ---------------------------------------------------------------------------
-- Key numbers next to the notebook's Section 6
-- ---------------------------------------------------------------------------
-- A WARNING, not an error: on the scaled datasets (bench/scale_data.py) they
-- differ on purpose.

DO $$
DECLARE
    c RECORD;
    v NUMERIC;
BEGIN
    FOR c IN SELECT * FROM (VALUES
        (1, '6.1 rows',
            'SELECT count(*) FROM features_grid', 24686),
        (2, '6.1 ambiguous shared-drive rows',
            'SELECT sum(ambiguous_shared_drive) FROM features_grid', 61),
        (3, '6.1 train rows (split)',
            'SELECT count(*) FROM features_grid WHERE split = ''train''', 22553),
        (4, '6.1 test rows (era_split)',
            'SELECT count(*) FROM features_grid WHERE era_split = ''test''', 3406),
        (5, '6.2 grid-0 rows',
            'SELECT sum(grid_zero_flag) FROM features_grid', 130),
        (6, '6.2 mean grid_filled',
            'SELECT round(avg(grid_filled), 1) FROM features_grid', 11.8),
        (7, '6.2 smallest field',
            'SELECT min(field_size) FROM features_grid', 6),
        (8, '6.3 qualifying rows with a Q1 gap',
            'SELECT count(gap_pct) FROM quali_gap', 10338),
        (9, '6.3 Q1 gaps above 30 %',
            'SELECT count(*) FROM quali_gap WHERE gap_pct > 30', 3),
        (10, '6.3 largest gap kept (%)',
            'SELECT round(max(quali_gap_pct), 1) FROM features_grid', 27.5),
        (11, '6.3 rows without qualifying',
            'SELECT count(*) FROM features_grid WHERE has_qualifying = 0', 14231),
        (12, '6.3   of which from 1994 on',
            'SELECT count(*) FROM features_grid WHERE has_qualifying = 0 AND year >= 1994', 1824),
        (13, '6.3 rows without quali gap',
            'SELECT count(*) FROM features_grid WHERE quali_gap_pct IS NULL', 14390),
        (14, '6.4 rows with driver form',
            'SELECT count(drv_form_source_date) FROM features_form', 24000),
        (15, '6.4 rows with team form',
            'SELECT count(cons_form_source_date) FROM features_form', 24421),
        (16, '6.4 max drv_prev_races',
            'SELECT max(drv_prev_races) FROM features_form', 401),
        (17, '6.4 mean drv_prev_races',
            'SELECT round(avg(drv_prev_races), 2) FROM features_form', 68.09),
        (18, '6.4 mean drv_podium_rate_last5',
            'SELECT round(avg(drv_podium_rate_last5), 2) FROM features_form', 0.14),
        (19, '6.4 mean drv_avg_finish_last5',
            'SELECT round(avg(drv_avg_finish_last5), 2) FROM features_form', 11.69),
        (20, '6.4 mean drv_career_podium_rate',
            'SELECT round(avg(drv_career_podium_rate), 2) FROM features_form', 0.12),
        (21, '6.4 mean cons_podiums_last5',
            'SELECT round(avg(cons_podiums_last5), 2) FROM features_form', 0.31),
        (22, '6.4 drv_circuit_podium_rate NULL %',
            'SELECT round(100.0 * count(*) FILTER (WHERE drv_circuit_podium_rate IS NULL)
                     / count(*), 2) FROM features_form', 30.24),
        (23, '6.5 rows with driver standings',
            'SELECT count(drv_standings_source_date) FROM features_standings', 21771),
        (24, '6.5 rows with team standings',
            'SELECT count(cons_standings_source_date) FROM features_standings', 21770),
        (25, '6.5 drv_pre_points_share not NULL',
            'SELECT count(drv_pre_points_share) FROM features_standings', 24261),
        (26, '6.5 cons_pre_points_share not NULL',
            'SELECT count(cons_pre_points_share) FROM features_standings', 23355),
        (27, '6.5 mean drv_pre_points_share',
            'SELECT round(avg(drv_pre_points_share), 2) FROM features_standings', 0.19),
        (28, '6.5 mean drv_pre_wins',
            'SELECT round(avg(drv_pre_wins), 2) FROM features_standings', 0.34),
        (29, '6.5 mean drv_pre_position',
            'SELECT round(avg(drv_pre_position), 2) FROM features_standings', 12.85),
        (30, '6.5 max drv_pre_position',
            'SELECT max(drv_pre_position) FROM features_standings', 102),
        (31, '6.5 mean cons_pre_points_share',
            'SELECT round(avg(cons_pre_points_share), 2) FROM features_standings', 0.28),
        (32, '6.5 mean cons_pre_position',
            'SELECT round(avg(cons_pre_position), 2) FROM features_standings', 6.10),
        (33, '6.5.1 stale driver standings',
            'SELECT count(*) FROM standings_source
             WHERE standings = ''drv'' AND source NOT IN
                   (''season start'', ''no earlier standings'', ''previous race'')', 0),
        (34, '6.5.1 stale team standings',
            'SELECT count(*) FROM standings_source
             WHERE standings = ''cons'' AND source NOT IN
                   (''season start'', ''no earlier standings'', ''previous race'')', 0)
    ) AS t(i, label, query, notebook)
    ORDER BY i
    LOOP
        EXECUTE c.query INTO v;
        IF v = c.notebook THEN
            RAISE NOTICE '% %, same as the notebook', rpad(c.label, 36), v;
        ELSE
            RAISE WARNING '% %, notebook has %', rpad(c.label, 36), v, c.notebook;
        END IF;
    END LOOP;
END $$;

COMMIT;
