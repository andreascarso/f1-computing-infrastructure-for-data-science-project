-- Export the SQL feature table in the shared output format (README, "Output
-- equivalence check"), so bench/compare_outputs.py can diff it against pandas.
--
-- Run after sql/features.sql, from the repo root (-q: only the CSV on stdout):
--   docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < sql/export_features.sql > bench/outputs/sql.csv
--
-- Format: raceId, driverId, then the features in canonical order (only the
-- ones built so far); sorted by (raceId, driverId); every feature with
-- exactly 6 decimals, flags and counts too; NULL as an empty field (what
-- COPY ... CSV writes anyway). Aliases are quoted, because Postgres
-- lowercases unquoted names (raceId would become raceid).
--   integers:  x::numeric(20,6)       ->  18.000000
--   decimals:  round(x::numeric, 6)   ->  1.234568

\copy (SELECT raceId AS "raceId", driverId AS "driverId", grid_filled::numeric(20,6) AS grid_filled, grid_zero_flag::numeric(20,6) AS grid_zero_flag, field_size::numeric(20,6) AS field_size, quali_position_filled::numeric(20,6) AS quali_position_filled, has_qualifying::numeric(20,6) AS has_qualifying, round(quali_gap_pct::numeric, 6) AS quali_gap_pct FROM features_grid ORDER BY "raceId", "driverId") TO STDOUT WITH (FORMAT csv, HEADER)
