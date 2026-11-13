"""Write smaller and larger copies of the 14 CSV tables for the growth measurements.

Sets (written to data/scaled/<set>/, the 100% set is data/ itself):

    25pct, 50pct   whole seasons, spread evenly over 1950-2024 (every 4th / every
                   2nd season), plus only the drivers, constructors and circuits
                   these seasons use; every table shrinks by about the same share
    x10, x50       the original plus 9 / 49 copies of the whole history

Copy k (k = 1, 2, ...) gets new ids for races, drivers, constructors and every
row-level table: id + k * unit, where unit is the next power of ten above the
table's largest id (raceId 18 -> 10018 in copy 1, 20018 in copy 2; driverId 1 ->
1001). driverRef and constructorRef get a "_k" suffix so they stay unique.
Seasons, circuits and status are shared: every copy is an independent field of
drivers and teams racing in the same seasons, at the same circuits, on the same
dates. So a copy's features equal the original's, and the Q1-Q3 rates stay the
same while every count grows k-fold.

Lines are copied byte for byte and only the leading id fields are rewritten, so
quoting and the \\N sentinel stay exactly as in data/ (loading speed depends on
both). After writing, each set is read back with a CSV parser: primary keys must
be unique and every foreign key must exist in its parent table.

Usage:
    python bench/scale_data.py data/                       # 25pct, 50pct, x10, x50
    python bench/scale_data.py data/ --sets 25pct x10      # only these sets
    python bench/scale_data.py data/ --out /tmp/scaled     # somewhere else
"""

import argparse
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

DEFAULT_SETS = ["25pct", "50pct", "x10", "x50"]

PRIMARY_KEYS = {
    "seasons": "year",
    "circuits": "circuitId",
    "status": "statusId",
    "drivers": "driverId",
    "constructors": "constructorId",
    "races": "raceId",
    "results": "resultId",
    "sprint_results": "resultId",
    "qualifying": "qualifyId",
    "driver_standings": "driverStandingsId",
    "constructor_standings": "constructorStandingsId",
    "constructor_results": "constructorResultsId",
}

# table -> {column: parent table}
FOREIGN_KEYS = {
    "races": {"year": "seasons", "circuitId": "circuits"},
    "results": {"raceId": "races", "driverId": "drivers",
                "constructorId": "constructors", "statusId": "status"},
    "sprint_results": {"raceId": "races", "driverId": "drivers",
                       "constructorId": "constructors", "statusId": "status"},
    "qualifying": {"raceId": "races", "driverId": "drivers", "constructorId": "constructors"},
    "driver_standings": {"raceId": "races", "driverId": "drivers"},
    "constructor_standings": {"raceId": "races", "constructorId": "constructors"},
    "constructor_results": {"raceId": "races", "constructorId": "constructors"},
    "lap_times": {"raceId": "races", "driverId": "drivers"},
    "pit_stops": {"raceId": "races", "driverId": "drivers"},
}

TABLE_NAMES = list(PRIMARY_KEYS) + ["lap_times", "pit_stops"]

# Not copied in the x sets: every copy uses the same seasons, circuits and statuses
SHARED = {"seasons", "circuits", "status"}

# Text keys that must stay unique across copies
REF_COLUMNS = {"drivers": "driverRef", "constructors": "constructorRef"}
REF_PATTERN = re.compile(r'"[^",]*"')

# Tables that hang off a race (the subsets keep the rows of the kept races)
RACE_TABLES = [t for t, fks in FOREIGN_KEYS.items() if fks.get("raceId") == "races"]

NULL = r"\N"


class Table:
    """One raw CSV: the header line and the data lines, kept as text."""

    def __init__(self, data_dir, name):
        self.name = name
        with open(data_dir / f"{name}.csv", newline="", encoding="utf-8") as f:
            self.header_line = f.readline()
            self.lines = [line if line.endswith("\n") else line + "\n" for line in f]
        self.columns = next(csv.reader([self.header_line]))

    def index(self, column):
        return self.columns.index(column)

    def ids(self, column):
        """Integer values of a leading id column, one per line."""
        i = self.index(column)
        return [int_field(line, i) for line in self.lines]


def int_field(line, i):
    """Field i of a raw line as int. The id columns come before any quoted text that
    could contain a comma; int() fails loudly if that ever stops being true."""
    return int(line.split(",", i + 1)[i])


