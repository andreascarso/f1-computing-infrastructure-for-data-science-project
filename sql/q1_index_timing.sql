-- Q1 query time without and with an index (issue #10).
--
-- Times `SELECT * FROM q1_conversion` (sql/questions.sql) twice: once as
-- clean.sql leaves the tables, once with an index on qualifying_clean's join
-- key. For each it prints the plan (EXPLAIN ANALYZE) and the median of 20 runs.
--
-- Why this index: Q1 joins qualifying_clean ON (raceId, driverId), and that is
-- the only join key without an index (the table's primary key is qualifyId).
-- The other joins use primary keys, which Postgres indexes automatically.
--
-- Run after sql/questions.sql, from the repo root:
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/q1_index_timing.sql
--
-- The index is dropped at the end, so the database is left as clean.sql made it.

\pset pager off

-- Median wall time of n runs of a query, in ms. One run first to warm the
-- cache, so every timed run reads from memory. pg_temp: the function lives
-- only for this session.
CREATE FUNCTION pg_temp.median_ms(query TEXT, n INT) RETURNS NUMERIC AS $$
DECLARE
    t0 TIMESTAMPTZ;
    times NUMERIC[] := '{}';
BEGIN
    EXECUTE query;
    FOR i IN 1..n LOOP
        t0 := clock_timestamp();
        EXECUTE query;   -- runs the whole query, the rows are discarded
        times := times || (EXTRACT(EPOCH FROM clock_timestamp() - t0) * 1000)::NUMERIC;
    END LOOP;
    RETURN (SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY t)::NUMERIC, 2)
            FROM unnest(times) AS t);
END $$ LANGUAGE plpgsql;


\echo '=== 1. Without the index ==='
DROP INDEX IF EXISTS qualifying_clean_race_driver_idx;
ANALYZE qualifying_clean;   -- fresh statistics, so the planner sees the table as it is

EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM q1_conversion;
SELECT pg_temp.median_ms('SELECT * FROM q1_conversion', 20) AS "median ms, no index";


\echo '=== 2. With an index on qualifying_clean (raceId, driverId) ==='
CREATE INDEX qualifying_clean_race_driver_idx ON qualifying_clean (raceId, driverId);
ANALYZE qualifying_clean;

EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM q1_conversion;
SELECT pg_temp.median_ms('SELECT * FROM q1_conversion', 20) AS "median ms, with index";


\echo '=== 3. Is the index used? (idx_scan counts index scans since it was created) ==='
SELECT indexrelname AS index, idx_scan
FROM pg_stat_user_indexes
WHERE indexrelname = 'qualifying_clean_race_driver_idx';

DROP INDEX qualifying_clean_race_driver_idx;
