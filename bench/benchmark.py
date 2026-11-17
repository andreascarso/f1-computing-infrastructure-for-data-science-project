"""Run the implementations, time them and store the numbers in benchmark_results.

    python bench/benchmark.py                                # data/, all implementations, 3 runs
    python bench/benchmark.py --datasets data x10 --runs 5
    python bench/benchmark.py --impl sql --datasets x50
    python bench/benchmark.py --impl c rust r                # only the ports

Runs on the laptop and drives the containers; it needs only Docker and
Python 3.11+ (no packages: every query goes through psql inside the db
container, so a Postgres installed on the laptop itself cannot get in the way). `db` must be up (docker compose up -d); the harness builds and
starts `bench`, runs the loader, and stops `bench` again at the end.

For every dataset and implementation:
  1. startup: a no-op process in the same container, e.g. `python -c "import
     pandas"`, timed the same way as a real run (median of --runs)
     Implementations: pandas (B, vectorised), pandas-lambda (A) and sql do
     Sections 2, 4, 6 and Q1-Q3; the C, Rust and R ports do load, the cleaning
     that 6.4 needs, form (6.4) and Q1.
  2. --runs repetitions, each a fresh process: the implementation's own timers
     give the parse step (load) and the compute steps; the wall clock of the
     whole process is measured from outside; `docker stats` samples the memory
     of the bench and db containers every 0.5 s in the background
  3. check: the feature table must match the pandas one (bench/compare_outputs.py),
     and the ports' Q1 the pandas Q1; if one does not, nothing of that dataset
     is stored
  4. SQL only: pg_database_size() and every table's size next to its CSV size
Every number goes into benchmark_results (sql/benchmark_results.sql), the
median over the repetitions plus min and max.
"""

import argparse
import csv
import io
import json
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = Path("bench/outputs")               # relative to ROOT, the same inside the bench container
COMPARE = ROOT / "bench" / "compare_outputs.py"
RESULTS_SCHEMA = ROOT / "sql" / "benchmark_results.sql"

IMPLEMENTATIONS = ["pandas", "pandas-lambda", "sql", "c", "rust", "r"]
PANDAS_VERSIONS = {"pandas": "B", "pandas-lambda": "A"}

# The ports (load, the cleaning 6.4 needs, form 6.4, Q1) in the bench container.
# C and Rust are compiled into the image (bench/Dockerfile); R runs from impl/.
PORTS = {
    "c": ["/opt/ports/f1-c"],
    "rust": ["/opt/ports/f1-rust"],
    "r": ["Rscript", "impl/r/run.R"],   # one data.table thread (its default)
}
# A no-op process per port, for startup: C and Rust print their usage and exit 2
PORT_STARTUP = {
    "c": ("process start", ["/opt/ports/f1-c"], (2,)),
    "rust": ("process start", ["/opt/ports/f1-rust"], (2,)),
    "r": ("R + data.table", ["Rscript", "-e", "suppressPackageStartupMessages(library(data.table))"], (0,)),
}
# The ports print their step times as text; these map the lines to our steps.
# C and Rust: "form (6.4)               0.0033 s"
CR_STEPS = {"parse (Section 2)": "load", "clean (4.9)": "clean", "form (6.4)": "form", "Q1 (5.1)": "q1"}
# R: a printed data.table, "4: rolling form (median)   0.107"
R_STEPS = {"load 14 tables": "load", "clean required tables": "clean", "base join + sort": "base",
           "rolling form (median)": "rolling", "Q1 (median)": "q1"}
Q1_TOLERANCE = 1e-6   # the ports write Q1 rates with 6 decimals

TABLE_NAMES = [
    "seasons", "circuits", "status", "drivers", "constructors", "races", "results",
    "sprint_results", "qualifying", "constructor_results", "constructor_standings",
    "driver_standings", "lap_times", "pit_stops",
]

