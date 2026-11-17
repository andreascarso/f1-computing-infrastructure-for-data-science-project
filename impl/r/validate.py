"""Run unchanged Python references and validate the R port. Requires Python >=3.11.
Usage: /opt/anaconda3/bin/python3 impl/r/validate.py data/ --out bench/outputs/r-validation
"""
import argparse
from pathlib import Path
import subprocess
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Use Python >=3.11 with pandas and numpy (see impl/r/README.md).")
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "impl/python"))
from load import load_tables
from clean import clean_tables
from features import (FORM_FUNCTIONS, base_table, build_features, write_output,
                      leakage_check, FEATURE_GROUPS)
from questions import q1, tidy_q1, rank_correlation

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("data_dir", type=Path)
parser.add_argument("--out", type=Path, default=Path("bench/outputs/r-validation"))
args = parser.parse_args()
data_dir, out = args.data_dir.resolve(), args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
print(f"Python {sys.version.split()[0]}, pandas {pd.__version__}, numpy {np.__version__}", flush=True)

def run(*command):
    subprocess.run([str(x) for x in command], cwd=ROOT, check=True)

run("Rscript", ROOT / "impl/r/run.R", data_dir, "--out", out / "r.csv",
    "--q1-out", out / "r_q1.csv", "--repeat", "3")
run("Rscript", ROOT / "impl/r/validate.R", data_dir, out)
tables, _ = load_tables(data_dir, verbose=False)
missing = pd.read_csv(out / "r_missing.csv")
for row in missing.itertuples(index=False):
    assert tables[row.dataset][row.column].isna().sum() == row.missing, (row.dataset, row.column)
clean, _ = clean_tables(tables, verbose=False)
reference, _ = build_features(clean, "B")
leakage_check(reference)
write_output(reference, out / "pandas.csv")
# Test partial output support against the existing comparator, not a copy.
run(sys.executable, ROOT / "bench/compare_outputs.py", out / "pandas.csv", out / "r.csv")
keys = ["raceId", "driverId"]
features = FEATURE_GROUPS["driver_form"] + FEATURE_GROUPS["constructor_form"]
a = reference.sort_values(keys).reset_index(drop=True)
b = pd.read_csv(out / "r_diagnostics.csv").sort_values(keys).reset_index(drop=True)
pd.testing.assert_frame_equal(a[keys], b[keys], check_dtype=False)
for column in features:
    np.testing.assert_array_equal(a[column].isna(), b[column].isna())
    np.testing.assert_allclose(a[column], b[column], rtol=0, atol=1e-12, equal_nan=True)
chronological = reference.sort_values(["date", "raceId", "driverId"])
circuit_source = chronological.groupby(["driverId", "circuitId"])["date"].shift(1)
expected_circuit = chronological.assign(source=circuit_source).sort_values(keys)["source"].reset_index(drop=True)
pd.testing.assert_series_equal(expected_circuit, pd.to_datetime(b["drv_circuit_source_date"]), check_names=False)
for column in ["drv_form_source_date", "cons_form_source_date"]:
    pd.testing.assert_series_equal(a[column], pd.to_datetime(b[column]), check_names=False)
selection = clean["results"][keys + ["resultId", "selection_method"]].sort_values(keys).reset_index(drop=True)
pd.testing.assert_frame_equal(selection, pd.read_csv(out / "r_selection.csv"), check_dtype=False)
# Both unchanged Python implementations must agree on the form features.
form_a = FORM_FUNCTIONS["A"](base_table(clean)).sort_values(keys).reset_index(drop=True)
pd.testing.assert_frame_equal(a[features], form_a[features], check_exact=True)
answers = q1(clean)
qref = pd.concat([tidy_q1(answers[0], "grid"), tidy_q1(answers[1], "qualifying")])
qkeys = ["front_row_by", "circuit", "era"]
qref = qref.sort_values(qkeys).reset_index(drop=True)
qr = pd.read_csv(out / "r_q1.csv").sort_values(qkeys).reset_index(drop=True)
pd.testing.assert_frame_equal(qref.drop(columns="rate"), qr.drop(columns="rate"), check_dtype=False)
np.testing.assert_allclose(qref.rate.to_numpy(dtype=float), qr.rate.to_numpy(dtype=float),
                           rtol=0, atol=1e-12)
qref.to_csv(out / "pandas_q1.csv", index=False)
corr = pd.read_csv(out / "r_correlations.csv")
np.testing.assert_allclose(corr.correlation, [rank_correlation(x) for x in answers],
                           rtol=0, atol=1e-12, equal_nan=True)
print("PASS: selected rows, raw feature values (1e-12), missing masks, source dates, Python A/B, Q1 counts/rates/ranks.", flush=True)
# Exact six-decimal equivalence is a stronger, separate test.
run(sys.executable, ROOT / "bench/compare_outputs.py", out / "pandas.csv", out / "r.csv", "--tol", "0")
