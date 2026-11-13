"""Compare two feature-table outputs and report the rows that differ.

Every implementation (pandas, SQL, R, C, Rust) writes its feature table in the
same fixed format (see README, "Output equivalence check"):

    - CSV with a header row, comma-separated, no index column
    - columns: raceId, driverId, then the feature columns in canonical order
      (FEATURE_COLUMNS below); an implementation that builds only some features
      writes only those, keeping the order
    - rows sorted by (raceId, driverId), ascending, one row per pair
    - raceId and driverId as integers; every feature value with exactly
      6 decimals (printf "%.6f"), also 0/1 flags and counts
    - NULL / NaN as an empty field

Values are compared as numbers: two values match when both are empty or they
differ by at most --tol (default 1e-6, one unit in the 6th decimal, because
languages can round the last digit differently). Both files are read as streams
and merged on (raceId, driverId), so memory stays flat for the scaled datasets.

Usage:
    python bench/compare_outputs.py bench/outputs/pandas.csv bench/outputs/sql.csv
    python bench/compare_outputs.py A.csv B.csv --tol 0 --max-rows 50

Exit code 0 when the files match, 1 when they differ or break the format.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

KEY_COLUMNS = ["raceId", "driverId"]

# Same order as FEATURES in impl/python/features.py (notebook 6.7); keep in sync.
FEATURE_COLUMNS = [
    "grid_filled", "grid_zero_flag", "field_size",
    "quali_position_filled", "has_qualifying", "quali_gap_pct",
    "drv_prev_races", "drv_podium_rate_last5", "drv_avg_finish_last5",
    "drv_career_podium_rate", "drv_circuit_podium_rate",
    "cons_podiums_last5", "cons_avg_finish_last5",
    "drv_pre_points_share", "drv_pre_wins", "drv_pre_position",
    "cons_pre_points_share", "cons_pre_position",
    "driver_age", "home_race", "round", "hybrid_era",
]

VALUE_PATTERN = re.compile(r"-?\d+\.\d{6}")

# Absorbs binary rounding when parsing, e.g. 0.123457 - 0.123456 = 1.0000000000287557e-06
FLOAT_SLACK = 1e-12


class FormatError(Exception):
    """The file breaks the format so badly that the comparison cannot go on."""


class OutputFile:
    """One output CSV, read row by row with format checks along the way."""

    def __init__(self, path):
        self.path = path
        self.rows = 0
        # column -> number of values not written as %.6f, and the first one seen
        self.bad_values = Counter()
        self.first_bad_value = {}

    def __enter__(self):
        self._file = open(self.path, newline="", encoding="utf-8")
        self._reader = csv.reader(self._file)
        self.features = self._read_header()
        return self

    def __exit__(self, *exc):
        self._file.close()

    def _read_header(self):
        header = next(self._reader, None)
        if header is None:
            raise FormatError(f"{self.path}: file is empty")
        if header[:2] != KEY_COLUMNS:
            raise FormatError(f"{self.path}: the first columns must be raceId,driverId, "
                              f"found {','.join(header[:2])}")
        features = header[2:]
        unknown = [c for c in features if c not in FEATURE_COLUMNS]
        if unknown:
            raise FormatError(f"{self.path}: unknown columns {unknown}")
        if len(set(features)) != len(features):
            raise FormatError(f"{self.path}: duplicate columns")
        positions = [FEATURE_COLUMNS.index(c) for c in features]
        if positions != sorted(positions):
            expected = [c for c in FEATURE_COLUMNS if c in features]
            raise FormatError(f"{self.path}: feature columns are not in canonical order; "
                              f"expected {','.join(expected)}")
        return features

    def __iter__(self):
        """Yield ((raceId, driverId), values) and check sorting and value format."""
        n_fields = len(KEY_COLUMNS) + len(self.features)
        previous = None
        for line_no, row in enumerate(self._reader, start=2):
            if len(row) != n_fields:
                raise FormatError(f"{self.path}:{line_no}: {len(row)} fields, "
                                  f"the header has {n_fields}")
            try:
                key = (int(row[0]), int(row[1]))
            except ValueError:
                raise FormatError(f"{self.path}:{line_no}: raceId and driverId must be "
                                  f"integers, found {row[0]!r}, {row[1]!r}") from None
            if previous is not None and key <= previous:
                raise FormatError(f"{self.path}:{line_no}: rows must be sorted by "
                                  f"(raceId, driverId) without duplicates; "
                                  f"{key} comes after {previous}")
            previous = key

            values = row[2:]
            for column, value in zip(self.features, values):
                if value and not VALUE_PATTERN.fullmatch(value):
                    self.bad_values[column] += 1
                    self.first_bad_value.setdefault(column, f"line {line_no}: {value!r}")
            self.rows += 1
            yield key, values


def values_match(a, b, tol):
    if a == b:
        return True
    if not a or not b:
        return False
    try:
        return abs(float(a) - float(b)) <= tol + FLOAT_SLACK
    except ValueError:
        return False


def compare(file_a, file_b, tol, max_rows):
    """Merge the two sorted files on (raceId, driverId) and collect the differences."""
    common = [c for c in file_a.features if c in file_b.features]
    index_a = [file_a.features.index(c) for c in common]
    index_b = [file_b.features.index(c) for c in common]

    result = {
        "common": common,
        "only_a": [], "only_a_count": 0,
        "only_b": [], "only_b_count": 0,
        "shared_rows": 0,
        "diff_rows": [], "diff_row_count": 0,
        "diff_by_column": Counter(),
    }

    rows_a, rows_b = iter(file_a), iter(file_b)
    a, b = next(rows_a, None), next(rows_b, None)
    while a is not None or b is not None:
        if b is None or (a is not None and a[0] < b[0]):
            result["only_a_count"] += 1
            if len(result["only_a"]) < max_rows:
                result["only_a"].append(a[0])
            a = next(rows_a, None)
        elif a is None or b[0] < a[0]:
            result["only_b_count"] += 1
            if len(result["only_b"]) < max_rows:
                result["only_b"].append(b[0])
            b = next(rows_b, None)
        else:
            result["shared_rows"] += 1
            diffs = [(column, a[1][i], b[1][j])
                     for column, i, j in zip(common, index_a, index_b)
                     if not values_match(a[1][i], b[1][j], tol)]
            if diffs:
                result["diff_row_count"] += 1
                result["diff_by_column"].update(column for column, _, _ in diffs)
                if len(result["diff_rows"]) < max_rows:
                    result["diff_rows"].append((a[0], diffs))
            a, b = next(rows_a, None), next(rows_b, None)
    return result


def show(value):
    return value if value else "(empty)"


def report(file_a, file_b, result, tol, max_rows):
    """Print the comparison; return True when the files match."""
    for label, f in (("A", file_a), ("B", file_b)):
        print(f"{label}: {f.path}  ({f.rows:,} rows, {len(f.features)} feature columns)")
    print(f"Compared {len(result['common'])} feature columns on "
          f"{result['shared_rows']:,} shared rows (tolerance {tol:g})\n")

    ok = True

    only_a = [c for c in file_a.features if c not in file_b.features]
    only_b = [c for c in file_b.features if c not in file_a.features]
    if only_a or only_b:
        print("Columns not compared (only in one file):")
        if only_a:
            print(f"  only in A: {', '.join(only_a)}")
        if only_b:
            print(f"  only in B: {', '.join(only_b)}")
        print()
    if not result["common"]:
        print("No feature columns in common.")
        ok = False

    for label, f in (("A", file_a), ("B", file_b)):
        if f.bad_values:
            ok = False
            print(f"Values in {label} not written with 6 decimals:")
            for column, count in f.bad_values.items():
                print(f"  {column:<26} {count:>8,}   first at {f.first_bad_value[column]}")
            print()

    for label, key in (("A", "only_a"), ("B", "only_b")):
        count = result[f"{key}_count"]
        if count:
            ok = False
            shown = ", ".join(f"({r}, {d})" for r, d in result[key])
            more = " ..." if count > len(result[key]) else ""
            print(f"Rows only in {label}: {count:,}   (raceId, driverId): {shown}{more}\n")

    if result["diff_row_count"]:
        ok = False
        print(f"Rows with different values: {result['diff_row_count']:,}")
        print("  per column:")
        for column, count in result["diff_by_column"].most_common():
            print(f"    {column:<26} {count:>8,}")
        shown = len(result["diff_rows"])
        print(f"  first {shown} of them:" if shown < result["diff_row_count"] else "  rows:")
        print(f"    {'raceId':>7} {'driverId':>8}  {'column':<26} {'A':>14} {'B':>14}")
        for (race_id, driver_id), diffs in result["diff_rows"]:
            for column, value_a, value_b in diffs:
                print(f"    {race_id:>7} {driver_id:>8}  {column:<26} "
                      f"{show(value_a):>14} {show(value_b):>14}")
        print()

    print("Result:", "MATCH" if ok else "DIFFERENT")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file_a", type=Path, help="first output CSV")
    parser.add_argument("file_b", type=Path, help="second output CSV")
    parser.add_argument("--tol", type=float, default=1e-6,
                        help="largest allowed absolute difference per value (default 1e-6)")
    parser.add_argument("--max-rows", type=int, default=20, metavar="N",
                        help="show at most N example rows per kind of difference (default 20)")
    args = parser.parse_args()

    for path in (args.file_a, args.file_b):
        if not path.is_file():
            sys.exit(f"File not found: {path}")

    try:
        with OutputFile(args.file_a) as file_a, OutputFile(args.file_b) as file_b:
            result = compare(file_a, file_b, args.tol, args.max_rows)
            ok = report(file_a, file_b, result, args.tol, args.max_rows)
    except FormatError as error:
        print(f"Format error: {error}")
        print("Result: DIFFERENT (the file does not follow the output format)")
        sys.exit(1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
