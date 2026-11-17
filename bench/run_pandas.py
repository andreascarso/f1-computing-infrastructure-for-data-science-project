"""Run the pandas implementation once and print its step times as JSON.

Called by bench/benchmark.py inside the bench container; one process is one
repetition, so the harness can time process startup separately. Uses the
functions of impl/python/ unchanged.

Steps (seconds, printed as one JSON line on stdout):
    load      read the 14 CSVs (Section 2)                     parse
    clean     clean all tables (Section 4)                     compute
    features  feature table 6.1-6.6, version A or B            compute
    form      6.4 driver/constructor form, part of features    (detail)
    q1-q3     the three questions (Section 5)                  compute
Plus max_rss_mib: the process's own peak memory (resident set size), which
unlike docker stats does not include the page cache of the files it read.

Usage:
    python bench/run_pandas.py data/ --version B
    python bench/run_pandas.py data/ --version A --no-questions --out bench/outputs/data/pandas-lambda.csv
"""

import argparse
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "impl" / "python"))

from clean import clean_tables  # noqa: E402
from features import build_features, write_output  # noqa: E402
from load import load_tables  # noqa: E402
from questions import q1, q2, q3  # noqa: E402

FORM_STEP = "6.4 driver/constructor form"


def timed(step):
    start = time.perf_counter()
    out = step()
    return out, time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--version", choices=["A", "B"], default="B",
                        help="A = transform(lambda), B = vectorised (default)")
    parser.add_argument("--no-questions", action="store_true", help="skip Q1-Q3")
    parser.add_argument("--out", type=Path, metavar="CSV",
                        help="write the feature table in the shared output format (not timed)")
    args = parser.parse_args()

    steps = {}
    (tables, _), steps["load"] = timed(lambda: load_tables(args.data_dir, verbose=False))
    (clean, _), steps["clean"] = timed(lambda: clean_tables(tables, verbose=False))
    (features, step_times), steps["features"] = timed(lambda: build_features(clean, args.version))
    steps["form"] = step_times[FORM_STEP]
    if not args.no_questions:
        for name, query in (("q1", q1), ("q2", q2), ("q3", q3)):
            _, steps[name] = timed(lambda: query(clean))

    if args.out:
        write_output(features, args.out)
    max_rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024   # Linux: KiB
    print(json.dumps({"steps": steps, "max_rss_mib": max_rss_mib, "feature_rows": len(features)}))


if __name__ == "__main__":
    main()
