"""Load the 14 F1 CSV tables with pandas and time each load.

Script version of notebook Section 2 (load) and 2.1 (sentinel check).

Usage:
    python impl/python/load.py data/
    python impl/python/load.py data/ --check   # also run the 2.1 sentinel check
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

# The raw CSVs write a missing value as the literal text \N (a sentinel).
NA_SENTINEL = r"\N"

TABLE_NAMES = [
    "results",
    "races",
    "drivers",
    "constructors",
    "qualifying",
    "status",
    "circuits",
    "constructor_results",
    "constructor_standings",
    "driver_standings",
    "lap_times",
    "pit_stops",
    "sprint_results",
    "seasons",
]


def load_tables(data_dir, verbose=True):
    """Read every table into a dict of DataFrames, timing each read.

    Returns (tables, timings) where timings maps table name -> seconds.
    """
    data_dir = Path(data_dir)
    tables = {}
    timings = {}

    for name in TABLE_NAMES:
        start = time.perf_counter()
        tables[name] = pd.read_csv(data_dir / f"{name}.csv", na_values=[NA_SENTINEL])
        timings[name] = time.perf_counter() - start

        if verbose:
            rows, cols = tables[name].shape
            print(f"{name:<22} {rows:>9,} rows  {cols:>3} cols  {timings[name]:8.4f} s")

    return tables, timings


def sentinel_check(tables, data_dir):
    """Section 2.1: every missing value after loading comes from a \\N or an empty field.

    Empty fields (qualifying q2/q3) are also read as missing by pandas' defaults,
    so they are counted too; otherwise qualifying would be a false mismatch.
    """
    data_dir = Path(data_dir)
    rows = []
    for name, df in tables.items():
        raw = pd.read_csv(data_dir / f"{name}.csv", dtype=str, keep_default_na=False)
        rows.append({
            "table": name,
            "raw_sentinels": int((raw == NA_SENTINEL).sum().sum()),
            "raw_empty": int(raw.apply(lambda s: s.str.strip().eq("")).sum().sum()),
            "missing_after_load": int(df.isna().sum().sum()),
        })

    check = pd.DataFrame(rows).set_index("table")
    check["all_converted"] = (
        check["raw_sentinels"] + check["raw_empty"] == check["missing_after_load"]
    )
    return check


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path, help="folder holding the 14 CSV files")
    parser.add_argument("--check", action="store_true",
                        help="run the \\N sentinel check after loading (not timed)")
    args = parser.parse_args()

    missing = [n for n in TABLE_NAMES if not (args.data_dir / f"{n}.csv").is_file()]
    if missing:
        sys.exit(f"Missing CSV files in {args.data_dir}: {', '.join(missing)}")

    print(f"Loading {len(TABLE_NAMES)} tables from {args.data_dir}\n")
    total_start = time.perf_counter()
    tables, timings = load_tables(args.data_dir)
    total = time.perf_counter() - total_start

    print("-" * 62)
    print(f"{'sum of table loads':<50} {sum(timings.values()):8.4f} s")
    print(f"{'total (wall clock)':<50} {total:8.4f} s")

    if args.check:
        print("\nSentinel check (Section 2.1):")
        check = sentinel_check(tables, args.data_dir)
        print(check.to_string())
        bad = list(check.index[~check["all_converted"]])
        print("Tables where missing values differ from \\N + empty count:", bad or "none")
        if bad:
            sys.exit(1)


if __name__ == "__main__":
    main()