def edit_line(line, edits):
    """Apply {field index: function} to the leading fields of a raw line."""
    fields = line.split(",", max(edits) + 1)
    for i, edit in edits.items():
        fields[i] = edit(fields[i])
    return ",".join(fields)


def shift(offset):
    def edit(value):
        if not value.isdigit():
            raise ValueError(f"expected an integer id, found {value!r}")
        return str(int(value) + offset)
    return edit


def suffix(k):
    def edit(value):
        if not REF_PATTERN.fullmatch(value):
            raise ValueError(f"expected a quoted ref without commas, found {value!r}")
        return f'{value[:-1]}_{k}"'
    return edit


def parse_set(name):
    """'25pct' -> ('fraction', Fraction(1, 4)); 'x10' -> ('factor', 10)."""
    if m := re.fullmatch(r"(\d+)pct", name):
        pct = int(m.group(1))
        if 0 < pct < 100:
            return "fraction", Fraction(pct, 100)
    if m := re.fullmatch(r"x(\d+)", name):
        factor = int(m.group(1))
        if factor >= 2:
            return "factor", factor
    raise argparse.ArgumentTypeError(f"bad set {name!r}: use e.g. 25pct (1-99) or x10 (2 or more)")


# ---------------------------------------------------------------------------
# Smaller sets: whole seasons
# ---------------------------------------------------------------------------

