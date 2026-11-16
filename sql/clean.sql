-- Cleaning in SQL: notebook Section 4, same decisions as impl/python/clean.py.
--
-- Every cleaned table is a new <name>_clean table built from the raw one; the raw
-- tables loaded by the loader are never changed. Checks that guard a decision
-- (the pandas `assert`s) are DO blocks that stop the script with an error.
--
-- Run after the loader, from the repo root:
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/clean.sql
--
-- Safe to re-run: one transaction, and every _clean table is dropped first.

BEGIN;

DROP TABLE IF EXISTS status_clean, races_clean, qualifying_clean,
                     lap_times_clean, pit_stops_clean;


-- ---------------------------------------------------------------------------
-- 4.8 status_clean: one category per status
-- ---------------------------------------------------------------------------
-- An explicit lookup table instead of keyword matching: a regex on "oil" or
-- "fire" would also catch borderline names. Anything not listed is 'other';
-- e.g. 'Not classified' is too little distance, not a regulation decision.
-- 'Finished' and '+N Lap(s)' are the only pattern, handled before the lookup.

CREATE TEMP TABLE status_lookup (status TEXT PRIMARY KEY, category TEXT NOT NULL)
ON COMMIT DROP;

INSERT INTO status_lookup (status, category) VALUES
    ('Did not qualify', 'did_not_start'), ('Did not prequalify', 'did_not_start'),
    ('Withdrew', 'did_not_start'),

    ('Disqualified', 'regulation'), ('Excluded', 'regulation'), ('107% Rule', 'regulation'),
    ('Underweight', 'regulation'), ('Safety concerns', 'regulation'), ('Safety', 'regulation'),

    ('Accident', 'accident'), ('Collision', 'accident'), ('Collision damage', 'accident'),
    ('Spun off', 'accident'), ('Fatal accident', 'accident'), ('Damage', 'accident'),
    ('Debris', 'accident'),

    -- Power unit and engine
    ('Engine', 'mechanical'), ('Engine fire', 'mechanical'), ('Engine misfire', 'mechanical'),
    ('Power Unit', 'mechanical'), ('ERS', 'mechanical'), ('Turbo', 'mechanical'),
    ('Supercharger', 'mechanical'), ('Power loss', 'mechanical'), ('Ignition', 'mechanical'),
    ('Spark plugs', 'mechanical'), ('Magneto', 'mechanical'), ('Distributor', 'mechanical'),
    ('Injection', 'mechanical'), ('Alternator', 'mechanical'), ('Battery', 'mechanical'),
    ('Electrical', 'mechanical'), ('Electronics', 'mechanical'), ('Crankshaft', 'mechanical'),
    ('Overheating', 'mechanical'), ('Cooling system', 'mechanical'), ('Radiator', 'mechanical'),
    ('Exhaust', 'mechanical'),
    -- Fluids
    ('Oil leak', 'mechanical'), ('Oil pressure', 'mechanical'), ('Oil pump', 'mechanical'),
    ('Oil line', 'mechanical'), ('Oil pipe', 'mechanical'), ('Water leak', 'mechanical'),
    ('Water pressure', 'mechanical'), ('Water pump', 'mechanical'), ('Water pipe', 'mechanical'),
    ('Fuel system', 'mechanical'), ('Fuel pressure', 'mechanical'), ('Fuel pump', 'mechanical'),
    ('Fuel leak', 'mechanical'), ('Fuel pipe', 'mechanical'), ('Fuel', 'mechanical'),
    ('Hydraulics', 'mechanical'), ('Pneumatics', 'mechanical'),
    -- Drivetrain
    ('Gearbox', 'mechanical'), ('Transmission', 'mechanical'), ('Clutch', 'mechanical'),
    ('Differential', 'mechanical'), ('Driveshaft', 'mechanical'), ('Halfshaft', 'mechanical'),
    ('Drivetrain', 'mechanical'), ('CV joint', 'mechanical'), ('Axle', 'mechanical'),
    ('Launch control', 'mechanical'),
    -- Chassis, suspension, brakes, wheels
    ('Suspension', 'mechanical'), ('Track rod', 'mechanical'), ('Steering', 'mechanical'),
    ('Brakes', 'mechanical'), ('Brake duct', 'mechanical'), ('Wheel bearing', 'mechanical'),
    ('Wheel rim', 'mechanical'), ('Chassis', 'mechanical'), ('Undertray', 'mechanical'),
    ('Rear wing', 'mechanical'), ('Throttle', 'mechanical'), ('Vibrations', 'mechanical'),
    ('Heat shield fire', 'mechanical'), ('Fire', 'mechanical'), ('Driver Seat', 'mechanical'),
    ('Seat', 'mechanical'), ('Safety belt', 'mechanical'),
    -- Generic failures
    ('Mechanical', 'mechanical'), ('Technical', 'mechanical');

