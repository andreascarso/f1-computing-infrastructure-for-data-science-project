# R contribution: Issue #25

This port loads all 14 raw CSVs with `data.table`, cleans the inputs required
for driver/constructor form (6.4), computes seven form features and answers Q1
(5.1). R never reads Python features as calculation inputs.

## Files

- `load.R`: existing loader, adapted to support sourcing without execution,
  optional quiet output, a data-directory argument and missing-file checks.
  Its missing-value handling and standalone report are preserved.
- `clean.R`: copied inputs, qualifying-key checks, shared-drive selection,
  non-starter/Indianapolis exclusions, podium target and race dates.
- `features.R`: chronological base table, grouped historical features,
  source-date leakage checks and canonical feature CSV writer.
- `questions.R`: front-row conversion by grid/qualifying, eligibility in both
  eras, tidy results and correlation of average ranks.
- `run.R`: runner, independent timings, repetition, thread control and exports.
- `validate.R`: synthetic edge cases and unrounded diagnostics for validation.
- `validate.py`: executes the unchanged Python reference and comparator;
  checks cleaning choices, loader missing counts by column, feature missing
  masks/values, three source-date columns, Python A/B and Q1 outputs.

## Run from the repository root

Requires R and `data.table` (tested with R 4.6.1, data.table 1.18.6.1).
No additional R packages are required. If needed, install `data.table` into your
own R library using `install.packages("data.table")`.

```sh
Rscript impl/r/load.R data/
Rscript impl/r/run.R data/ --out bench/outputs/r.csv --q1-out bench/outputs/r_q1.csv --repeat 3 --threads 1
Rscript impl/r/validate.R
```

The runner defaults to one data.table thread, making the choice explicit and
repeatable. Use `--threads N` to change it. Form and Q1 times are medians over
`--repeat N`; loading, required cleaning and the base join/sort each run once.
Form timing includes copying its base input, sorting, grouping and team joins.
Checks, file writes, source loading and process startup are outside those step
timings. `proc.time()[["elapsed"]]` has coarse resolution for small operations.

Python validation needs Python >=3.11 with pandas and numpy. An existing local
Anaconda environment was usable without any environment changes:
Python 3.14.6, pandas 3.0.3, numpy 2.4.6. `/usr/bin/python3` was 3.9.6 and was
not used for reference validation. These versions differ from the repository's
pins; equivalence below applies to the tested environment and unchanged source.

```sh
/opt/anaconda3/bin/python3 impl/r/validate.py data/ --out bench/outputs/r-validation
/opt/anaconda3/bin/python3 bench/scale_data.py data/ --sets 25pct x10
/opt/anaconda3/bin/python3 impl/r/validate.py data/scaled/25pct --out bench/outputs/r-validation-25pct
/opt/anaconda3/bin/python3 impl/r/validate.py data/scaled/x10 --out bench/outputs/r-validation-x10
```

On another machine, replace `/opt/anaconda3/bin/python3` with an interpreter
meeting the dependencies. All reference files, R CSVs and diagnostic files go
under ignored `bench/outputs/`; scaled inputs go under ignored `data/scaled/`.
The validator exits nonzero if a comparison fails.

The validator calls this existing comparison script at both default and zero
tolerance, using Python's full 22-feature output and R's seven-feature output:

```sh
/opt/anaconda3/bin/python3 bench/compare_outputs.py bench/outputs/r-validation/pandas.csv bench/outputs/r-validation/r.csv
/opt/anaconda3/bin/python3 bench/compare_outputs.py bench/outputs/r-validation/pandas.csv bench/outputs/r-validation/r.csv --tol 0
```

This actually tests partial-output support: the comparator reports 15 Python-only
columns, compares the seven common features, and returns MATCH. It also checks
canonical columns, integer keys, unique sorted rows, empty missing fields and
exactly six decimal places. Q1 has a separate schema and is compared separately.

## Explain the calculations