# Q1-Q3 in SQL are views made by sql/questions.sql; the same answers as q1/q2/q3()
SQL_QUESTIONS = {"q1": "q1_conversion", "q2": "q2_home_by_grid", "q3": "q3_mechanical_rate"}

PSQL = 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'

STATS_INTERVAL = 0.5   # seconds between docker stats samples (Docker's own rate)


class CommandFailed(Exception):
    pass


def run(cmd, stdin=None, ok_codes=(0,)):
    """Run a command; return (stdout, start, end) in time.perf_counter() seconds."""
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=ROOT, input=stdin, capture_output=True, text=True)
    end = time.perf_counter()
    if proc.returncode not in ok_codes:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-15:])
        hint = "  (exit 137: out of memory? raise Docker's memory limit)" if proc.returncode == 137 else ""
        raise CommandFailed(f"{' '.join(cmd)}\nexit code {proc.returncode}{hint}\n{tail}")
    return proc.stdout, start, end


def compose(*args):
    return run(["docker", "compose", "--profile", "bench", *args])[0]


def container_id(service):
    cid = compose("ps", "-q", service).strip()
    if not cid:
        sys.exit(f"The {service} container is not running. Start the database with: docker compose up -d --wait")
    return cid


# ---------------------------------------------------------------------------
# Memory: docker stats in the background
# ---------------------------------------------------------------------------

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TO_MIB = {"B": 1 / 2**20, "KiB": 1 / 2**10, "MiB": 1, "GiB": 2**10,
          "kB": 1e3 / 2**20, "MB": 1e6 / 2**20, "GB": 1e9 / 2**20}