-- Every listed name must exist in the data (catches spelling mismatches)
DO $$
DECLARE unknown TEXT;
BEGIN
    SELECT string_agg(l.status, ', ') INTO unknown
    FROM status_lookup l LEFT JOIN status s USING (status)
    WHERE s.statusId IS NULL;
    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION 'status_lookup names not in status: %', unknown;
    END IF;
END $$;

CREATE TABLE status_clean AS
SELECT statusId, status, category, category = 'mechanical' AS is_mechanical
FROM (
    SELECT s.statusId,
           s.status,
           CASE
               WHEN s.status = 'Finished' OR s.status ~ '^\+\d+ Laps?$' THEN 'finished'
               ELSE COALESCE(l.category, 'other')
           END AS category
    FROM status s
    LEFT JOIN status_lookup l USING (status)
) c
ORDER BY statusId;

ALTER TABLE status_clean ADD PRIMARY KEY (statusId);


-- ---------------------------------------------------------------------------
-- 4.2, 4.6 races_clean
-- ---------------------------------------------------------------------------
-- The schema already stores date as DATE and the clock times as TIME, so the
-- pandas conversions are done at load time; missing session dates/times are
-- structural (they only exist from 2021) and kept (4.6.2). Only the 4.6.3
-- consistency checks remain.

CREATE TABLE races_clean AS
SELECT * FROM races ORDER BY raceId;

ALTER TABLE races_clean ADD PRIMARY KEY (raceId);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM races_clean WHERE EXTRACT(YEAR FROM date) <> year) THEN
        RAISE EXCEPTION 'races_clean: race date outside its season';
    END IF;
    IF EXISTS (SELECT 1 FROM races_clean
               WHERE fp1_date > date OR fp2_date > date OR fp3_date > date
                  OR quali_date > date OR sprint_date > date) THEN
        RAISE EXCEPTION 'races_clean: a session date is after the race';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 4.3, 4.7 qualifying_clean: Q1-Q3 to seconds, then label the missing strings
-- ---------------------------------------------------------------------------
-- 'm:ss.sss' becomes an interval by putting '00:' (hours) in front, the same
-- trick as pandas' to_timedelta; EXTRACT(EPOCH ...) gives the seconds.
-- Empty strings ('') count as missing, as pandas reads them.
-- Only the text columns get labels; the *_seconds columns stay NULL (4.7.2).

CREATE TABLE qualifying_clean AS
WITH t AS (
    SELECT q.*,
           r.year >= 2006 AS knockout_era,   -- Q1/Q2/Q3 format exists from 2006
           EXTRACT(EPOCH FROM ('00:' || NULLIF(q.q1, ''))::INTERVAL) AS q1_seconds,
           EXTRACT(EPOCH FROM ('00:' || NULLIF(q.q2, ''))::INTERVAL) AS q2_seconds,
           EXTRACT(EPOCH FROM ('00:' || NULLIF(q.q3, ''))::INTERVAL) AS q3_seconds
    FROM qualifying q
    JOIN races r USING (raceId)
),
f AS (
    SELECT t.*,
           q1_seconds IS NOT NULL AS has_q1,
           q2_seconds IS NOT NULL AS has_q2,
           q3_seconds IS NOT NULL AS has_q3
    FROM t
)
SELECT qualifyId, raceId, driverId, constructorId, number, position,
       CASE
           WHEN NOT (has_q1 OR has_q2 OR has_q3)    THEN 'No Qualifying Time Recorded'
           ELSE NULLIF(q1, '')
       END AS q1,
       CASE
           WHEN NOT (has_q1 OR has_q2 OR has_q3)    THEN 'No Qualifying Time Recorded'
           WHEN has_q1 AND NOT has_q2 AND NOT has_q3 AND knockout_era
                                                    THEN 'Not Recorded After Q1'
           WHEN has_q1 AND NOT has_q2 AND NOT knockout_era
                                                    THEN 'Not Recorded (Historical Format)'
           ELSE NULLIF(q2, '')
       END AS q2,
       CASE
           WHEN NOT (has_q1 OR has_q2 OR has_q3)    THEN 'No Qualifying Time Recorded'
           WHEN has_q1 AND has_q2 AND NOT has_q3 AND knockout_era
                                                    THEN 'Not Recorded After Q2'
           WHEN has_q1 AND NOT has_q2 AND NOT has_q3 AND knockout_era
                                                    THEN 'Not Recorded After Q1'
           WHEN has_q1 AND NOT has_q3 AND NOT knockout_era
                                                    THEN 'Not Recorded (Historical Format)'
           ELSE NULLIF(q3, '')
       END AS q3,
       q1_seconds, q2_seconds, q3_seconds
