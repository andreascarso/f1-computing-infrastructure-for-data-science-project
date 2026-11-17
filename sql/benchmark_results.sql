-- Results of bench/benchmark.py (issue #21), read by the Streamlit app (#23).
--
-- One row per measured number, in long format: every implementation reports a
-- different subset of steps, and a chart can filter by kind and step.
-- bench/benchmark.py creates the table if it is missing; schema.sql does not
-- drop it, so results survive reloading the data.
--
-- kind:
--   startup   a no-op process in the same container (interpreter, libraries, docker exec)
--   parse     reading the CSVs (pandas read_csv, SQL COPY, ...)
--   compute   cleaning, features, questions; these add up to the compute total
--   detail    part of a compute step, not added again (form is part of features)
--   total     wall clock of all processes of one repetition, measured from outside
--   memory    peak memory of a container during a repetition (docker stats)
--   storage   size on disk (CSV files, tables, pg_database_size())
--
-- value is the median over `runs` repetitions; min_value / max_value show the spread.

CREATE TABLE IF NOT EXISTS benchmark_results (
    id             BIGSERIAL        PRIMARY KEY,
    run_id         TEXT             NOT NULL,   -- one harness call, e.g. 2026-11-17T21:04:11
    measured_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),
    machine        TEXT             NOT NULL,   -- CPU and Docker resources
    dataset        TEXT             NOT NULL,   -- data, 25pct, 50pct, x10, x50
    implementation TEXT             NOT NULL,   -- pandas, pandas-lambda, sql, ...
    step           TEXT             NOT NULL,   -- load, clean, features, form, q1, ...
    kind           TEXT             NOT NULL
        CHECK (kind IN ('startup', 'parse', 'compute', 'detail', 'total', 'memory', 'storage')),
    unit           TEXT             NOT NULL CHECK (unit IN ('s', 'MiB', 'bytes')),
    value          DOUBLE PRECISION NOT NULL,
    min_value      DOUBLE PRECISION,
    max_value      DOUBLE PRECISION,
    runs           INTEGER          NOT NULL,
    container      TEXT,                        -- where it ran / whose memory: bench, db, loader
    output_checked BOOLEAN          NOT NULL    -- feature table matched pandas (bench/compare_outputs.py)
);
