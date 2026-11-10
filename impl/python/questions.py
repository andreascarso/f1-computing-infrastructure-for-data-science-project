"""Answer the three data-engineering questions (Q1-Q3) with pandas and time each query.

Script version of notebook Section 5. Each question is one function that takes
the cleaned tables (from clean.py) and returns the same DataFrame(s) as the
notebook; plots and the written answers stay in the notebook.

    Q1  front-row start -> podium by circuit, 1994-2013 vs. 2014-2024   (5.1)
    Q2  home podium rate, controlling for starting position             (5.2)
    Q3  mechanical-retirement rate by constructor, 2014-21 vs. 2022-24 (5.3)

Usage:
    python impl/python/questions.py data/
    python impl/python/questions.py data/ --check        # compare with the notebook's numbers
    python impl/python/questions.py data/ --out results/ # tidy CSVs, e.g. to diff with SQL
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from clean import clean_tables
from load import TABLE_NAMES, load_tables

ERAS = ["1994–2013", "2014–2024"]
PERIODS = ["2014–2021", "2022–2024"]

MIN_STARTS_Q1 = 10   # front-row starts per circuit in EACH era
MIN_STARTS_Q3 = 30   # race starts per constructor per period

GRID_BINS = [-1, 0, 3, 6, 10, np.inf]
GRID_LABELS = ["grid 0", "1–3", "4–6", "7–10", "11+"]


# ---------------------------------------------------------------------------
# Q1 (5.1)
# ---------------------------------------------------------------------------

def _q1_rows(clean):
    """Driver-race rows from 1994 with era and both front-row definitions."""
    df = (
        clean["results"][["raceId", "driverId", "grid", "podium"]]
        .merge(clean["races"][["raceId", "year", "circuitId"]],
               on="raceId", how="left", validate="many_to_one")
        .merge(clean["circuits"][["circuitId", "name"]].rename(columns={"name": "circuit"}),
               on="circuitId", how="left", validate="many_to_one")
        .merge(clean["qualifying"][["raceId", "driverId", "position"]]
               .rename(columns={"position": "quali_position"}),
               on=["raceId", "driverId"], how="left", validate="one_to_one")
    )
    # Qualifying data starts in 1994, so both definitions are compared from there
    df = df[df["year"] >= 1994].copy()
    df["era"] = np.where(df["year"] >= 2014, ERAS[1], ERAS[0])
    df["grid_front_row"] = df["grid"].isin([1, 2])
    df["quali_front_row"] = df["quali_position"].isin([1, 2])
    return df


def _conversion_by_circuit(df, front_row_col):
    """Podium rate of front-row starters per circuit and era (circuits x era table)."""
    front = df[df[front_row_col]]
    table = (
        front.groupby(["circuit", "era"])["podium"]
        .agg(starts="size", podiums="sum")
        .assign(rate=lambda t: t["podiums"] / t["starts"] * 100)
        .unstack("era")
    )
    # keep circuits with enough front-row starts in BOTH eras
    enough = (table["starts"] >= MIN_STARTS_Q1).all(axis=1)
    return table[enough]


def q1(clean):
    """Q1: front-row -> podium conversion by circuit and era.

    Returns (by_grid, by_qualifying): front row measured by where the car started
    (after penalties) and by qualifying position (before penalties). Columns are a
    (starts | podiums | rate) x era MultiIndex, one row per circuit.
    """
    rows = _q1_rows(clean)
    return (_conversion_by_circuit(rows, "grid_front_row"),
            _conversion_by_circuit(rows, "quali_front_row"))


def rank_correlation(table):
    """Spearman correlation of circuit rates between the two eras (1 = same ranking).

    Computed as the Pearson correlation of average ranks, which is what
    corr(method='spearman') does, without needing scipy.
    """
    ranks = table["rate"][ERAS].rank(method="average")
    return ranks[ERAS[0]].corr(ranks[ERAS[1]])


# ---------------------------------------------------------------------------
# Q2 (5.2)
# ---------------------------------------------------------------------------

def q2(clean):
    """Q2: podium rate home vs. away inside each starting-position group.

    Returns one row per grid group with columns
    'rate away', 'rate home' (%), 'starts away', 'starts home', 'difference (pp)'.
    """
    df = (
        clean["results"][["raceId", "driverId", "grid", "podium"]]
        .merge(clean["races"][["raceId", "year", "circuitId"]],
               on="raceId", how="left", validate="many_to_one")
        .merge(clean["circuits"][["circuitId", "country"]],
               on="circuitId", how="left", validate="many_to_one")
        .merge(clean["drivers"][["driverId", "home_country", "home_country_2"]],
               on="driverId", how="left", validate="many_to_one")
    )
    # Home race: circuit country is the driver's (first or second) home country
    df["is_home"] = (df["country"] == df["home_country"]) | (df["country"] == df["home_country_2"])
    # grid 0 (no slot recorded, often a pit-lane start) is its own group
    df["grid_group"] = pd.cut(df["grid"], bins=GRID_BINS, labels=GRID_LABELS)

    by_group = (
        df.groupby(["grid_group", "is_home"], observed=True)["podium"]
        .agg(rate="mean", starts="size")
        .unstack("is_home")
    )
    by_group.columns = [f"{stat} {'home' if home else 'away'}" for stat, home in by_group.columns]
    by_group["rate home"] *= 100
    by_group["rate away"] *= 100
    by_group["difference (pp)"] = by_group["rate home"] - by_group["rate away"]
    return by_group


def home_advantage(by_group):
    """Q2 in one number: group differences weighted by the number of home starts (pp)."""
    weights = by_group["starts home"].fillna(0)
    return (by_group["difference (pp)"] * weights).sum() / weights.sum()


# ---------------------------------------------------------------------------
# Q3 (5.3)
# ---------------------------------------------------------------------------

def q3(clean):
    """Q3: mechanical retirements / race starts per constructor and period.

    Returns one row per (period, constructor) with at least MIN_STARTS_Q3 starts:
    columns period, constructor, starts, mechanical, rate (%).
    """
    df = (
        clean["results"][["raceId", "driverId", "constructorId", "statusId"]]
        .merge(clean["races"][["raceId", "year"]],
               on="raceId", how="left", validate="many_to_one")
        .merge(clean["status"][["statusId", "is_mechanical"]],
               on="statusId", how="left", validate="many_to_one")
        .merge(clean["constructors"][["constructorId", "name"]].rename(columns={"name": "constructor"}),
               on="constructorId", how="left", validate="many_to_one")
    )
    df = df[df["year"].between(2014, 2024)].copy()
    df["period"] = np.where(df["year"] <= 2021, PERIODS[0], PERIODS[1])

    rates = (
        df.groupby(["period", "constructor"])["is_mechanical"]
        .agg(starts="size", mechanical="sum")
        .assign(rate=lambda t: t["mechanical"] / t["starts"] * 100)
        .reset_index()
    )
    return rates[rates["starts"] >= MIN_STARTS_Q3]


# ---------------------------------------------------------------------------
# Tidy output (for comparing with the SQL versions)
# ---------------------------------------------------------------------------

def tidy_q1(table, definition):
    """Long format: definition, circuit, era, starts, podiums, rate."""
    long = table.stack("era", future_stack=True).reset_index()
    long.insert(0, "front_row_by", definition)
    long[["starts", "podiums"]] = long[["starts", "podiums"]].astype(int)
    return long[["front_row_by", "circuit", "era", "starts", "podiums", "rate"]]


def tidy_outputs(answers):
    """One sorted, flat DataFrame per question, as a SQL query would return it."""
    q1_grid, q1_quali = answers["q1"]
    q1_long = pd.concat([tidy_q1(q1_grid, "grid"), tidy_q1(q1_quali, "qualifying")])
    q2_flat = answers["q2"].reset_index().astype({"grid_group": str})
    q3_flat = answers["q3"][["period", "constructor", "starts", "mechanical", "rate"]]
    return {
        "q1": q1_long.sort_values(["front_row_by", "circuit", "era"]).reset_index(drop=True),
        "q2": q2_flat.reset_index(drop=True),
        "q3": q3_flat.sort_values(["period", "constructor"]).reset_index(drop=True),
    }


# ---------------------------------------------------------------------------
# Check against the notebook's printed answers
# ---------------------------------------------------------------------------

def check_against_notebook(answers):
    """Key numbers the notebook prints in Section 5, compared with the script's."""
    q1_grid, q1_quali = answers["q1"]
    by_group, rates = answers["q2"], answers["q3"]
    p1 = rates[rates["period"] == PERIODS[0]].set_index("constructor")
    p2 = rates[rates["period"] == PERIODS[1]].set_index("constructor")

    checks = [
        ("Q1 circuits (grid)", 15, len(q1_grid)),
        ("Q1 circuits (qualifying)", 14, len(q1_quali)),
        ("Q1 rank corr (grid)", -0.07, round(rank_correlation(q1_grid), 2)),
        ("Q1 rank corr (qualifying)", 0.20, round(rank_correlation(q1_quali), 2)),
        ("Q1 Suzuka 2014–2024 (grid, %)", 88.9, round(q1_grid.loc["Suzuka Circuit", ("rate", ERAS[1])], 1)),
        ("Q1 Hungaroring 1994–2013 (grid, %)", 72.5, round(q1_grid.loc["Hungaroring", ("rate", ERAS[0])], 1)),
        ("Q2 home starts", 1943, int(by_group["starts home"].sum())),
        ("Q2 away starts", 22743, int(by_group["starts away"].sum())),
        ("Q2 grid 1–3 difference (pp)", 0.37, round(by_group.loc["1–3", "difference (pp)"], 2)),
        ("Q2 home advantage (pp)", -0.61, round(home_advantage(by_group), 2)),
        ("Q3 starts 2014–2021", 3258, int(p1["starts"].sum())),
        ("Q3 starts 2022–2024", 1354, int(p2["starts"].sum())),
        ("Q3 mechanical 2014–2021", 303, int(p1["mechanical"].sum())),
        ("Q3 constructors 2014–2021", 19, len(p1)),
        ("Q3 constructors 2022–2024", 12, len(p2)),
        ("Q3 Caterham 2014–2021 (%)", 23.53, round(p1.loc["Caterham", "rate"], 2)),
        ("Q3 Alfa Romeo 2022–2024 (%)", 12.50, round(p2.loc["Alfa Romeo", "rate"], 2)),
    ]
    check = pd.DataFrame(checks, columns=["value", "notebook", "script"]).set_index("value")
    check["match"] = check["notebook"] == check["script"]
    return check


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path, help="folder holding the 14 CSV files")
    parser.add_argument("--check", action="store_true",
                        help="compare key numbers with the notebook (not timed)")
    parser.add_argument("--out", type=Path, metavar="DIR",
                        help="write tidy q1.csv, q2.csv, q3.csv to DIR (not timed)")
    args = parser.parse_args()

    missing = [n for n in TABLE_NAMES if not (args.data_dir / f"{n}.csv").is_file()]
    if missing:
        sys.exit(f"Missing CSV files in {args.data_dir}: {', '.join(missing)}")

    prep_start = time.perf_counter()
    tables, _ = load_tables(args.data_dir, verbose=False)
    clean, _ = clean_tables(tables, verbose=False)
    print(f"Loaded and cleaned {len(TABLE_NAMES)} tables in "
          f"{time.perf_counter() - prep_start:.4f} s (not part of the query time)\n")

    answers, timings = {}, {}
    for name, query in [("q1", q1), ("q2", q2), ("q3", q3)]:
        start = time.perf_counter()
        answers[name] = query(clean)
        timings[name] = time.perf_counter() - start

    print(f"{'query':<52} {'rows':>6} {'time':>10}")
    print(f"{'Q1 front row -> podium by circuit (grid)':<52} {len(answers['q1'][0]):>6}")
    print(f"{'Q1 front row -> podium by circuit (qualifying)':<52} {len(answers['q1'][1]):>6}"
          f" {timings['q1']:8.4f} s")
    print(f"{'Q2 home vs. away podium rate by grid group':<52} {len(answers['q2']):>6}"
          f" {timings['q2']:8.4f} s")
    print(f"{'Q3 mechanical-retirement rate by constructor':<52} {len(answers['q3']):>6}"
          f" {timings['q3']:8.4f} s")
    print("-" * 70)
    print(f"{'sum of queries':<59} {sum(timings.values()):8.4f} s")

    print(f"\nQ1 rank correlation between eras: grid {rank_correlation(answers['q1'][0]):+.2f}, "
          f"qualifying {rank_correlation(answers['q1'][1]):+.2f}")
    print(f"Q2 home advantage after controlling for grid: {home_advantage(answers['q2']):+.2f} pp")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, df in tidy_outputs(answers).items():
            df.to_csv(args.out / f"{name}.csv", index=False)
        print(f"\nWrote q1.csv, q2.csv, q3.csv to {args.out}")

    if args.check:
        print("\nComparison with the notebook:")
        check = check_against_notebook(answers)
        print(check.to_string())
        bad = list(check.index[~check["match"]])
        print("Mismatches:", bad or "none")
        if bad:
            sys.exit(1)


if __name__ == "__main__":
    main()
