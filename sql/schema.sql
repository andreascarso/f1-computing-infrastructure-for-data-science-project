-- Schema for the 14 raw F1 tables (one table per CSV in data/).
--
-- Columns are in the same order as the CSV headers, so COPY ... HEADER loads them by position.
-- Names are the CSV names left unquoted: Postgres stores raceId as raceid, and queries can
-- still write raceId. Values are kept raw (lap times as text, durations in ms); conversions
-- happen in the cleaning step. Keys follow notebook Sections 3.2 (PKs) and 3.3 (23 FKs).
--
-- Safe to re-run: every table is dropped first, which gives an empty database again.

DROP TABLE IF EXISTS
    pit_stops, lap_times, driver_standings, constructor_standings, constructor_results,
    qualifying, sprint_results, results, races,
    constructors, drivers, status, circuits, seasons
CASCADE;

-- Parent tables (no foreign keys)

CREATE TABLE seasons (
    year INTEGER PRIMARY KEY,
    url  TEXT    NOT NULL
);

CREATE TABLE circuits (
    circuitId  INTEGER PRIMARY KEY,
    circuitRef TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    location   TEXT    NOT NULL,
    country    TEXT    NOT NULL,
    lat        NUMERIC NOT NULL,
    lng        NUMERIC NOT NULL,
    alt        INTEGER NOT NULL,
    url        TEXT    NOT NULL
);

CREATE TABLE status (
    statusId INTEGER PRIMARY KEY,
    status   TEXT    NOT NULL
);

CREATE TABLE drivers (
    driverId    INTEGER PRIMARY KEY,
    driverRef   TEXT    NOT NULL,
    number      INTEGER,            -- permanent numbers only exist since 2014
    code        TEXT,               -- three-letter code, missing for older drivers
    forename    TEXT    NOT NULL,
    surname     TEXT    NOT NULL,
    dob         DATE    NOT NULL,
    nationality TEXT    NOT NULL,
    url         TEXT    NOT NULL
);

CREATE TABLE constructors (
    constructorId  INTEGER PRIMARY KEY,
    constructorRef TEXT    NOT NULL,
    name           TEXT    NOT NULL,
    nationality    TEXT    NOT NULL,
    url            TEXT    NOT NULL
);

-- Races (depends on seasons and circuits)

CREATE TABLE races (
    raceId      INTEGER PRIMARY KEY,
    year        INTEGER NOT NULL REFERENCES seasons (year),
    round       INTEGER NOT NULL,
    circuitId   INTEGER NOT NULL REFERENCES circuits (circuitId),
    name        TEXT    NOT NULL,
    date        DATE    NOT NULL,
    time        TIME,               -- start times are only recorded for recent seasons
    url         TEXT    NOT NULL,
    fp1_date    DATE,               -- session dates/times exist only from 2021
    fp1_time    TIME,
    fp2_date    DATE,
    fp2_time    TIME,
    fp3_date    DATE,
    fp3_time    TIME,
    quali_date  DATE,
    quali_time  TIME,
    sprint_date DATE,
    sprint_time TIME
);

-- Race-level tables (depend on races, drivers, constructors, status)

CREATE TABLE results (
    resultId        INTEGER PRIMARY KEY,
    raceId          INTEGER NOT NULL REFERENCES races (raceId),
    driverId        INTEGER NOT NULL REFERENCES drivers (driverId),
    constructorId   INTEGER NOT NULL REFERENCES constructors (constructorId),
    number          INTEGER,
    grid            INTEGER NOT NULL,
    position        INTEGER,        -- NULL when the driver was not classified
    positionText    TEXT    NOT NULL,  -- position or a letter (R, D, E, W, F, N)
    positionOrder   INTEGER NOT NULL,
    points          NUMERIC NOT NULL,  -- half points exist
    laps            INTEGER NOT NULL,
    time            TEXT,           -- winner's time or gap such as "+5.478"
    milliseconds    INTEGER,
    fastestLap      INTEGER,
    rank            INTEGER,
    fastestLapTime  TEXT,           -- "m:ss.sss"
    fastestLapSpeed NUMERIC,
    statusId        INTEGER NOT NULL REFERENCES status (statusId)
);

