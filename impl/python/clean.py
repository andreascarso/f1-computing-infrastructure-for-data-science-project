"""Clean the 14 F1 tables with pandas and time each cleaning step.

Script version of notebook Section 4 (Data Cleaning). Each table gets one
clean_<table>() function with the same logic as the notebook; the inspection
cells (displays, plots, evidence-only counts) are left out, the decisions and
the assertions that guard them are kept. Raw tables are never modified: every
function works on a copy and returns the cleaned table.

Usage:
    python impl/python/clean.py data/
    python impl/python/clean.py data/ --check   # compare row counts with the notebook
"""

import argparse
import html
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from load import TABLE_NAMES, load_tables

# Shapes of the cleaned tables as printed by the notebook (Section 4), used by --check.
NOTEBOOK_SHAPES = {
    "status":                (139, 4),
    "races":                 (1125, 18),
    "qualifying":            (10494, 12),
    "results":               (24686, 22),
    "drivers":               (861, 13),
    "circuits":              (77, 9),
    "constructors":          (212, 5),
    "constructor_results":   (12625, 4),
    "sprint_results":        (360, 18),
    "driver_standings":      (34863, 7),
    "constructor_standings": (13391, 7),
    "seasons":               (75, 2),
    "lap_times":             (589081, 7),
    "pit_stops":             (11371, 8),
}