FROM f
ORDER BY qualifyId;

ALTER TABLE qualifying_clean ADD PRIMARY KEY (qualifyId);

DO $$
BEGIN
    -- every missing string got a label (no pattern was left out)
    IF EXISTS (SELECT 1 FROM qualifying_clean WHERE q1 IS NULL OR q2 IS NULL OR q3 IS NULL) THEN
        RAISE EXCEPTION 'qualifying_clean: unlabelled missing session time';
    END IF;
    IF EXISTS (SELECT 1 FROM qualifying_clean GROUP BY raceId, driverId HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'qualifying_clean: duplicate (raceId, driverId)';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 4.4 lap_times_clean and pit_stops_clean: durations from milliseconds
-- ---------------------------------------------------------------------------
-- 1000.0 (not 1000) so the division is numeric, not integer division.

CREATE TABLE lap_times_clean AS
SELECT *, milliseconds / 1000.0 AS time_seconds
FROM lap_times
ORDER BY raceId, driverId, lap;

ALTER TABLE lap_times_clean ADD PRIMARY KEY (raceId, driverId, lap);

CREATE TABLE pit_stops_clean AS
SELECT *, milliseconds / 1000.0 AS duration_seconds
FROM pit_stops
ORDER BY raceId, driverId, stop;

ALTER TABLE pit_stops_clean ADD PRIMARY KEY (raceId, driverId, stop);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM lap_times_clean WHERE time_seconds <= 0) THEN
        RAISE EXCEPTION 'lap_times_clean: non-positive lap time';
    END IF;
    IF EXISTS (SELECT 1 FROM pit_stops_clean WHERE duration_seconds <= 0) THEN
        RAISE EXCEPTION 'pit_stops_clean: non-positive pit stop';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 4.5 Rows and columns match the notebook's cleaned tables
-- ---------------------------------------------------------------------------
-- A WARNING, not an error: on the scaled datasets (bench/scale_data.py) the
-- row counts differ on purpose, and the cleaning must still run there.

DO $$
DECLARE
    t RECORD;
    n_rows BIGINT;
    n_cols INT;
BEGIN
    FOR t IN SELECT * FROM (VALUES
        ('status_clean',       139,  4),
        ('races_clean',       1125, 18),
        ('qualifying_clean', 10494, 12),
        ('lap_times_clean', 589081,  7),
        ('pit_stops_clean',  11371,  8)
    ) AS v(name, nb_rows, nb_cols)
    LOOP
        EXECUTE format('SELECT count(*) FROM %I', t.name) INTO n_rows;
        SELECT count(*) INTO n_cols FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = t.name;
        IF (n_rows, n_cols) = (t.nb_rows, t.nb_cols) THEN
            RAISE NOTICE '% % rows x % cols, same as the notebook',
                rpad(t.name, 18), n_rows, n_cols;
        ELSE
            RAISE WARNING '% % rows x % cols, notebook has % x %',
                rpad(t.name, 18), n_rows, n_cols, t.nb_rows, t.nb_cols;
        END IF;
    END LOOP;
END $$;

COMMIT;
