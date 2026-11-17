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

DROP TABLE IF EXISTS features_grid CASCADE;


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
            'SELECT count(*) FROM features_grid WHERE quali_gap_pct IS NULL', 14390)
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