# Rows of results after each step (notebook 4.9.7 cleaning log).
NOTEBOOK_RESULTS_LOG = {
    "raw results": 26759,
    "after duplicate resolution": 26668,
    "after removing non-starters": 25067,
    "after excluding Indy 500": 24686,
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

HTML_CODE = r"&(?:amp|quot|apos|lt|gt|#\d+);"


def decode_html(df):
    """4.16.2: decode HTML codes such as '&amp;' in every text column (in place)."""
    # 'str' is pandas 3's text dtype; 'object' covers mixed columns (e.g. datetime.time)
    for col in df.select_dtypes(include=["object", "str"]).columns:
        has_code = df[col].astype(str).str.contains(HTML_CODE, regex=True)
        if has_code.any():
            df.loc[has_code, col] = df.loc[has_code, col].map(html.unescape)
        assert not df[col].astype(str).str.contains(HTML_CODE, regex=True).any()
    return df


def lap_time_to_seconds(series):
    """'1:26.572' -> 86.572; missing values stay NaN (4.3, 4.9.10, 4.12.2)."""
    return pd.to_timedelta("00:" + series.dropna().astype(str), errors="coerce").dt.total_seconds()


def clock_time(series):
    """'17:05:23' -> datetime.time; missing values stay missing (4.6.1, 4.17.1)."""
    return pd.to_datetime(series, format="%H:%M:%S", errors="coerce").dt.time


# ---------------------------------------------------------------------------
# 4.8 status
# ---------------------------------------------------------------------------

DID_NOT_START = {"Did not qualify", "Did not prequalify", "Withdrew"}

# 'Not classified' is left out on purpose: it means too little distance was
# covered to be classified, not a regulation decision, so it falls into 'other'.
REGULATION = {
    "Disqualified", "Excluded", "107% Rule", "Underweight",
    "Safety concerns", "Safety",
}

ACCIDENT = {
    "Accident", "Collision", "Collision damage", "Spun off",
    "Fatal accident", "Damage", "Debris",
}

MECHANICAL = {
    # Power unit and engine
    "Engine", "Engine fire", "Engine misfire", "Power Unit", "ERS",
    "Turbo", "Supercharger", "Power loss", "Ignition", "Spark plugs",
    "Magneto", "Distributor", "Injection", "Alternator", "Battery",
    "Electrical", "Electronics", "Crankshaft", "Overheating",
    "Cooling system", "Radiator", "Exhaust",
    # Fluids
    "Oil leak", "Oil pressure", "Oil pump", "Oil line", "Oil pipe",
    "Water leak", "Water pressure", "Water pump", "Water pipe",
    "Fuel system", "Fuel pressure", "Fuel pump", "Fuel leak",
    "Fuel pipe", "Fuel", "Hydraulics", "Pneumatics",
    # Drivetrain
    "Gearbox", "Transmission", "Clutch", "Differential", "Driveshaft",
    "Halfshaft", "Drivetrain", "CV joint", "Axle", "Launch control",
    # Chassis, suspension, brakes, wheels
    "Suspension", "Track rod", "Steering", "Brakes", "Brake duct",
    "Wheel bearing", "Wheel rim", "Chassis", "Undertray", "Rear wing",
    "Throttle", "Vibrations", "Heat shield fire", "Fire",
    "Driver Seat", "Seat", "Safety belt",
    # Generic failures
    "Mechanical", "Technical",
}


def status_category(name):
    """Map one status text to finished / did_not_start / regulation / accident / mechanical / other."""
    if name == "Finished" or re.fullmatch(r"\+\d+ Laps?", name):
        return "finished"
    if name in DID_NOT_START:
        return "did_not_start"
    if name in REGULATION:
        return "regulation"
    if name in ACCIDENT:
        return "accident"
    if name in MECHANICAL:
        return "mechanical"
    return "other"


def clean_status(status):
    """4.8.1: add `category` and `is_mechanical` from one explicit mapping."""
    df = status.copy()
    df["category"] = df["status"].apply(status_category)
    df["is_mechanical"] = df["category"] == "mechanical"

    # Every listed name must exist in the data (catches spelling mismatches)
    listed = DID_NOT_START | REGULATION | ACCIDENT | MECHANICAL
    assert not (listed - set(df["status"])), sorted(listed - set(df["status"]))
    assert df["category"].notna().all()
    assert len(df) == len(status)
    return decode_html(df)


# ---------------------------------------------------------------------------
# 4.2, 4.6 races
# ---------------------------------------------------------------------------

SESSION_DATE_COLUMNS = ["fp1_date", "fp2_date", "fp3_date", "quali_date", "sprint_date"]
SESSION_TIME_COLUMNS = ["time", "fp1_time", "fp2_time", "fp3_time", "quali_time", "sprint_time"]


def clean_races(races):
    """4.2 + 4.6.1: race and session dates to datetime, clock times to time.

    Schedule NaNs are structurally missing and kept (decision in 4.6.2).
    """
    df = races.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    assert df["date"].notna().all()

    before = df[SESSION_DATE_COLUMNS + SESSION_TIME_COLUMNS].notna().sum()
    for col in SESSION_DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in SESSION_TIME_COLUMNS:
        df[col] = clock_time(df[col])
    after = df[SESSION_DATE_COLUMNS + SESSION_TIME_COLUMNS].notna().sum()
    assert (before == after).all(), "date/time conversion lost values"

    # 4.6.3 consistency checks
    assert (df["date"].dt.year == df["year"]).all()
    for col in SESSION_DATE_COLUMNS:
        assert not (df[col].notna() & (df[col] > df["date"])).any()
    assert len(df) == len(races)
    return decode_html(df)


# ---------------------------------------------------------------------------
# 4.3, 4.7 qualifying
# ---------------------------------------------------------------------------

def clean_qualifying(qualifying, races_clean):
    """4.3 + 4.7.2: Q1-Q3 times to seconds, then label the missing session strings.

    Only the string columns get labels; the *_seconds columns keep NaN on purpose.
    """
    df = qualifying.copy()

    for col in ["q1", "q2", "q3"]:
        df[col + "_seconds"] = lap_time_to_seconds(df[col])
        assert not (df[col].notna() & df[col + "_seconds"].isna()).any()

    has_q1 = df["q1_seconds"].notna()
    has_q2 = df["q2_seconds"].notna()
    has_q3 = df["q3_seconds"].notna()

    q2_only_end = has_q1 & has_q2 & ~has_q3
    q1_only_end = has_q1 & ~has_q2 & ~has_q3
    no_times = ~has_q1 & ~has_q2 & ~has_q3

    # Knockout format (Q1/Q2/Q3) only exists from 2006
    year = df["raceId"].map(races_clean.set_index("raceId")["year"])
    knockout_era = year >= 2006

    df.loc[q2_only_end & knockout_era, "q3"] = "Not Recorded After Q2"
    df.loc[q1_only_end & knockout_era, ["q2", "q3"]] = "Not Recorded After Q1"
    df.loc[has_q1 & ~has_q2 & ~knockout_era, "q2"] = "Not Recorded (Historical Format)"
    df.loc[has_q1 & ~has_q3 & ~knockout_era, "q3"] = "Not Recorded (Historical Format)"
    df.loc[no_times, ["q1", "q2", "q3"]] = "No Qualifying Time Recorded"

    assert df[["q1", "q2", "q3"]].notna().all().all()
    assert not df.duplicated(["raceId", "driverId"]).any()
    assert len(df) == len(qualifying)
    return decode_html(df)


# ---------------------------------------------------------------------------
# 4.9 results (the modeling table)
# ---------------------------------------------------------------------------

def clean_results(results, races_clean, qualifying_clean, status_clean, log=None):
    """4.9: one row per driver per race, starters only, no Indy 500, plus `podium`.

    Steps: 4.9.4 resolve duplicate (raceId, driverId) rows without using the race
    result, 4.9.7 remove non-starters and the Indianapolis 500, 4.9.10 durations
    to seconds, 4.9.11 target. If `log` is a dict, the row count after each step
    is written into it.
    """
    keys = ["raceId", "driverId"]
    rows = {"raw results": len(results)}

    # 4.9.4 Keep one row per driver per race: a unique qualifying match
    # (car number + constructor) first, otherwise the original (earliest) entry.
    work = results.copy().reset_index(drop=True)
    work["_original_order"] = np.arange(len(work))
    work["_group_size"] = work.groupby(keys)["resultId"].transform("size")

    qual_ref = qualifying_clean[keys + ["number", "constructorId"]].rename(columns={
        "number": "qual_number",
        "constructorId": "qual_constructorId",
    })
    assert not qual_ref.duplicated(keys).any()
    work = work.merge(qual_ref, on=keys, how="left", validate="many_to_one")

    work["_car_match"] = (
        work["number"].eq(work["qual_number"])
        & work["constructorId"].eq(work["qual_constructorId"])
    )
    match_count = work.groupby(keys)["_car_match"].transform("sum")
    work["_preferred"] = work["_car_match"] & match_count.eq(1)

    ranked = work.sort_values(
        ["raceId", "driverId", "_preferred", "_original_order"],
        ascending=[True, True, False, True],
        kind="stable",
    )
    selected = ranked.loc[~ranked.duplicated(keys, keep="first")].copy()
    selected["selection_method"] = np.select(
        [selected["_group_size"].eq(1), selected["_preferred"]],
        ["single_record", "unique_qualifying_match"],
        default="original_entry_fallback",
    )
    df = (
        selected.drop(columns=["_original_order", "_group_size", "qual_number",
                               "qual_constructorId", "_car_match", "_preferred"])
        .sort_values(keys)
        .reset_index(drop=True)
    )
    assert not df.duplicated(keys).any()
    rows["after duplicate resolution"] = len(df)

    # 4.9.7 Remove non-starters (by status category) ...
    non_starter_ids = status_clean.loc[status_clean["category"] == "did_not_start", "statusId"]
    df = df[~df["statusId"].isin(non_starter_ids)].copy()
    rows["after removing non-starters"] = len(df)

    # ... and the Indianapolis 500 (1950-1960, separate field)
    indy_race_ids = races_clean.loc[races_clean["name"] == "Indianapolis 500", "raceId"]
    df = df[~df["raceId"].isin(indy_race_ids)].copy()
    rows["after excluding Indy 500"] = len(df)

    assert not df["statusId"].isin(non_starter_ids).any()
    assert not df["raceId"].isin(indy_race_ids).any()
    assert df["number"].notna().all()   # the 6 rows without a car number were non-starters

    # 4.9.8 valid ranges
    assert (df[["points", "laps", "grid"]] >= 0).all().all()
    assert (df["positionOrder"] >= 1).all()

    # 4.9.10 Durations to seconds (remaining NaNs are post-race columns, kept: 4.9.9)
    df["time_seconds"] = df["milliseconds"] / 1000
    df["fastestLapTime_seconds"] = lap_time_to_seconds(df["fastestLapTime"])
    assert not (df["fastestLapTime"].notna() & df["fastestLapTime_seconds"].isna()).any()

    # 4.9.11 Target, created only after the row choice and population are fixed
    df["podium"] = (df["positionOrder"] <= 3).astype(int)
    assert df["podium"].isin([0, 1]).all()

    if log is not None:
        log.update(rows)
    return decode_html(df)


# ---------------------------------------------------------------------------
# 4.11 drivers, 4.15 circuits, 4.16 constructors, 4.10 constructor_results
# ---------------------------------------------------------------------------

# Nationality -> country, written like circuits.country (e.g. 'UK', 'USA'), 4.11.2
NATIONALITY_TO_COUNTRY = {
    "American": "USA",
    "American-Italian": "USA",
    "Argentine": "Argentina",
    "Argentinian": "Argentina",
    "Argentine-Italian": "Argentina",
    "Australian": "Australia",
    "Austrian": "Austria",
    "Belgian": "Belgium",
    "Brazilian": "Brazil",
    "British": "UK",
    "Canadian": "Canada",
    "Chilean": "Chile",
    "Chinese": "China",
    "Colombian": "Colombia",
    "Czech": "Czech Republic",
    "Danish": "Denmark",
    "Dutch": "Netherlands",
    "East German": "Germany",
    "Finnish": "Finland",
    "French": "France",
    "German": "Germany",
    "Hong Kong": "Hong Kong",
    "Hungarian": "Hungary",
    "Indian": "India",
    "Indonesian": "Indonesia",
    "Irish": "Ireland",
    "Italian": "Italy",
    "Japanese": "Japan",
    "Liechtensteiner": "Liechtenstein",
    "Malaysian": "Malaysia",
    "Mexican": "Mexico",
    "Monegasque": "Monaco",
    "New Zealander": "New Zealand",
    "Polish": "Poland",
    "Portuguese": "Portugal",
    "Rhodesian": "Zimbabwe",
    "Russian": "Russia",
    "South African": "South Africa",
    "Spanish": "Spain",
    "Swedish": "Sweden",
    "Swiss": "Switzerland",
    "Thai": "Thailand",
    "Uruguayan": "Uruguay",
    "Venezuelan": "Venezuela",
}

# Second country for dual nationalities only; NaN for everyone else
SECOND_COUNTRY = {
    "American-Italian": "Italy",
    "Argentine-Italian": "Italy",
}


def clean_drivers(drivers):
    """4.11: fill missing codes (flagged), dob to datetime, nationality to home country.

    `drivers.number` stays missing: permanent numbers only exist from 2014.
    """
    df = drivers.copy()

    # Codes: generated from the surname only where the official code is missing
    df["code_was_generated"] = df["code"].isna()
    df["code_filled"] = df["code"].fillna(df["surname"].str[:3].str.upper())
    existing = df["code"].notna()
    assert df.loc[existing, "code"].equals(df.loc[existing, "code_filled"])
    assert df["code_filled"].notna().all()

    # 4.11.1 Date of birth
    dob_count = df["dob"].notna().sum()
    df["dob"] = pd.to_datetime(df["dob"], errors="coerce")
    assert df["dob"].notna().sum() == dob_count

    # 4.11.2 Home country (stray whitespace such as 'Argentinian ' stripped first)
    df["nationality"] = df["nationality"].str.strip()
    df["home_country"] = df["nationality"].map(NATIONALITY_TO_COUNTRY)
    df["home_country_2"] = df["nationality"].map(SECOND_COUNTRY)
    assert df["home_country"].notna().all(), "unmapped nationality"

    assert len(df) == len(drivers)
    assert df["driverId"].is_unique
    return decode_html(df)


def clean_circuits(circuits, races_clean):
    """4.11.2 / 4.15: standardise 'United States' to 'USA'; otherwise unchanged."""
    df = circuits.copy()
    df["country"] = df["country"].replace({"United States": "USA"})

    assert "United States" not in set(df["country"])
    assert df["lat"].between(-90, 90).all() and df["lng"].between(-180, 180).all()
    assert races_clean["circuitId"].isin(df["circuitId"]).all()
    assert df["circuitId"].is_unique
    assert len(df) == len(circuits)
    return decode_html(df)


def clean_constructors(constructors):
    """4.16: unchanged apart from decoding HTML codes ('Lotus-Pratt &amp; Whitney')."""
    df = constructors.copy()
    assert df["constructorId"].is_unique
    assert len(df) == len(constructors)
    return decode_html(df)


def clean_constructor_results(constructor_results):
    """4.10: drop the `status` column (17 of 12,625 values, all McLaren 2007 'D')."""
    df = constructor_results.drop(columns=["status"])
    assert len(df) == len(constructor_results)
    return df


# ---------------------------------------------------------------------------
# 4.12 sprint_results
# ---------------------------------------------------------------------------

def clean_sprint_results(sprint_results):
    """4.12.2: total sprint time and fastest lap to seconds; outcome NaNs kept (4.12.1)."""
    df = sprint_results.copy()
    df["total_time_seconds"] = df["milliseconds"] / 1000
    df["fastestLapTime_seconds"] = lap_time_to_seconds(df["fastestLapTime"])

    assert len(df) == len(sprint_results)
    assert df["resultId"].is_unique
    assert not df.duplicated(["raceId", "driverId"]).any()
    assert df["milliseconds"].notna().sum() == df["total_time_seconds"].notna().sum()
    assert df["fastestLapTime"].notna().sum() == df["fastestLapTime_seconds"].notna().sum()
    return decode_html(df)


# ---------------------------------------------------------------------------
# 4.13 standings, 4.14 seasons: integrity checks only, tables kept unchanged
# ---------------------------------------------------------------------------

def _clean_standings(standings, key):
    df = standings.copy()
    assert not df.duplicated(["raceId", key]).any()
    assert (df["points"] >= 0).all() and (df["wins"] >= 0).all()
    assert len(df) == len(standings)
    return df


def clean_driver_standings(driver_standings):
    """4.13: integrity checks; kept unchanged (post-race info, shifted later)."""
    return _clean_standings(driver_standings, "driverId")


def clean_constructor_standings(constructor_standings):
    """4.13: integrity checks; kept unchanged (no standings before 1958)."""
    return _clean_standings(constructor_standings, "constructorId")


def clean_seasons(seasons, races_clean):
    """4.14: integrity checks; kept unchanged."""
    df = seasons.copy()
    assert df["year"].is_unique and df["year"].notna().all()
    assert set(df["year"]) == set(races_clean["year"])
    assert len(df) == len(seasons)
    return df


# ---------------------------------------------------------------------------
# 4.4, 4.17 lap_times and pit_stops
# ---------------------------------------------------------------------------

def clean_lap_times(lap_times):
    """4.4: lap time in seconds from milliseconds; all rows kept (4.17)."""
    df = lap_times.copy()
    df["time_seconds"] = df["milliseconds"] / 1000
    assert (df["time_seconds"] > 0).all()
    assert len(df) == len(lap_times)
    return df


def clean_pit_stops(pit_stops):
    """4.4 + 4.17.1: duration in seconds, clock time of the stop to a time type."""
    df = pit_stops.copy()
    df["duration_seconds"] = df["milliseconds"] / 1000
    assert (df["duration_seconds"] > 0).all()

    time_count = df["time"].notna().sum()
    df["time"] = clock_time(df["time"])
    assert df["time"].notna().sum() == time_count
    assert len(df) == len(pit_stops)
    return df


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def clean_tables(tables, verbose=True, results_log=None):
    """Clean every table in dependency order, timing each step.

    Returns (clean, timings): dicts keyed by table name (without '_clean').
    """
    clean = {}
    timings = {}

    # (name, function producing the cleaned table); later steps use earlier results
    steps = [
        ("status",                lambda: clean_status(tables["status"])),
        ("races",                 lambda: clean_races(tables["races"])),
        ("qualifying",            lambda: clean_qualifying(tables["qualifying"], clean["races"])),
        ("results",               lambda: clean_results(tables["results"], clean["races"],
                                                        clean["qualifying"], clean["status"],
                                                        log=results_log)),
        ("drivers",               lambda: clean_drivers(tables["drivers"])),
        ("circuits",              lambda: clean_circuits(tables["circuits"], clean["races"])),
        ("constructors",          lambda: clean_constructors(tables["constructors"])),
        ("constructor_results",   lambda: clean_constructor_results(tables["constructor_results"])),
        ("sprint_results",        lambda: clean_sprint_results(tables["sprint_results"])),
        ("driver_standings",      lambda: clean_driver_standings(tables["driver_standings"])),
        ("constructor_standings", lambda: clean_constructor_standings(tables["constructor_standings"])),
        ("seasons",               lambda: clean_seasons(tables["seasons"], clean["races"])),
        ("lap_times",             lambda: clean_lap_times(tables["lap_times"])),
        ("pit_stops",             lambda: clean_pit_stops(tables["pit_stops"])),
    ]
    assert sorted(name for name, _ in steps) == sorted(TABLE_NAMES)

    for name, step in steps:
        start = time.perf_counter()
        clean[name] = step()
        timings[name] = time.perf_counter() - start

        if verbose:
            raw_rows = len(tables[name])
            rows, cols = clean[name].shape
            print(f"{name:<22} {raw_rows:>9,} -> {rows:>9,} rows  {cols:>3} cols  "
                  f"{timings[name]:8.4f} s")

    return clean, timings


def check_against_notebook(clean, results_log):
    """Compare cleaned shapes and the results row log with the notebook's output."""
    rows = []
    for name, expected in NOTEBOOK_SHAPES.items():
        rows.append({"table": name, "notebook": expected, "script": clean[name].shape})
    for step, expected in NOTEBOOK_RESULTS_LOG.items():
        rows.append({"table": f"results: {step}", "notebook": expected,
                     "script": results_log.get(step)})
    check = pd.DataFrame(rows).set_index("table")
    check["match"] = check["notebook"] == check["script"]
    return check


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path, help="folder holding the 14 CSV files")
    parser.add_argument("--check", action="store_true",
                        help="compare row/column counts with the notebook (not timed)")
    args = parser.parse_args()

    missing = [n for n in TABLE_NAMES if not (args.data_dir / f"{n}.csv").is_file()]
    if missing:
        sys.exit(f"Missing CSV files in {args.data_dir}: {', '.join(missing)}")

    print(f"Loading {len(TABLE_NAMES)} tables from {args.data_dir}")
    load_start = time.perf_counter()
    tables, _ = load_tables(args.data_dir, verbose=False)
    print(f"Loaded in {time.perf_counter() - load_start:.4f} s (not part of the cleaning time)\n")

    print("Cleaning (Section 4):")
    results_log = {}
    total_start = time.perf_counter()
    clean, timings = clean_tables(tables, results_log=results_log)
    total = time.perf_counter() - total_start

    print("-" * 74)
    print(f"{'sum of cleaning steps':<62} {sum(timings.values()):8.4f} s")
    print(f"{'total (wall clock)':<62} {total:8.4f} s")

    print("\nresults row log (4.9.7):")
    for step, n in results_log.items():
        print(f"  {step:<30} {n:>7,}")

    if args.check:
        print("\nComparison with the notebook:")
        check = check_against_notebook(clean, results_log)
        print(check.to_string())
        bad = list(check.index[~check["match"]])
        print("Mismatches:", bad or "none")
        if bad:
            sys.exit(1)


if __name__ == "__main__":
    main()
