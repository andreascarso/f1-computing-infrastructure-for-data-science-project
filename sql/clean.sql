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
-- CASCADE also drops the views built on them (sql/questions.sql); without it
-- the DROP fails once those exist. Re-run questions.sql afterwards.

BEGIN;

DROP TABLE IF EXISTS status_clean, races_clean, qualifying_clean,
                     lap_times_clean, pit_stops_clean, results_clean,
                     nationality_country, drivers_clean, circuits_clean
CASCADE;


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
-- 4.9 results_clean: the modeling table
-- ---------------------------------------------------------------------------
-- 4.9.4 One row per driver per race. Some (raceId, driverId) pairs have
-- several rows (a car taken over mid-race). The row to keep is chosen
-- without looking at the race result, so the target can't leak in:
--   1. the row whose car (number + constructor) matches the driver's
--      qualifying entry, if exactly one row matches
--   2. otherwise the original entry, the lowest resultId (4.9.3)
-- ROW_NUMBER() ranks the rows inside each pair; rank 1 is kept.
-- Comparisons with a NULL give NULL, so COALESCE makes them false, like
-- pandas' eq() with NaN.

CREATE TEMP TABLE results_one_per_driver ON COMMIT DROP AS
WITH matched AS (
    SELECT r.*,
           count(*) OVER (PARTITION BY r.raceId, r.driverId) AS group_size,
           COALESCE(r.number = q.number AND r.constructorId = q.constructorId, false)
               AS car_match
    FROM results r
    LEFT JOIN qualifying_clean q USING (raceId, driverId)
),
preferred AS (
    SELECT m.*,
           car_match AND count(*) FILTER (WHERE car_match)
                             OVER (PARTITION BY raceId, driverId) = 1 AS preferred
    FROM matched m
),
ranked AS (
    SELECT p.*,
           ROW_NUMBER() OVER (PARTITION BY raceId, driverId
                              ORDER BY preferred DESC, resultId) AS rn
    FROM preferred p
)
SELECT resultId, raceId, driverId, constructorId, number, grid, position,
       positionText, positionOrder, points, laps, time, milliseconds,
       fastestLap, rank, fastestLapTime, fastestLapSpeed, statusId,
       CASE
           WHEN group_size = 1 THEN 'single_record'
           WHEN preferred      THEN 'unique_qualifying_match'
           ELSE 'original_entry_fallback'
       END AS selection_method
FROM ranked
WHERE rn = 1;

-- 4.9.7 Remove non-starters (status category) and the Indianapolis 500
-- (1950-1960, run with a separate field). 4.9.10 durations to seconds.
-- 4.9.11 the target `podium`, made only now that the rows are fixed.

CREATE TABLE results_clean AS
SELECT r.*,
       r.milliseconds / 1000.0 AS time_seconds,
       EXTRACT(EPOCH FROM ('00:' || NULLIF(r.fastestLapTime, ''))::INTERVAL)
           AS fastestLapTime_seconds,
       (r.positionOrder <= 3)::INT AS podium
FROM results_one_per_driver r
WHERE r.statusId NOT IN (SELECT statusId FROM status_clean WHERE category = 'did_not_start')
  AND r.raceId   NOT IN (SELECT raceId FROM races_clean WHERE name = 'Indianapolis 500')
ORDER BY r.raceId, r.driverId;

-- The primary key also checks that each driver now has one row per race
ALTER TABLE results_clean ADD PRIMARY KEY (raceId, driverId);

DO $$
BEGIN
    -- 4.9.2: the 6 rows without a car number were all non-starters
    IF EXISTS (SELECT 1 FROM results_clean WHERE number IS NULL) THEN
        RAISE EXCEPTION 'results_clean: row without a car number';
    END IF;
    -- 4.9.8 valid ranges
    IF EXISTS (SELECT 1 FROM results_clean
               WHERE points < 0 OR laps < 0 OR grid < 0 OR positionOrder < 1) THEN
        RAISE EXCEPTION 'results_clean: value out of range';
    END IF;
    IF EXISTS (SELECT 1 FROM results_clean
               WHERE fastestLapTime IS NOT NULL AND fastestLapTime_seconds IS NULL) THEN
        RAISE EXCEPTION 'results_clean: fastest lap time not converted';
    END IF;
