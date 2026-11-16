-- Data-engineering questions in SQL: notebook Section 5, same logic as
-- impl/python/questions.py.
--
-- Each question is a VIEW over the _clean tables, so the query lives in one
-- place: this script prints the answer, and sql/q1_index_timing.sql (or you,
-- in psql) can run `SELECT * FROM q1_conversion` without copying the query.
-- A view stores the query, not the rows: every SELECT runs it again.
--
-- Run after sql/clean.sql, from the repo root:
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/questions.sql
--
-- Safe to re-run: one transaction, and every view is dropped first. Re-running
-- clean.sql drops these views (they depend on the _clean tables), so run this
-- script again after it.

BEGIN;

DROP VIEW IF EXISTS q1_rank_correlation, q1_conversion;


-- ---------------------------------------------------------------------------
-- 5.1 Q1: which circuits convert a front-row start into a podium most often,
-- and did that change after 2014?
-- ---------------------------------------------------------------------------
-- Joins: results_clean -> races_clean (year, circuit) -> circuits (name), plus
-- qualifying_clean (qualifying position). The raw circuits table is enough:
-- Q1 only needs the name, and no name has HTML codes to decode.
--
-- Front row two ways: by grid (where the car started, after penalties) and by
-- qualifying position (before penalties). Qualifying data starts in 1994, so
-- both compare 1994-2013 with 2014-2024 (the hybrid era).
--
-- One row per (front_row_by, circuit, era), the same long format as
-- tidy_q1() in questions.py, so the two outputs can be diffed.

CREATE VIEW q1_conversion AS
WITH q1_rows AS (
    -- one row per driver per race from 1994, with both front-row flags
    SELECT c.name AS circuit,
           CASE WHEN ra.year >= 2014 THEN '2014–2024' ELSE '1994–2013' END AS era,
           r.podium,
           r.grid IN (1, 2) AS grid_front_row,
           -- LEFT JOIN: no qualifying record gives position NULL, and
           -- NULL IN (1, 2) is NULL, so COALESCE makes it "not front row"
           COALESCE(q.position IN (1, 2), false) AS quali_front_row
    FROM results_clean r
    JOIN races_clean ra USING (raceId)
    JOIN circuits c USING (circuitId)
    LEFT JOIN qualifying_clean q USING (raceId, driverId)
    WHERE ra.year >= 1994
),
front AS (
    -- the front-row starters, once per definition (a driver can be in both)
    SELECT 'grid' AS front_row_by, circuit, era, podium FROM q1_rows WHERE grid_front_row
    UNION ALL
    SELECT 'qualifying',           circuit, era, podium FROM q1_rows WHERE quali_front_row
),
by_era AS (
    SELECT front_row_by, circuit, era,
           count(*)    AS starts,
           sum(podium) AS podiums,
           -- 100.0 (not 100) so the division is numeric, not integer division
           100.0 * sum(podium) / count(*) AS rate
    FROM front
    GROUP BY front_row_by, circuit, era
),
checked AS (
    -- how many eras of this circuit have >= 10 starts: a window over the
    -- by_era rows of the same circuit (0, 1 or 2)
    SELECT by_era.*,
           count(*) FILTER (WHERE starts >= 10)
               OVER (PARTITION BY front_row_by, circuit) AS eras_with_enough
    FROM by_era
)
-- Keep circuits with >= 10 front-row starts in BOTH eras: a rate from a
-- handful of races is unreliable. A window value can't be used in the WHERE
-- of its own SELECT, which is why it is computed in a CTE first.
SELECT front_row_by, circuit, era, starts, podiums, rate
FROM checked
WHERE eras_with_enough = 2;


-- Did the ranking of circuits change? Spearman rank correlation between the
-- two eras (1 = same order), per definition. Spearman is the Pearson
-- correlation (corr()) of the ranks; tied rates share their average rank,
-- as in pandas' rank(method='average'): rank() gives a tie its lowest rank,
-- and (ties - 1) / 2 moves it to the middle of the tied block.

CREATE VIEW q1_rank_correlation AS
WITH ranked AS (
    SELECT front_row_by, circuit, era,
           rank() OVER w + (count(*) OVER (PARTITION BY front_row_by, era, rate) - 1) / 2.0
               AS avg_rank
    FROM q1_conversion
    WINDOW w AS (PARTITION BY front_row_by, era ORDER BY rate)
),
paired AS (
    -- one row per circuit with its rank in each era
    SELECT front_row_by, circuit,
           max(avg_rank) FILTER (WHERE era = '1994–2013') AS rank_before,
           max(avg_rank) FILTER (WHERE era = '2014–2024') AS rank_after
    FROM ranked
    GROUP BY front_row_by, circuit
)
SELECT front_row_by,
       count(*) AS circuits,
       corr(rank_before::float8, rank_after::float8) AS rank_corr
FROM paired
GROUP BY front_row_by;


-- The answer, laid out like the notebook's table: one row per circuit,
-- sorted by the 2014-2024 rate.
SELECT front_row_by,
       circuit,
       round(max(rate) FILTER (WHERE era = '1994–2013'), 1) AS "rate 1994–2013",
       round(max(rate) FILTER (WHERE era = '2014–2024'), 1) AS "rate 2014–2024",
       max(starts) FILTER (WHERE era = '1994–2013') AS "starts 1994–2013",
       max(starts) FILTER (WHERE era = '2014–2024') AS "starts 2014–2024"
FROM q1_conversion
GROUP BY front_row_by, circuit
ORDER BY front_row_by, "rate 2014–2024" DESC, circuit;

SELECT front_row_by, circuits, round(rank_corr::numeric, 2) AS rank_corr
FROM q1_rank_correlation
ORDER BY front_row_by;

-- Key numbers next to the notebook's Section 5.1. A WARNING, not an error:
-- on the scaled datasets (bench/scale_data.py) they differ on purpose.
DO $$
DECLARE
    c RECORD;
    v NUMERIC;
BEGIN
    FOR c IN SELECT * FROM (VALUES
        (1, 'Q1 circuits (grid)',
            'SELECT circuits FROM q1_rank_correlation WHERE front_row_by = ''grid''', 15),
        (2, 'Q1 circuits (qualifying)',
            'SELECT circuits FROM q1_rank_correlation WHERE front_row_by = ''qualifying''', 14),
        (3, 'Q1 rank corr (grid)',
            'SELECT round(rank_corr::numeric, 2) FROM q1_rank_correlation WHERE front_row_by = ''grid''', -0.07),
        (4, 'Q1 rank corr (qualifying)',
            'SELECT round(rank_corr::numeric, 2) FROM q1_rank_correlation WHERE front_row_by = ''qualifying''', 0.20),
        (5, 'Q1 Suzuka 2014–2024 (grid, %)',
            'SELECT round(rate, 1) FROM q1_conversion
             WHERE front_row_by = ''grid'' AND circuit = ''Suzuka Circuit'' AND era = ''2014–2024''', 88.9),
        (6, 'Q1 Hungaroring 1994–2013 (grid, %)',
            'SELECT round(rate, 1) FROM q1_conversion
             WHERE front_row_by = ''grid'' AND circuit = ''Hungaroring'' AND era = ''1994–2013''', 72.5)
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
