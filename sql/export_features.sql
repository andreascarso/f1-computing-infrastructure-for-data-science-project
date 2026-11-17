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
--
-- \copy must be one line, so the query is a temp view written over several.

CREATE TEMP VIEW export_features AS
SELECT g.raceId AS "raceId", g.driverId AS "driverId",
       -- 6.2 grid
       g.grid_filled::numeric(20,6)           AS grid_filled,
       g.grid_zero_flag::numeric(20,6)        AS grid_zero_flag,
       g.field_size::numeric(20,6)            AS field_size,
       -- 6.3 qualifying
       g.quali_position_filled::numeric(20,6) AS quali_position_filled,
       g.has_qualifying::numeric(20,6)        AS has_qualifying,
       round(g.quali_gap_pct::numeric, 6)     AS quali_gap_pct,
       -- 6.4 driver and constructor form
       f.drv_prev_races::numeric(20,6)        AS drv_prev_races,
       round(f.drv_podium_rate_last5, 6)      AS drv_podium_rate_last5,
       round(f.drv_avg_finish_last5, 6)       AS drv_avg_finish_last5,
       round(f.drv_career_podium_rate, 6)     AS drv_career_podium_rate,
       round(f.drv_circuit_podium_rate, 6)    AS drv_circuit_podium_rate,
       round(f.cons_podiums_last5, 6)         AS cons_podiums_last5,
       round(f.cons_avg_finish_last5, 6)      AS cons_avg_finish_last5
FROM features_grid g
JOIN features_form f USING (raceId, driverId);

\copy (SELECT * FROM export_features ORDER BY "raceId", "driverId") TO STDOUT WITH (FORMAT csv, HEADER)