END $$;

-- Rows after each step, next to the notebook's 4.9.7 cleaning log
DO $$
DECLARE
    step RECORD;
    n BIGINT;
BEGIN
    FOR step IN SELECT * FROM (VALUES
        (1, 'raw results',                 'SELECT count(*) FROM results',               26759),
        (2, 'after duplicate resolution',  'SELECT count(*) FROM results_one_per_driver', 26668),
        (3, 'after removing non-starters', 'SELECT count(*) FROM results_one_per_driver
                                             WHERE statusId NOT IN (SELECT statusId FROM status_clean
                                                                    WHERE category = ''did_not_start'')', 25067),
        (4, 'after excluding Indy 500',    'SELECT count(*) FROM results_clean',          24686)
    ) AS v(i, label, query, nb_rows)
    ORDER BY i
    LOOP
        EXECUTE step.query INTO n;
        IF n = step.nb_rows THEN
            RAISE NOTICE 'results: % % rows, same as the notebook', rpad(step.label, 28), n;
        ELSE
            RAISE WARNING 'results: % % rows, notebook has %', rpad(step.label, 28), n, step.nb_rows;
        END IF;
    END LOOP;
END $$;


-- ---------------------------------------------------------------------------
-- 4.11 drivers_clean: nationality -> home country
-- ---------------------------------------------------------------------------
-- 4.11.2 A home race is a race in the driver's home country, but drivers have
-- a nationality ('British') and circuits a country ('UK'). nationality_country
-- maps one to the other, written like circuits.country. It is a real table,
-- not a temp one, so later queries (Q2, the home_race feature) can join it.
-- home_country_2 is only set for the two dual nationalities.

CREATE TABLE nationality_country (
    nationality    TEXT PRIMARY KEY,
    home_country   TEXT NOT NULL,
    home_country_2 TEXT
);

INSERT INTO nationality_country (nationality, home_country, home_country_2) VALUES
    ('American', 'USA', NULL), ('American-Italian', 'USA', 'Italy'),
    ('Argentine', 'Argentina', NULL), ('Argentinian', 'Argentina', NULL),
    ('Argentine-Italian', 'Argentina', 'Italy'),
    ('Australian', 'Australia', NULL), ('Austrian', 'Austria', NULL),
    ('Belgian', 'Belgium', NULL), ('Brazilian', 'Brazil', NULL), ('British', 'UK', NULL),
    ('Canadian', 'Canada', NULL), ('Chilean', 'Chile', NULL), ('Chinese', 'China', NULL),
    ('Colombian', 'Colombia', NULL), ('Czech', 'Czech Republic', NULL),
    ('Danish', 'Denmark', NULL), ('Dutch', 'Netherlands', NULL),
    ('East German', 'Germany', NULL), ('Finnish', 'Finland', NULL),
    ('French', 'France', NULL), ('German', 'Germany', NULL),
    ('Hong Kong', 'Hong Kong', NULL), ('Hungarian', 'Hungary', NULL),
    ('Indian', 'India', NULL), ('Indonesian', 'Indonesia', NULL),
    ('Irish', 'Ireland', NULL), ('Italian', 'Italy', NULL), ('Japanese', 'Japan', NULL),
    ('Liechtensteiner', 'Liechtenstein', NULL), ('Malaysian', 'Malaysia', NULL),
    ('Mexican', 'Mexico', NULL), ('Monegasque', 'Monaco', NULL),
    ('New Zealander', 'New Zealand', NULL), ('Polish', 'Poland', NULL),
    ('Portuguese', 'Portugal', NULL), ('Rhodesian', 'Zimbabwe', NULL),
    ('Russian', 'Russia', NULL), ('South African', 'South Africa', NULL),
    ('Spanish', 'Spain', NULL), ('Swedish', 'Sweden', NULL), ('Swiss', 'Switzerland', NULL),
    ('Thai', 'Thailand', NULL), ('Uruguayan', 'Uruguay', NULL),
    ('Venezuelan', 'Venezuela', NULL);

-- Every listed nationality must exist in the data (catches spelling mismatches).
-- Drivers or constructors: 'Hong Kong' is only a constructor nationality; it
-- is kept so the table is the same as the pandas map.
DO $$
DECLARE unknown TEXT;
BEGIN
    SELECT string_agg(n.nationality, ', ') INTO unknown
    FROM nationality_country n
    WHERE NOT EXISTS (SELECT 1 FROM drivers d WHERE btrim(d.nationality) = n.nationality)
      AND NOT EXISTS (SELECT 1 FROM constructors c WHERE btrim(c.nationality) = n.nationality);
    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION 'nationality_country names not in drivers: %', unknown;
    END IF;
END $$;

-- Same 13 columns as the pandas drivers table. dob is already a DATE (schema).
-- Stray whitespace ('Argentinian ') is stripped before the lookup.
-- Missing codes are generated from the surname and flagged; `number` stays
-- missing (permanent numbers only exist from 2014).
CREATE TABLE drivers_clean AS
SELECT d.driverId, d.driverRef, d.number, d.code, d.forename, d.surname, d.dob,
       btrim(d.nationality) AS nationality, d.url,
       d.code IS NULL                                AS code_was_generated,
       COALESCE(d.code, upper(left(d.surname, 3)))   AS code_filled,
       n.home_country,
       n.home_country_2
FROM drivers d
LEFT JOIN nationality_country n ON n.nationality = btrim(d.nationality)
ORDER BY d.driverId;

ALTER TABLE drivers_clean ADD PRIMARY KEY (driverId);

DO $$
DECLARE unmapped TEXT;
BEGIN
    -- the LEFT JOIN leaves home_country NULL for a nationality not in the map
    SELECT string_agg(DISTINCT nationality, ', ') INTO unmapped
    FROM drivers_clean WHERE home_country IS NULL;
    IF unmapped IS NOT NULL THEN
        RAISE EXCEPTION 'drivers_clean: unmapped nationality: %', unmapped;
    END IF;
    IF EXISTS (SELECT 1 FROM drivers_clean WHERE code_filled IS NULL) THEN
        RAISE EXCEPTION 'drivers_clean: driver without a code';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 4.11.2, 4.15 circuits_clean
-- ---------------------------------------------------------------------------
-- The data writes the US both as 'USA' and 'United States'. One spelling,
-- 'USA' (as nationality_country uses), so US home races are not missed.

CREATE TABLE circuits_clean AS
SELECT circuitId, circuitRef, name, location,
       CASE WHEN country = 'United States' THEN 'USA' ELSE country END AS country,
       lat, lng, alt, url
FROM circuits
ORDER BY circuitId;

ALTER TABLE circuits_clean ADD PRIMARY KEY (circuitId);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM circuits_clean WHERE country = 'United States') THEN
        RAISE EXCEPTION 'circuits_clean: ''United States'' left';
    END IF;
    IF EXISTS (SELECT 1 FROM circuits_clean
               WHERE lat NOT BETWEEN -90 AND 90 OR lng NOT BETWEEN -180 AND 180) THEN
        RAISE EXCEPTION 'circuits_clean: coordinates out of range';
    END IF;
    IF EXISTS (SELECT 1 FROM races_clean r
               WHERE NOT EXISTS (SELECT 1 FROM circuits_clean c WHERE c.circuitId = r.circuitId)) THEN
        RAISE EXCEPTION 'circuits_clean: a race has no circuit';
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
        ('pit_stops_clean',  11371,  8),
        ('results_clean',    24686, 22),
        ('drivers_clean',      861, 13),
        ('circuits_clean',      77,  9)
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