Cleaning first chooses one car for each driver/race. A unique match to the car
number and constructor in qualifying wins; otherwise the earliest original CSV
entry wins. The finish result never influences this choice. Only then do we
remove `Did not qualify`, `Did not prequalify`, `Withdrew`, and Indianapolis 500.
The podium target is `positionOrder <= 3`.

All histories follow `(date, raceId, driverId)` order. Grouped `shift()` moves
results down one row, so a race sees only previous results. `frollmean()` uses
adaptive widths up to five, with missing values excluded: this reproduces
pandas `rolling(5, min_periods=1)` even for groups with fewer than five rows.
Cumulative sums and nonmissing counts reproduce expanding historical means.
A first appearance has zero previous races and missing historical means. Histories
continue across seasons, with circuit history grouped by driver and circuit.

For constructor form, first calculate each team's podium count and mean finish
for each race. Then average those race-level quantities over previous races.
`cons_podiums_last5` is a mean of podium counts, despite its name. Averaging
race-level finishes gives each race equal weight even when team sizes differ.

The immediately preceding driver, driver/circuit and constructor source dates
must all be strictly earlier than the current date. With chronological sorting,
this also establishes that every older observation is earlier. Same-date history
within a group fails the check rather than silently weakening the leakage rule.

Q1 is a descriptive analysis of completed races: its outcome is the podium
result. Both front-row definitions mean position 1 or 2. Missing qualifying
positions are not front row. Following Python, the filter is `year >= 1994`,
with years >=2014 placed in the second era (there is no separate upper-year
filter). Keep circuits with at least ten front-row starts in each era, compute
podiums/starts * 100, and correlate average circuit ranks between eras.

## Validation results

| Dataset | Form rows | Q1 rows | Default comparison | Zero tolerance |
|---|---:|---:|---|---|
| Original | 24,686 | 58 | MATCH | MATCH |
| 25pct | 5,900 | 0 | MATCH | MATCH |
| x10 | 246,860 | 84 | MATCH | MATCH |

Unrounded feature values and Q1 rates/correlations agree within absolute
`1e-12`; missing masks, selected result IDs, selection methods and source dates
agree. Python form versions A and B also agree exactly on the seven features.
The original cleaning counts are 26,759 → 26,668 → 25,067 → 24,686.
Original Q1 rank correlations are -0.06588619 (grid) and 0.19756423 (qualifying).
At 25pct no circuit reaches the Q1 threshold in both eras, so both correlations
are missing. At x10 more circuits qualify because starts increase tenfold.

Synthetic checks cover short and missing windows, the sixth previous result
falling out of the window, career/circuit history, season continuity, unequal
team sizes, current/future result mutations, strict date rejection, duplicate
selection (unique/multiple/no qualifying matches), raw-input preservation,
missing qualifying, Q1 thresholds, tied ranks and undefined correlations.

Illustrative observed elapsed seconds (single thread; not a speed claim):

| Step | Original | 25pct | x10 |
|---|---:|---:|---:|
| Load 14 tables | 0.170 | 0.036 | 1.406 |
| Required cleaning | 0.059 | 0.025 | 0.274 |
| Base join/sort | 0.017 | 0.004 | 0.018 |
| Form, median of 3 | 0.107 | 0.059 | 1.066 |
| Q1, median of 3 | 0.007 | 0.005 | 0.038 |

Timings vary with machine load and include R interpretation/compilation effects.
The x10 and 25pct validation jobs were run concurrently for this observation.
Python cleaning covers all tables; comparing its total cleaning time directly
with this focused cleaning step would be misleading.

## Scope and limitations

This implements seven form features, not all 22 model features, model splits,
Q2 or Q3. Only transformations consumed by 6.4/Q1 are ported; unrelated duration,
session-time and nationality cleaning is omitted. Required race/circuit names
in the tested inputs contain no HTML entities; this port rejects recognized
entities there rather than silently producing different names. Full generic
HTML decoding is not implemented. Original, 25pct and x10 were tested; 50pct and
x50 were not. Python emits a mixed-type warning for a `positionText` column at
x10; that column does not affect these outputs. No global packages were changed.