CREATE TABLE sprint_results (
    resultId       INTEGER PRIMARY KEY,
    raceId         INTEGER NOT NULL REFERENCES races (raceId),
    driverId       INTEGER NOT NULL REFERENCES drivers (driverId),
    constructorId  INTEGER NOT NULL REFERENCES constructors (constructorId),
    number         INTEGER NOT NULL,
    grid           INTEGER NOT NULL,
    position       INTEGER,
    positionText   TEXT    NOT NULL,
    positionOrder  INTEGER NOT NULL,
    points         NUMERIC NOT NULL,
    laps           INTEGER NOT NULL,
    time           TEXT,
    milliseconds   INTEGER,
    fastestLap     INTEGER,
    fastestLapTime TEXT,
    statusId       INTEGER NOT NULL REFERENCES status (statusId)
);

CREATE TABLE qualifying (
    qualifyId     INTEGER PRIMARY KEY,
    raceId        INTEGER NOT NULL REFERENCES races (raceId),
    driverId      INTEGER NOT NULL REFERENCES drivers (driverId),
    constructorId INTEGER NOT NULL REFERENCES constructors (constructorId),
    number        INTEGER NOT NULL,
    position      INTEGER NOT NULL,
    q1            TEXT,             -- "m:ss.sss"; can also be NULL or an empty string
    q2            TEXT,
    q3            TEXT
);

CREATE TABLE constructor_results (
    constructorResultsId INTEGER PRIMARY KEY,
    raceId               INTEGER NOT NULL REFERENCES races (raceId),
    constructorId        INTEGER NOT NULL REFERENCES constructors (constructorId),
    points               NUMERIC NOT NULL,
    status               TEXT               -- only 'D' (disqualified) or NULL
);

CREATE TABLE constructor_standings (
    constructorStandingsId INTEGER PRIMARY KEY,
    raceId                 INTEGER NOT NULL REFERENCES races (raceId),
    constructorId          INTEGER NOT NULL REFERENCES constructors (constructorId),
    points                 NUMERIC NOT NULL,
    position               INTEGER NOT NULL,
    positionText           TEXT    NOT NULL,
    wins                   INTEGER NOT NULL
);

CREATE TABLE driver_standings (
    driverStandingsId INTEGER PRIMARY KEY,
    raceId            INTEGER NOT NULL REFERENCES races (raceId),
    driverId          INTEGER NOT NULL REFERENCES drivers (driverId),
    points            NUMERIC NOT NULL,
    position          INTEGER NOT NULL,
    positionText      TEXT    NOT NULL,
    wins              INTEGER NOT NULL
);

-- Lap-level tables: no id column, so the key is composite

CREATE TABLE lap_times (
    raceId       INTEGER NOT NULL REFERENCES races (raceId),
    driverId     INTEGER NOT NULL REFERENCES drivers (driverId),
    lap          INTEGER NOT NULL,
    position     INTEGER NOT NULL,
    time         TEXT    NOT NULL,  -- "m:ss.sss"
    milliseconds INTEGER NOT NULL,
    PRIMARY KEY (raceId, driverId, lap)
);

CREATE TABLE pit_stops (
    raceId       INTEGER NOT NULL REFERENCES races (raceId),
    driverId     INTEGER NOT NULL REFERENCES drivers (driverId),
    stop         INTEGER NOT NULL,
    lap          INTEGER NOT NULL,
    time         TIME    NOT NULL,  -- time of day of the stop
    duration     TEXT    NOT NULL,  -- "ss.sss", or "m:ss.sss" for red-flag stops
    milliseconds INTEGER NOT NULL,
    PRIMARY KEY (raceId, driverId, stop)
);