def spread_seasons(years, fraction):
    """Every 1/fraction-th season, spread over the whole range (25% -> 1953, 1957, ...)."""
    years = sorted(years)
    return [y for i, y in enumerate(years)
            if (i + 1) * fraction // 1 > i * fraction // 1]


def subset_lines(tables, fraction):
    """{table: kept lines} for the seasons chosen by spread_seasons."""
    seasons = tables["seasons"]
    years = set(spread_seasons(seasons.ids("year"), fraction))
    races = tables["races"]
    kept = {
        "seasons": [l for l, y in zip(seasons.lines, seasons.ids("year")) if y in years],
        "races": [l for l, y in zip(races.lines, races.ids("year")) if y in years],
        "status": tables["status"].lines,
    }
    i_race, i_circuit = races.index("raceId"), races.index("circuitId")
    race_ids = {int_field(l, i_race) for l in kept["races"]}

    used = defaultdict(set)
    used["circuits"] = {int_field(l, i_circuit) for l in kept["races"]}
    for name in RACE_TABLES:
        t = tables[name]
        i = t.index("raceId")
        kept[name] = [l for l in t.lines if int_field(l, i) in race_ids]
        for column, parent in FOREIGN_KEYS[name].items():
            if parent in ("drivers", "constructors"):
                j = t.index(column)
                used[parent].update(int_field(l, j) for l in kept[name])

    for name in ("drivers", "constructors", "circuits"):
        t = tables[name]
        kept[name] = [l for l, key in zip(t.lines, t.ids(PRIMARY_KEYS[name]))
                      if key in used[name]]
    return kept, sorted(years)


def write_subset(tables, fraction, out_dir):
    kept, years = subset_lines(tables, fraction)
    for name, t in tables.items():
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            f.write(t.header_line)
            f.writelines(kept[name])
    shown = ", ".join(map(str, years[:4]))
    return {name: len(lines) for name, lines in kept.items()}, \
        f"{len(years)} seasons ({shown}, ... {years[-1]})"


# ---------------------------------------------------------------------------
# Larger sets: copies with new ids
# ---------------------------------------------------------------------------

def id_units(tables):
    """Offset unit per copied table: the next power of ten above its largest id."""
    return {name: 10 ** len(str(max(tables[name].ids(pk))))
            for name, pk in PRIMARY_KEYS.items() if name not in SHARED}


def copy_edits(table, k, units):
    """Field edits that turn a line of the original into the same line of copy k."""
    edits = {}
    name = table.name
    if name in units:
        edits[table.index(PRIMARY_KEYS[name])] = shift(k * units[name])
    for column, parent in FOREIGN_KEYS.get(name, {}).items():
        if parent not in SHARED:
            edits[table.index(column)] = shift(k * units[parent])
    if name in REF_COLUMNS:
        edits[table.index(REF_COLUMNS[name])] = suffix(k)
    return edits


def write_multiplied(tables, factor, out_dir):
    units = id_units(tables)
    rows = {}
    for name, t in tables.items():
        copies = 1 if name in SHARED else factor
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            f.write(t.header_line)
            f.writelines(t.lines)
            for k in range(1, copies):
                edits = copy_edits(t, k, units)
                f.writelines(edit_line(line, edits) for line in t.lines)
        rows[name] = len(t.lines) * copies
    unit_text = ", ".join(f"{PRIMARY_KEYS[n]} +{units[n]:,}" for n in ("races", "drivers", "constructors"))
    return rows, f"original + {factor - 1} copies (per copy: {unit_text}, ...)"


# ---------------------------------------------------------------------------
# Check: read every written set back with a CSV parser
# ---------------------------------------------------------------------------

def read_columns(path, columns):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        idx = [header.index(c) for c in columns]
        for row in reader:
            yield [row[i] for i in idx]


def check_keys(out_dir, expected_rows):
    """Primary keys unique, every foreign key present in its parent, row counts as written."""
    problems = []
    keys = {}
    for name, pk in PRIMARY_KEYS.items():
        values = [int(v) for (v,) in read_columns(out_dir / f"{name}.csv", [pk])]
        keys[name] = set(values)
        if len(keys[name]) != len(values):
            problems.append(f"{name}.{pk}: {len(values) - len(keys[name]):,} duplicate ids")
        if len(values) != expected_rows[name]:
            problems.append(f"{name}: {len(values):,} rows read back, {expected_rows[name]:,} written")

    for name, fks in FOREIGN_KEYS.items():
        columns = list(fks)
        missing, example = Counter(), {}
        n = 0
        for row in read_columns(out_dir / f"{name}.csv", columns):
            n += 1
            for column, value in zip(columns, row):
                if value != NULL and int(value) not in keys[fks[column]]:
                    missing[column] += 1
                    example.setdefault(column, value)
        for column, count in missing.items():
            problems.append(f"{name}.{column}: {count:,} values not in {fks[column]} "
                            f"(e.g. {example[column]})")
        if name not in PRIMARY_KEYS and n != expected_rows[name]:
            problems.append(f"{name}: {n:,} rows read back, {expected_rows[name]:,} written")
    return problems


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path, help="folder holding the 14 CSV files")
    parser.add_argument("--out", type=Path, default=Path("data/scaled"),
                        help="parent folder for the sets (default data/scaled)")
    parser.add_argument("--sets", nargs="+", default=DEFAULT_SETS, metavar="SET",
                        help=f"sets to write, e.g. 25pct or x10 (default {' '.join(DEFAULT_SETS)})")
    args = parser.parse_args()
    try:
        sets = {name: parse_set(name) for name in args.sets}
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))

    missing = [n for n in TABLE_NAMES if not (args.data_dir / f"{n}.csv").is_file()]
    if missing:
        sys.exit(f"Missing CSV files in {args.data_dir}: {', '.join(missing)}")

    tables = {name: Table(args.data_dir, name) for name in TABLE_NAMES}
    counts = {"data/": {name: len(t.lines) for name, t in tables.items()}}
    failed = False

    for name, (kind, value) in sets.items():
        out_dir = args.out / name
        out_dir.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        if kind == "fraction":
            rows, note = write_subset(tables, value, out_dir)
        else:
            rows, note = write_multiplied(tables, value, out_dir)
        written = time.perf_counter() - start
        problems = check_keys(out_dir, rows)
        size_mb = sum(p.stat().st_size for p in out_dir.glob("*.csv")) / 1e6
        print(f"{name:>6}: {out_dir}  {size_mb:,.1f} MB, written in {written:.1f} s; {note}")
        if problems:
            failed = True
            print("        KEY PROBLEMS:")
            for problem in problems:
                print(f"          {problem}")
        else:
            print("        keys OK: primary keys unique, every foreign key found in its parent")
        counts[name] = rows

    print("\nRows per table:")
    print(f"{'table':<24}" + "".join(f"{c:>12}" for c in counts))
    for table in TABLE_NAMES:
        print(f"{table:<24}" + "".join(f"{counts[c][table]:>12,}" for c in counts))
    base = sum(counts["data/"].values())
    print(f"{'all tables':<24}" + "".join(f"{sum(c.values()):>12,}" for c in counts.values()))
    print(f"{'share of data/':<24}" + "".join(f"{sum(c.values()) / base:>12.2f}" for c in counts.values()))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
