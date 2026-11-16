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

DROP VIEW IF EXISTS q2_home_advantage, q2_home_by_grid,
                    q1_rank_correlation, q1_conversion;


-- ---------------------------------------------------------------------------
-- 5.1 Q1: which circuits convert a front-row start into a podium most often,
-- and did that change after 2014?
-- ---------------------------------------------------------------------------
-- Joins: results_clean -> races_clean (year, circuit) -> circuits_clean (name),
-- plus qualifying_clean (qualifying position).
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
    JOIN circuits_clean c USING (circuitId)
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


-- ---------------------------------------------------------------------------
-- 5.2 Q2: do drivers finish on the podium more often at home, after
-- controlling for starting position?
-- ---------------------------------------------------------------------------
-- Joins: results_clean -> drivers_clean (home_country, home_country_2, 4.11.2)
-- and results_clean -> races_clean -> circuits_clean (country).
--
-- A race is a home race when the circuit's country is the driver's home
-- country (either one for dual nationalities). Home drivers might simply
-- start further up, so home and away are compared inside the same
-- starting-position group.
--
-- Conditional aggregation: FILTER (WHERE is_home) makes the home and the
-- away columns in one GROUP BY, without a pivot step. Same columns as the
-- pandas table, in snake_case.

CREATE VIEW q2_home_by_grid AS
WITH q2_rows AS (
    SELECT r.podium,
           -- most drivers have no second country: country = NULL is NULL,
           -- and false OR NULL is NULL, so COALESCE makes it "away"
           COALESCE(c.country = d.home_country OR c.country = d.home_country_2, false)
               AS is_home,
           -- the bins of pd.cut([-1, 0, 3, 6, 10, inf]); grid 0 (no slot
           -- recorded, often a pit-lane start) is its own group
           CASE
               WHEN r.grid = 0   THEN 'grid 0'
               WHEN r.grid <= 3  THEN '1–3'
               WHEN r.grid <= 6  THEN '4–6'
               WHEN r.grid <= 10 THEN '7–10'
               ELSE                   '11+'
           END AS grid_group,
           -- to sort the groups by position, not alphabetically
           CASE
               WHEN r.grid = 0   THEN 0
               WHEN r.grid <= 3  THEN 1
               WHEN r.grid <= 6  THEN 2
               WHEN r.grid <= 10 THEN 3
               ELSE                   4
           END AS group_order
    FROM results_clean r
    JOIN races_clean ra   USING (raceId)
    JOIN circuits_clean c USING (circuitId)
    JOIN drivers_clean d  USING (driverId)
),
by_group AS (
    SELECT group_order, grid_group,
           100.0 * avg(podium) FILTER (WHERE NOT is_home) AS rate_away,
           100.0 * avg(podium) FILTER (WHERE is_home)     AS rate_home,
           count(*) FILTER (WHERE NOT is_home)            AS starts_away,
           count(*) FILTER (WHERE is_home)                AS starts_home
    FROM q2_rows
    GROUP BY group_order, grid_group
)
SELECT grid_group, rate_away, rate_home, starts_away, starts_home,
       rate_home - rate_away AS difference_pp,
       group_order
FROM by_group;


-- Q2 in one number: the group differences weighted by the number of home
-- starts (pp), next to the raw rates without any control. A group without
-- home (or away) starts has no difference (NULL); sum() skips NULLs, as
-- pandas' sum() skips NaN.

CREATE VIEW q2_home_advantage AS
WITH totals AS (
    SELECT sum(starts_home) AS starts_home,
           sum(starts_away) AS starts_away,
           sum(rate_home * starts_home) / sum(starts_home) AS raw_rate_home,
           sum(rate_away * starts_away) / sum(starts_away) AS raw_rate_away,
           sum(difference_pp * starts_home) / sum(starts_home) AS home_advantage_pp
    FROM q2_home_by_grid
)
SELECT starts_home, starts_away,
       100.0 * starts_home / (starts_home + starts_away) AS home_share,
       raw_rate_home, raw_rate_away, home_advantage_pp
FROM totals;


SELECT grid_group,
       round(rate_away, 2) AS "rate away", round(rate_home, 2) AS "rate home",
       starts_away AS "starts away", starts_home AS "starts home",
       round(difference_pp, 2) AS "difference (pp)"
FROM q2_home_by_grid
ORDER BY group_order;

SELECT starts_home AS "home starts",
       round(home_share, 1)        AS "home share (%)",
       round(raw_rate_home, 1)     AS "raw rate home",
       round(raw_rate_away, 1)     AS "raw rate away",
       round(home_advantage_pp, 2) AS "home advantage (pp)"
FROM q2_home_advantage;


-- ---------------------------------------------------------------------------
-- Key numbers next to the notebook's Section 5
-- ---------------------------------------------------------------------------
-- A WARNING, not an error: on the scaled datasets (bench/scale_data.py) they
-- differ on purpose.

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
             WHERE front_row_by = ''grid'' AND circuit = ''Hungaroring'' AND era = ''1994–2013''', 72.5),
        (7, 'Q2 home starts',
            'SELECT starts_home FROM q2_home_advantage', 1943),
        (8, 'Q2 away starts',
            'SELECT starts_away FROM q2_home_advantage', 22743),
        (9, 'Q2 raw rate home (%)',
            'SELECT round(raw_rate_home, 1) FROM q2_home_advantage', 11.6),
        (10, 'Q2 raw rate away (%)',
            'SELECT round(raw_rate_away, 1) FROM q2_home_advantage', 13.7),
        (11, 'Q2 grid 1–3 difference (pp)',
            'SELECT round(difference_pp, 2) FROM q2_home_by_grid WHERE grid_group = ''1–3''', 0.37),
        (12, 'Q2 home advantage (pp)',
            'SELECT round(home_advantage_pp, 2) FROM q2_home_advantage', -0.61)
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
