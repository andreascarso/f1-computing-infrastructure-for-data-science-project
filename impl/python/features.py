"""Build the model features with pandas in two versions and time both.

Script version of notebook Section 6.1-6.6. Both versions build the same
feature table; they differ only in the driver/constructor form features (6.4):

    A  "lambda":     groupby().transform(lambda s: s.shift(1).rolling(5).mean())
                     as in the notebook, one Python call per driver/team
    B  "vectorised": groupby().shift(1), then groupby().rolling() / .expanding()
                     on the shifted column, no Python lambdas

Everything else (base table, grid, qualifying, standings, context) is shared
code, so a timing difference between A and B comes from 6.4 alone.

Usage:
    python impl/python/features.py data/
    python impl/python/features.py data/ --repeat 3   # median of 3 runs per version
    python impl/python/features.py data/ --check      # A == B, leakage check, notebook numbers
    python impl/python/features.py data/ --out bench/outputs/pandas.csv   # for bench/compare_outputs.py
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from clean import clean_tables
from load import TABLE_NAMES, load_tables

VERSIONS = {"A": "lambda", "B": "vectorised"}

# 6.1 robustness split: five 15-season eras, shuffled with a fixed seed
SEED = 42
ERA_STARTS = [1950, 1965, 1980, 1995, 2010]
EXPECTED_VAL_SEASONS = [1951, 1962, 1971, 1976, 1992, 1993, 1999, 2009, 2017, 2019]
EXPECTED_TEST_SEASONS = [1958, 1963, 1974, 1979, 1980, 1983, 1998, 2006, 2015, 2022]

# 6.3 Q1 gaps above this are not a competitive pace measurement
MAX_QUALI_GAP_PCT = 30

# 6.4 rolling window for "last 5 races"
FORM_WINDOW = 5

# 6.7 feature groups (used by the ablation in Section 9)
FEATURE_GROUPS = {
    "grid": ["grid_filled", "grid_zero_flag", "field_size"],
    "qualifying": ["quali_position_filled", "has_qualifying", "quali_gap_pct"],
    "driver_form": ["drv_prev_races", "drv_podium_rate_last5", "drv_avg_finish_last5",
                    "drv_career_podium_rate", "drv_circuit_podium_rate"],
    "constructor_form": ["cons_podiums_last5", "cons_avg_finish_last5"],
    "standings": ["drv_pre_points_share", "drv_pre_wins", "drv_pre_position",
                  "cons_pre_points_share", "cons_pre_position"],
    "context": ["driver_age", "home_race", "round", "hybrid_era"],
}
# Order is the canonical column order of the output format; bench/compare_outputs.py
# holds the same list (FEATURE_COLUMNS), keep the two in sync.
FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]
TARGET = "podium"

# Output format shared by all implementations (README, "Output equivalence check")
OUTPUT_KEYS = ["raceId", "driverId"]

SOURCE_DATE_COLUMNS = ["drv_form_source_date", "cons_form_source_date",
                       "drv_standings_source_date", "cons_standings_source_date"]


# ---------------------------------------------------------------------------
# 6.1 Base table and data split (shared)
# ---------------------------------------------------------------------------

def era_seasons():
    """6.1: train/validation/test seasons of the by-era robustness split."""
    rng = np.random.default_rng(SEED)
    train, val, test = [], [], []
    for start in ERA_STARTS:
        seasons = rng.permutation(np.arange(start, start + 15))
        train += seasons[:11].tolist()
        val += seasons[11:13].tolist()
        test += seasons[13:].tolist()
    return sorted(train), sorted(val), sorted(test)


def base_table(clean):
    """6.1: one row per driver per race, sorted by date, with both splits."""
    results = clean["results"]
    df = (
        results[["resultId", "raceId", "driverId", "constructorId",
                 "grid", "positionOrder", "podium"]]
        .merge(clean["races"][["raceId", "year", "round", "date", "circuitId"]],
               on="raceId", how="left", validate="many_to_one")
        .sort_values(["date", "raceId", "driverId"])
        .reset_index(drop=True)
    )

    # Shared-drive rows chosen among several cars (4.9.4): left out of modeling later
    ambiguous_ids = results.loc[results["selection_method"] != "single_record", "resultId"]
    df["ambiguous_shared_drive"] = df["resultId"].isin(ambiguous_ids).astype(int)

    # Main split: chronological
    df["split"] = np.select(
        [df["year"] <= 2019, df["year"] <= 2021],
        ["train", "validation"], default="test",
    )

    # Robustness split: whole seasons, shuffled inside each era
    train, val, test = era_seasons()
    assert val == EXPECTED_VAL_SEASONS and test == EXPECTED_TEST_SEASONS
    assert set(train) | set(val) | set(test) == set(df["year"])
    season_to_split = {**{y: "train" for y in train},
                       **{y: "validation" for y in val},
                       **{y: "test" for y in test}}
    df["era_split"] = df["year"].map(season_to_split)

    assert len(df) == len(results)
    assert not df.duplicated(["raceId", "driverId"]).any()
    return df


# ---------------------------------------------------------------------------
# 6.2 Grid and 6.3 qualifying features (shared)
# ---------------------------------------------------------------------------

def add_grid_features(df):
    """6.2: field size, grid-0 flag, grid with grid 0 placed at the back (assumption)."""
    df["field_size"] = df.groupby("raceId")["driverId"].transform("size")
    df["grid_zero_flag"] = (df["grid"] == 0).astype(int)
    df["grid_filled"] = df["grid"].where(df["grid"] > 0, df["field_size"])
    return df


def add_qualifying_features(df, clean):
    """6.3: qualifying position (grid where missing) and Q1 gap to the fastest, in %."""
    quali = clean["qualifying"][["raceId", "driverId", "position", "q1_seconds"]].copy()
    fastest_q1 = quali.groupby("raceId")["q1_seconds"].transform("min")
    quali["quali_gap_pct"] = (quali["q1_seconds"] / fastest_q1 - 1) * 100
    assert (quali["quali_gap_pct"].dropna() >= 0).all()
    # Interrupted laps / recording errors: missing in this feature only
    quali.loc[quali["quali_gap_pct"] > MAX_QUALI_GAP_PCT, "quali_gap_pct"] = np.nan

    df = df.merge(
        quali[["raceId", "driverId", "position", "quali_gap_pct"]]
        .rename(columns={"position": "quali_position"}),
        on=["raceId", "driverId"], how="left", validate="one_to_one",
    )
    df["has_qualifying"] = df["quali_position"].notna().astype(int)
    df["quali_position_filled"] = df["quali_position"].fillna(df["grid_filled"])
    return df


# ---------------------------------------------------------------------------
# 6.4 Driver and constructor form: the part that differs between A and B
# ---------------------------------------------------------------------------

def _team_races(df):
    """One row per team per race (shared by both versions)."""
    return (
        df.groupby(["constructorId", "raceId"])
        .agg(date=("date", "first"), team_podiums=("podium", "sum"),
             team_avg_finish=("positionOrder", "mean"))
        .reset_index()
        .sort_values(["date", "raceId"])
    )


def _merge_team_form(df, team_races):
    return df.merge(
        team_races[["constructorId", "raceId", "cons_podiums_last5",
                    "cons_avg_finish_last5", "cons_form_source_date"]],
        on=["constructorId", "raceId"], how="left", validate="many_to_one",
    )


def add_form_features_lambda(df):
    """6.4, version A (as in the notebook): transform with a Python lambda per group."""
    # Rows are sorted by date, so shift(1) inside each group = the previous race
    by_driver = df.groupby("driverId")
    df["drv_prev_races"] = by_driver.cumcount()
    df["drv_podium_rate_last5"] = by_driver["podium"].transform(
        lambda s: s.shift(1).rolling(FORM_WINDOW, min_periods=1).mean())
    df["drv_avg_finish_last5"] = by_driver["positionOrder"].transform(
        lambda s: s.shift(1).rolling(FORM_WINDOW, min_periods=1).mean())
    df["drv_career_podium_rate"] = by_driver["podium"].transform(
        lambda s: s.shift(1).expanding().mean())
    df["drv_form_source_date"] = by_driver["date"].shift(1)

    df["drv_circuit_podium_rate"] = df.groupby(["driverId", "circuitId"])["podium"].transform(
        lambda s: s.shift(1).expanding().mean())

    team_races = _team_races(df)
    by_team = team_races.groupby("constructorId")
    team_races["cons_podiums_last5"] = by_team["team_podiums"].transform(
        lambda s: s.shift(1).rolling(FORM_WINDOW, min_periods=1).mean())
    team_races["cons_avg_finish_last5"] = by_team["team_avg_finish"].transform(
        lambda s: s.shift(1).rolling(FORM_WINDOW, min_periods=1).mean())
    team_races["cons_form_source_date"] = by_team["date"].shift(1)

    return _merge_team_form(df, team_races)


def _past_mean(values, keys, window=None):
    """Mean of the previous `window` values (all previous values if None) within each group.

    Vectorised form of transform(lambda s: s.shift(1).rolling(window).mean()):
    one grouped shift, then one grouped rolling/expanding call. groupby().rolling()
    returns the group keys as extra index levels in front; they are dropped so the
    result lines up with the original rows again.
    """
    previous = values.groupby(keys).shift(1)
    grouped = previous.groupby(keys)
    if window is None:
        result = grouped.expanding().mean()
    else:
        result = grouped.rolling(window, min_periods=1).mean()
    return result.droplevel(list(range(len(keys)))).reindex(values.index)


def add_form_features_vectorised(df):
    """6.4, version B: grouped shift + grouped rolling/expanding, no lambdas."""
    driver = [df["driverId"]]
    df["drv_prev_races"] = df.groupby("driverId").cumcount()
    df["drv_podium_rate_last5"] = _past_mean(df["podium"], driver, FORM_WINDOW)
    df["drv_avg_finish_last5"] = _past_mean(df["positionOrder"], driver, FORM_WINDOW)
    df["drv_career_podium_rate"] = _past_mean(df["podium"], driver)
    df["drv_form_source_date"] = df.groupby("driverId")["date"].shift(1)

    df["drv_circuit_podium_rate"] = _past_mean(df["podium"], [df["driverId"], df["circuitId"]])

    team_races = _team_races(df)
    team = [team_races["constructorId"]]
    team_races["cons_podiums_last5"] = _past_mean(team_races["team_podiums"], team, FORM_WINDOW)
    team_races["cons_avg_finish_last5"] = _past_mean(team_races["team_avg_finish"], team, FORM_WINDOW)
    team_races["cons_form_source_date"] = team_races.groupby("constructorId")["date"].shift(1)

    return _merge_team_form(df, team_races)


FORM_FUNCTIONS = {"A": add_form_features_lambda, "B": add_form_features_vectorised}


# ---------------------------------------------------------------------------
# 6.5 Pre-race standings and 6.6 context features (shared)
# ---------------------------------------------------------------------------

def _pre_race_standings(standings, races_clean, key, prefix):
    """Standings after the previous race of the same season, for each (raceId, key)."""
    s = standings.merge(races_clean[["raceId", "year", "date"]], on="raceId", how="left")
    s = s.sort_values(["date", "raceId"])
    season = s.groupby([key, "year"])
    s[f"{prefix}_pre_points"] = season["points"].shift(1)
    s[f"{prefix}_pre_wins"] = season["wins"].shift(1)
    s[f"{prefix}_pre_position"] = season["position"].shift(1)
    s[f"{prefix}_standings_source_date"] = season["date"].shift(1)
    # season start: nothing scored yet
    s[f"{prefix}_pre_points"] = s[f"{prefix}_pre_points"].fillna(0)
    s[f"{prefix}_pre_wins"] = s[f"{prefix}_pre_wins"].fillna(0)
    # points systems changed over the years: share of the leader's points
    leader = s.groupby("raceId")[f"{prefix}_pre_points"].transform("max")
    s[f"{prefix}_pre_points_share"] = np.where(leader > 0, s[f"{prefix}_pre_points"] / leader, 0.0)
    return s[["raceId", key, f"{prefix}_pre_points_share", f"{prefix}_pre_wins",
              f"{prefix}_pre_position", f"{prefix}_standings_source_date"]]


def add_standings_features(df, clean):
    """6.5: driver and constructor standings before the race."""
    races = clean["races"]
    df = df.merge(_pre_race_standings(clean["driver_standings"], races, "driverId", "drv"),
                  on=["raceId", "driverId"], how="left", validate="one_to_one")
    df = df.merge(_pre_race_standings(clean["constructor_standings"], races, "constructorId", "cons"),
                  on=["raceId", "constructorId"], how="left", validate="many_to_one")
    return df


def add_context_features(df, clean):
    """6.6: driver age, home race, hybrid era; sprint weekend for reference only."""
    df = df.merge(clean["drivers"][["driverId", "dob", "home_country", "home_country_2"]],
                  on="driverId", how="left", validate="many_to_one")
    df = df.merge(clean["circuits"][["circuitId", "country"]],
                  on="circuitId", how="left", validate="many_to_one")

    df["driver_age"] = (df["date"] - df["dob"]).dt.days / 365.25
    df["home_race"] = ((df["country"] == df["home_country"])
                       | (df["country"] == df["home_country_2"])).astype(int)
    df["hybrid_era"] = (df["year"] >= 2014).astype(int)
    # Not a feature (sprints only exist from 2021), kept for reference
    df["is_sprint_weekend"] = df["raceId"].isin(clean["sprint_results"]["raceId"]).astype(int)

    assert not df.columns.duplicated().any()
    assert not df.duplicated(["raceId", "driverId"]).any()
    return df


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_features(clean, version):
    """Build the full feature table with version 'A' (lambda) or 'B' (vectorised).

    Returns (model_df, timings) where timings maps step name -> seconds.
    """
    form = FORM_FUNCTIONS[version]
    timings = {}

    def timed(name, step):
        start = time.perf_counter()
        out = step()
        timings[name] = time.perf_counter() - start
        return out

    df = timed("6.1 base table + splits", lambda: base_table(clean))
    df = timed("6.2 grid", lambda: add_grid_features(df))
    df = timed("6.3 qualifying", lambda: add_qualifying_features(df, clean))
    df = timed("6.4 driver/constructor form", lambda: form(df))
    df = timed("6.5 pre-race standings", lambda: add_standings_features(df, clean))
    df = timed("6.6 context", lambda: add_context_features(df, clean))

    assert len(df) == len(clean["results"])
    return df, timings


def leakage_check(df):
    """6.7: every past-race source date is strictly before the race; no result column is a feature."""
    for col in SOURCE_DATE_COLUMNS:
        known = df[col].notna()
        assert (df.loc[known, col] < df.loc[known, "date"]).all(), col
    forbidden = {"positionOrder", "position", "points", "laps", "statusId", "milliseconds",
                 "time_seconds", "fastestLap", "rank", "podium"}
    assert not forbidden & set(FEATURES)


def write_output(df, path):
    """Write the feature table in the shared output format for bench/compare_outputs.py.

    raceId, driverId, then FEATURES; sorted by (raceId, driverId); every feature
    as a float with 6 decimals (also flags and counts); NaN as an empty field.
    """
    out = df[OUTPUT_KEYS + FEATURES].sort_values(OUTPUT_KEYS)
    out[FEATURES] = out[FEATURES].astype("float64")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, float_format="%.6f", na_rep="", lineterminator="\n")


def check_against_notebook(df):
    """Key numbers the notebook prints in Section 6, compared with the script's."""
    known = {c: int(df[c].notna().sum()) for c in SOURCE_DATE_COLUMNS}
    checks = [
        ("rows", 24686, len(df)),
        ("ambiguous shared-drive rows", 61, int(df["ambiguous_shared_drive"].sum())),
        ("train rows (split)", 22553, int((df["split"] == "train").sum())),
        ("test rows (era_split)", 3406, int((df["era_split"] == "test").sum())),
        ("grid-0 rows", 130, int(df["grid_zero_flag"].sum())),
        ("rows without qualifying", 14231, int((df["has_qualifying"] == 0).sum())),
        ("rows without quali gap", 14390, int(df["quali_gap_pct"].isna().sum())),
        ("drv form rows", 24000, known["drv_form_source_date"]),
        ("cons form rows", 24421, known["cons_form_source_date"]),
        ("drv standings rows", 21771, known["drv_standings_source_date"]),
        ("cons standings rows", 21770, known["cons_standings_source_date"]),
        ("max drv_prev_races", 401, int(df["drv_prev_races"].max())),
        ("mean drv_podium_rate_last5", 0.14, round(df["drv_podium_rate_last5"].mean(), 2)),
        ("mean drv_avg_finish_last5", 11.69, round(df["drv_avg_finish_last5"].mean(), 2)),
        ("drv_circuit_podium_rate missing %", 30.24,
         round(df["drv_circuit_podium_rate"].isna().mean() * 100, 2)),
        ("home races", 1943, int(df["home_race"].sum())),
        ("sprint-weekend rows", 358, int(df["is_sprint_weekend"].sum())),
        ("features", 22, len(FEATURES)),
    ]
    check = pd.DataFrame(checks, columns=["value", "notebook", "script"]).set_index("value")
    check["match"] = check["notebook"] == check["script"]
    return check


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path, help="folder holding the 14 CSV files")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="build each version N times and report the median (default 1)")
    parser.add_argument("--check", action="store_true",
                        help="check A == B, the leakage rule and the notebook's numbers (not timed)")
    parser.add_argument("--out", type=Path, metavar="CSV",
                        help="write the feature table (version B; A is identical, see --check) "
                             "in the shared output format (not timed)")
    args = parser.parse_args()

    missing = [n for n in TABLE_NAMES if not (args.data_dir / f"{n}.csv").is_file()]
    if missing:
        sys.exit(f"Missing CSV files in {args.data_dir}: {', '.join(missing)}")

    prep_start = time.perf_counter()
    tables, _ = load_tables(args.data_dir, verbose=False)
    clean, _ = clean_tables(tables, verbose=False)
    print(f"Loaded and cleaned {len(TABLE_NAMES)} tables in "
          f"{time.perf_counter() - prep_start:.4f} s (not part of the feature time)\n")

    # runs[version] = list of per-step timing dicts, one per repetition
    runs = {v: [] for v in VERSIONS}
    tables_out = {}
    for _ in range(args.repeat):
        for version in VERSIONS:
            tables_out[version], timings = build_features(clean, version)
            runs[version].append(timings)

    steps = list(runs["A"][0])
    median = {v: {s: statistics.median(r[s] for r in runs[v]) for s in steps} for v in VERSIONS}
    label = "median of " + str(args.repeat) if args.repeat > 1 else "1 run"

    print(f"Feature engineering, Section 6.1-6.6 ({label}):")
    print(f"{'step':<32} {'A lambda':>12} {'B vectorised':>14} {'A / B':>8}")
    for step in steps:
        a, b = median["A"][step], median["B"][step]
        print(f"{step:<32} {a:10.4f} s {b:12.4f} s {a / b:7.1f}x")
    total_a, total_b = sum(median["A"].values()), sum(median["B"].values())
    print("-" * 70)
    print(f"{'total':<32} {total_a:10.4f} s {total_b:12.4f} s {total_a / total_b:7.1f}x")

    df_a, df_b = tables_out["A"], tables_out["B"]
    print(f"\nFeature table: {df_a.shape[0]:,} rows, {df_a.shape[1]} columns, "
          f"{len(FEATURES)} model features")

    if args.out:
        write_output(df_b, args.out)
        print(f"Wrote {args.out} (output format for bench/compare_outputs.py)")

    if args.check:
        print("\nVersion A vs. B:", end=" ")
        pd.testing.assert_frame_equal(df_a, df_b, check_exact=True)
        print("identical (all columns, dtypes and values, exact)")

        leakage_check(df_a)
        print("Leakage check (6.7): every source date is before its race")

        print("\nComparison with the notebook:")
        check = check_against_notebook(df_a)
        print(check.to_string())
        bad = list(check.index[~check["match"]])
        print("Mismatches:", bad or "none")
        if bad:
            sys.exit(1)


if __name__ == "__main__":
    main()