class MemorySampler:
    """Streams `docker stats` for some containers and keeps every sample with its time.

    docker stats reports usage without the inactive page cache, about every 0.5 s.
    A peak needs at least two samples inside the run; shorter runs (under about
    a second) get none, and their process max RSS is the number to use.
    """

    def __init__(self, containers):
        self._names = {cid[:12]: name for name, cid in containers.items()}
        self.samples = {name: [] for name in containers}
        self._proc = subprocess.Popen(
            ["docker", "stats", "--format", "{{.ID}}\t{{.MemUsage}}", *containers.values()],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self._proc.stdout:
            m = re.match(r"(\w+)\t([\d.]+)\s*([A-Za-z]+)\s*/", ANSI.sub("", line).strip())
            if m and m.group(1)[:12] in self._names and m.group(3) in TO_MIB:
                self.samples[self._names[m.group(1)[:12]]].append(
                    (time.perf_counter(), float(m.group(2)) * TO_MIB[m.group(3)]))

    def peak(self, name, start, end):
        values = [mib for t, mib in self.samples[name] if start <= t <= end]
        return max(values) if len(values) >= 2 else None

    def latest(self, name):
        return self.samples[name][-1][1] if self.samples[name] else None

    def close(self):
        self._proc.terminate()


# ---------------------------------------------------------------------------
# Collecting numbers
# ---------------------------------------------------------------------------

class Measurements:
    """Values per (implementation, step, kind, unit, container), one per repetition."""

    def __init__(self):
        self.values = defaultdict(list)
        self.checked = {}   # implementation -> True (MATCH) / False (skipped)

    def add(self, impl, step, kind, value, unit="s", container=None):
        if value is not None:
            self.values[(impl, step, kind, unit, container)].append(value)

    def median(self, impl, step, kind):
        for (i, s, k, _, _), values in self.values.items():
            if (i, s, k) == (impl, step, kind):
                return statistics.median(values)
        return None

    def total(self, impl, kind):
        meds = [statistics.median(v) for (i, _, k, _, _), v in self.values.items()
                if i == impl and k == kind]
        return sum(meds) if meds else None

    def rows(self):
        for (impl, step, kind, unit, container), values in self.values.items():
            yield (impl, step, kind, unit, statistics.median(values), min(values), max(values),
                   len(values), container, self.checked.get(impl, False))


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

class Dataset:
    def __init__(self, name):
        self.name = name
        self.rel = "data" if name == "data" else f"data/scaled/{name}"   # same path in bench
        self.loader_dir = "/" + self.rel                                  # mounted at /data
        missing = [t for t in TABLE_NAMES if not (ROOT / self.rel / f"{t}.csv").is_file()]
        if missing:
            hint = "" if name == "data" else f"; write it with: python bench/scale_data.py data/ --sets {name}"
            sys.exit(f"Dataset {name}: missing CSVs in {self.rel}{hint}")
        self.outputs = OUTPUTS / name
        (ROOT / self.outputs).mkdir(parents=True, exist_ok=True)

    def csv_bytes(self):
        return {t: (ROOT / self.rel / f"{t}.csv").stat().st_size for t in TABLE_NAMES}


def compare(reference, other):
    """bench/compare_outputs.py on two outputs; returns (matched, last lines of its report)."""
    proc = subprocess.run([sys.executable, str(COMPARE), str(ROOT / reference), str(ROOT / other)],
                          capture_output=True, text=True)
    return proc.returncode == 0, proc.stdout.strip()


def compare_q1(reference, other):
    """Q1 in the long layout of questions.py --out: the same rows (front_row_by,
    circuit, era), equal starts and podiums, rates within Q1_TOLERANCE.
    Returns (matched, report)."""
    def read(path):
        with open(ROOT / path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return reader.fieldnames, {(r["front_row_by"], r["circuit"], r["era"]): r for r in reader}

    (ref_cols, ref), (cols, rows) = read(reference), read(other)
    problems = [] if cols == ref_cols else [f"columns {cols}, expected {ref_cols}"]
    problems += [f"missing row {key}" for key in sorted(ref.keys() - rows.keys())]
    problems += [f"extra row {key}" for key in sorted(rows.keys() - ref.keys())]
    for key in sorted(ref.keys() & rows.keys()):
        a, b = ref[key], rows[key]
        for col in ("starts", "podiums"):
            if float(a[col]) != float(b[col]):
                problems.append(f"{key} {col}: {a[col]} vs {b[col]}")
        if abs(float(a["rate"]) - float(b["rate"])) > Q1_TOLERANCE:
            problems.append(f"{key} rate: {a['rate']} vs {b['rate']}")
    if problems:
        return False, f"Q1: {len(problems)} differences\n" + "\n".join(problems[:10])
    return True, f"Q1: {len(ref)} rows MATCH"


# ---------------------------------------------------------------------------
# pandas (bench container)
# ---------------------------------------------------------------------------

def pandas_command(bench, ds, impl, *extra):
    version = PANDAS_VERSIONS[impl]
    cmd = ["docker", "exec", bench, "python", "bench/run_pandas.py", ds.rel, "--version", version]
    if impl == "pandas-lambda":
        cmd.append("--no-questions")   # Q1-Q3 do not depend on the version
    return cmd + list(extra)


def pandas_check(bench, ds, impl):
    """Write the feature table (and for the reference also Q1); returns their paths."""
    out, q1_out = ds.outputs / f"{impl}.csv", ds.outputs / f"{impl}_q1.csv"
    extra = ["--q1-out", str(q1_out)] if impl == "pandas" else []
    run(pandas_command(bench, ds, impl, "--no-questions", "--out", str(out), *extra))
    return out, q1_out


def measure_pandas(m, sampler, idle, bench, ds, impl, runs):
    for _ in range(runs):
        _, start, end = run(["docker", "exec", bench, "python", "-c", "import pandas, numpy"])
        m.add(impl, "python + pandas", "startup", end - start, container="bench")

    m.add(impl, "idle", "memory", idle["bench"], "MiB", "bench")
    for i in range(runs):
        out, start, end = run(pandas_command(bench, ds, impl))
        result = json.loads(out.strip().splitlines()[-1])
        steps = result["steps"]
        m.add(impl, "load", "parse", steps["load"], container="bench")
        for step in ("clean", "features", "q1", "q2", "q3"):
            if step in steps:
                m.add(impl, step, "compute", steps[step], container="bench")
        m.add(impl, "form", "detail", steps["form"], container="bench")
        m.add(impl, "total", "total", end - start, container="bench")
        m.add(impl, "peak", "memory", sampler.peak("bench", start, end), "MiB", "bench")
        m.add(impl, "process max RSS", "memory", result["max_rss_mib"], "MiB", "bench")
        print(f"    run {i + 1}/{runs}: {end - start:.2f} s")


# ---------------------------------------------------------------------------
# C, Rust and R ports (bench container)
# ---------------------------------------------------------------------------

def port_steps(impl, output):
    """The step times (seconds) a port prints, by our step names."""
    names, line = (R_STEPS, r"\s*\d+:\s+(.+?)\s+([\d.]+)\s*") if impl == "r" else \
                  (CR_STEPS, r"(.+?)\s+([\d.]+) s")
    steps = {}
    for text in output.splitlines():
        m = re.fullmatch(line, text)
        if m and m.group(1).strip() in names:
            steps[names[m.group(1).strip()]] = float(m.group(2))
    missing = set(names.values()) - steps.keys()
    if missing:
        raise CommandFailed(f"{impl}: no time for {', '.join(sorted(missing))} in its output "
                            f"(did the printed format change?)\n{output[-1500:]}")
    return steps


def measure_port(m, sampler, idle, bench, ds, impl, runs):
    name, noop, ok_codes = PORT_STARTUP[impl]
    for _ in range(runs):
        _, start, end = run(["docker", "exec", bench, *noop], ok_codes=ok_codes)
        m.add(impl, name, "startup", end - start, container="bench")

    m.add(impl, "idle", "memory", idle["bench"], "MiB", "bench")
    # GNU time reports the process's max RSS; -o /dev/stdout puts it after the
    # port's own output, where the harness reads it
    timed = ["/usr/bin/time", "-f", "max_rss_kib=%M", "-o", "/dev/stdout"]
    for i in range(runs):
        out, start, end = run(["docker", "exec", bench, *timed, *PORTS[impl], ds.rel])
        steps = port_steps(impl, out)
        m.add(impl, "load", "parse", steps["load"], container="bench")
        m.add(impl, "clean", "compute", steps["clean"], container="bench")
        if impl == "r":
            # R times the join + sort before the rolling windows on its own; C
            # and Rust sort inside their form step, so form is both together
            m.add(impl, "form", "compute", steps["base"] + steps["rolling"], container="bench")
            m.add(impl, "form: join + sort", "detail", steps["base"], container="bench")
        else:
            m.add(impl, "form", "compute", steps["form"], container="bench")
        m.add(impl, "q1", "compute", steps["q1"], container="bench")
        m.add(impl, "total", "total", end - start, container="bench")
        m.add(impl, "peak", "memory", sampler.peak("bench", start, end), "MiB", "bench")
        rss = re.search(r"max_rss_kib=(\d+)", out)
        m.add(impl, "process max RSS", "memory", int(rss.group(1)) / 1024, "MiB", "bench")
        print(f"    run {i + 1}/{runs}: {end - start:.2f} s")


def port_check(bench, ds, impl):
    out, q1_out = ds.outputs / f"{impl}.csv", ds.outputs / f"{impl}_q1.csv"
    run(["docker", "exec", bench, *PORTS[impl], ds.rel, "--out", str(out), "--q1-out", str(q1_out)])
    return out, q1_out


# ---------------------------------------------------------------------------
# SQL (loader + db container)
# ---------------------------------------------------------------------------

def loader_command(ds, *command):
    return ["docker", "compose", "run", "--rm", "-T", "--no-deps",
            "-e", f"DATA_DIR={ds.loader_dir}", "loader", *command]


def psql(db, sql_text, *flags):
    return run(["docker", "exec", "-i", db, "sh", "-c", " ".join([PSQL, *flags])], stdin=sql_text)


def timed_script(name):
    """A SQL script with \\timing on for every statement (its own \\timing lines removed)."""
    lines = (ROOT / "sql" / name).read_text().splitlines(keepends=True)
    return "\\timing on\n" + "".join(l for l in lines if not re.match(r"\s*\\timing\b", l))


def psql_times(output):
    """Sum of psql's `Time:` lines in seconds, and the time of each CREATE TABLE.

    Needs psql -e (echo each statement), so a Time line can be matched to the
    CREATE TABLE before it.
    """
    total, tables, pending = 0.0, {}, None
    for line in output.splitlines():
        if m := re.match(r"Time: ([\d.]+) ms", line):
            seconds = float(m.group(1)) / 1000
            total += seconds
            if pending:
                tables[pending] = seconds
                pending = None
        elif m := re.match(r"\s*CREATE TABLE (\w+)", line, re.IGNORECASE):
            pending = m.group(1).lower()
    return total, tables


def query(db, sql_text):
    """One value from psql (unaligned, tuples only)."""
    return psql(db, sql_text, "-At")[0].strip()


def explain_seconds(db, view):
    """Planning + execution time of SELECT * FROM view, measured inside Postgres."""
    plan = json.loads(query(db, f"EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) SELECT * FROM {view}"))[0]
    return (plan["Planning Time"] + plan["Execution Time"]) / 1000


def measure_sql(m, sampler, idle, db, ds, runs):
    for _ in range(runs):
        _, start, end = run(["docker", "exec", db, "sh", "-c", PSQL + " -c 'SELECT 1'"])
        m.add("sql", "psql", "startup", end - start, container="db")
        _, start, end = run(loader_command(ds, "python", "-c", "import psycopg"))
        m.add("sql", "loader container", "startup", end - start, container="loader")

    m.add("sql", "idle", "memory", idle["db"], "MiB", "db")
    for i in range(runs):
        walls = 0.0
        out, start, end = run(loader_command(ds))
        first = start
        walls += end - start
        copy = re.search(r"total \(wall clock\)\s+([\d.]+) s", out)
        m.add("sql", "load", "parse", float(copy.group(1)), container="db")

        out, start, end = psql(db, timed_script("clean.sql"), "-e")
        walls += end - start
        m.add("sql", "clean", "compute", psql_times(out)[0], container="db")

        out, start, end = psql(db, timed_script("features.sql"), "-e")
        walls += end - start
        features, tables = psql_times(out)
        m.add("sql", "features", "compute", features, container="db")
        m.add("sql", "form", "detail", tables["features_form"], container="db")

        _, start, end = psql(db, (ROOT / "sql" / "questions.sql").read_text())   # creates the views
        walls += end - start
        for step, view in SQL_QUESTIONS.items():
            m.add("sql", step, "compute", explain_seconds(db, view), container="db")

        m.add("sql", "total", "total", walls, container="db")
        m.add("sql", "peak", "memory", sampler.peak("db", first, end), "MiB", "db")
        print(f"    run {i + 1}/{runs}: {walls:.2f} s")


def sql_check(db, ds):
    out = ds.outputs / "sql.csv"
    csv_text, _, _ = psql(db, (ROOT / "sql" / "export_features.sql").read_text(), "-q")
    (ROOT / out).write_text(csv_text)
    return out


def measure_storage(m, db, ds):
    """Sizes after the last run: the 14 raw tables (with indexes) next to their CSVs."""
    csv_sizes = ds.csv_bytes()
    sizes = query(db, "SELECT string_agg(t || '=' || pg_total_relation_size(t), ',') FROM unnest(ARRAY['"
                  + "','".join(TABLE_NAMES) + "']) t")
    table_sizes = {t: int(n) for t, n in (pair.split("=") for pair in sizes.split(","))}
    for table in TABLE_NAMES:
        m.add("csv", table, "storage", csv_sizes[table], "bytes")
        m.add("sql", table, "storage", table_sizes[table], "bytes", "db")
    m.add("csv", "all tables", "storage", sum(csv_sizes.values()), "bytes")
    m.add("sql", "all tables", "storage", sum(table_sizes.values()), "bytes", "db")
    m.add("sql", "database", "storage",
          int(query(db, "SELECT pg_database_size(current_database())")), "bytes", "db")
    m.checked["csv"] = True


# ---------------------------------------------------------------------------
# Reporting and storing
# ---------------------------------------------------------------------------

def fmt(value, unit="s"):
    if value is None:
        return "–"
    if unit == "s":
        if value < 0.01:
            return f"{value * 1000:.1f} ms"
        return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"
    if unit == "MiB":
        return f"{value:,.0f} MiB"
    return f"{value / 1e6:,.1f} MB"


def print_summary(m, ds, impls, runs):
    print(f"\n{ds.name}: medians of {runs} runs")
    print(f"  {'implementation':<15} {'startup':>9} {'parse':>9} {'compute':>9} {'total':>9} "
          f"{'peak mem':>10} {'max RSS':>10}  output")
    for impl in impls:
        check = "MATCH" if m.checked.get(impl) else "not checked"
        print(f"  {impl:<15} {fmt(m.total(impl, 'startup')):>9} {fmt(m.total(impl, 'parse')):>9} "
              f"{fmt(m.total(impl, 'compute')):>9} {fmt(m.median(impl, 'total', 'total')):>9} "
              f"{fmt(m.median(impl, 'peak', 'memory'), 'MiB'):>10} "
              f"{fmt(m.median(impl, 'process max RSS', 'memory'), 'MiB'):>10}  {check}")

    steps = ["load", "clean", "features", "form", "q1", "q2", "q3"]
    print(f"\n  {'step':<10}" + "".join(f"{impl:>15}" for impl in impls))
    for step in steps:
        kinds = ["parse"] if step == "load" else ["compute", "detail"]   # form: detail in pandas/SQL
        cells = [next((v for k in kinds if (v := m.median(impl, step, k)) is not None), None)
                 for impl in impls]
        print(f"  {step:<10}" + "".join(f"{fmt(c):>15}" for c in cells))
    print("  pandas and SQL: form is part of features, clean covers all tables.\n"
          "  C, Rust, R: only the cleaning that form needs, then form and Q1.")

    if "sql" in impls:
        csv, raw, db = (m.median("csv", "all tables", "storage"), m.median("sql", "all tables", "storage"),
                        m.median("sql", "database", "storage"))
        print(f"\n  storage: CSV files {fmt(csv, 'bytes')}, the 14 tables in Postgres {fmt(raw, 'bytes')} "
              f"({raw / csv:.1f}x), whole database {fmt(db, 'bytes')} (with _clean, features_ tables)")


def machine():
    cpu = platform.machine()
    if sys.platform == "darwin":
        brand = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
        cpu = brand.stdout.strip() or cpu
    ncpu, mem = run(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"])[0].split()
    return f"{cpu}, {platform.system()}; Docker: {ncpu} CPUs, {int(mem) / 2**30:.1f} GiB"


RESULT_COLUMNS = ["run_id", "machine", "dataset", "implementation", "step", "kind", "unit",
                  "value", "min_value", "max_value", "runs", "container", "output_checked"]


def store(db, run_id, machine_text, ds, m):
    """COPY the rows into benchmark_results through psql, as CSV on stdin."""
    data = io.StringIO()
    writer = csv.writer(data, lineterminator="\n")
    for row in m.rows():
        writer.writerow([run_id, machine_text, ds.name, *("" if v is None else v for v in row)])
    psql(db, f"COPY benchmark_results ({', '.join(RESULT_COLUMNS)}) FROM STDIN WITH (FORMAT csv);\n"
             + data.getvalue() + "\\.\n")
    return len(data.getvalue().splitlines())


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datasets", nargs="+", default=["data"], metavar="NAME",
                        help="data (the original) and/or scaled sets: 25pct, 50pct, x10, x50 (default data)")
    parser.add_argument("--impl", nargs="+", choices=IMPLEMENTATIONS, default=IMPLEMENTATIONS,
                        help="implementations to run (default all)")
    parser.add_argument("--runs", type=int, default=3, help="repetitions per implementation (default 3)")
    parser.add_argument("--skip-check", action="store_true",
                        help="do not compare the outputs with pandas (results are stored as unchecked)")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.runs < 3:
        print(f"Note: {args.runs} run(s); the issue asks for a median of 3 or more.\n")

    datasets = [Dataset(name) for name in args.datasets]
    impls = [i for i in IMPLEMENTATIONS if i in args.impl]

    if not (ROOT / ".env").is_file():
        sys.exit("No .env: cp .env.example .env and set a password (see README, Database)")
    db = container_id("db")
    print("Building and starting the bench container ...")
    run(["docker", "compose", "--profile", "bench", "--profile", "load", "build", "bench", "loader"])
    compose("up", "-d", "bench")
    bench = container_id("bench")

    psql(db, RESULTS_SCHEMA.read_text())

    run_id = datetime.now().isoformat(timespec="seconds")
    machine_text = machine()
    sampler = MemorySampler({"bench": bench, "db": db})
    time.sleep(2 * STATS_INTERVAL + 0.2)
    # Idle memory before any work: once a run has read the CSVs, docker stats
    # also counts their page cache, so "idle" between runs would be too high.
    idle = {name: sampler.latest(name) for name in ("bench", "db")}
    failed = False
    try:
        for ds in datasets:
            print(f"\n=== {ds.name} ({ds.rel}) ===")
            m = Measurements()
            mismatches = []
            reference = None
            if not args.skip_check:
                print("  check: pandas reference output")
                reference, reference_q1 = pandas_check(bench, ds, "pandas")
                m.checked["pandas"] = True

            for impl in impls:
                print(f"  {impl}")
                if impl.startswith("pandas"):
                    measure_pandas(m, sampler, idle, bench, ds, impl, args.runs)
                    if impl != "pandas" and reference:
                        ok, report = compare(reference, pandas_check(bench, ds, impl)[0])
                        m.checked[impl] = ok
                        if not ok:
                            mismatches.append((impl, report))
                elif impl in PORTS:
                    measure_port(m, sampler, idle, bench, ds, impl, args.runs)
                    if reference:
                        out, q1_out = port_check(bench, ds, impl)
                        ok, report = compare(reference, out)
                        ok_q1, report_q1 = compare_q1(reference_q1, q1_out)
                        m.checked[impl] = ok and ok_q1
                        if not ok:
                            mismatches.append((impl, report))
                        if not ok_q1:
                            mismatches.append((f"{impl} (Q1)", report_q1))
                else:
                    measure_sql(m, sampler, idle, db, ds, args.runs)
                    measure_storage(m, db, ds)
                    if reference:
                        ok, report = compare(reference, sql_check(db, ds))
                        m.checked["sql"] = ok
                        if not ok:
                            mismatches.append(("sql", report))

            print_summary(m, ds, impls, args.runs)
            if mismatches:
                failed = True
                for impl, report in mismatches:
                    print(f"\n  {impl}: output differs from pandas, nothing stored for {ds.name}:")
                    print("    " + "\n    ".join(report.splitlines()[-12:]))
                continue
            stored = store(db, run_id, machine_text, ds, m)
            print(f"\n  stored {stored} rows in benchmark_results (run_id {run_id})")
    except CommandFailed as error:
        print(f"\nCommand failed: {error}")
        failed = True
    finally:
        sampler.close()
        compose("stop", "bench")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
