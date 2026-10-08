"""
A workbook's values, sheet by sheet, as a small fingerprint: for every sheet its row and
column count and the sha256 of its cell values (formulas as their text, never styling).

    python scripts/workbook_digest.py build/Approps_Pilot_Schema_Loaded.xlsx            # print the digest
    python scripts/workbook_digest.py build/X.xlsx --compare tests/fixtures/workbook_values.json

Two workbooks with the same digest hold the same values in the same cells on the same
sheets, in the same order. tests/fixtures/workbook_values.json is the digest of the
reference build (first the v38 workbook, sha256 a859ece3...; regenerated when the data
changes on purpose), so the CI-built workbook is checked against it without a copy of the file.
"""

import argparse
import datetime as dt
import hashlib
import json
import sys

import openpyxl


def _value(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def digest(path):
    wb = openpyxl.load_workbook(path)          # not data_only: a formula is compared as its text
    out = {}
    for ws in wb.worksheets:
        rows = [[_value(c.value) for c in row] for row in ws.iter_rows()]
        while rows and all(v is None for v in rows[-1]):
            rows.pop()
        blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
        out[ws.title] = {"rows": len(rows), "columns": ws.max_column,
                         "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()}
    return out


def compare(got, want):
    """-> a list of differences (empty when the workbooks match)."""
    diffs = []
    if list(got) != list(want):
        diffs.append(f"sheets differ: {list(got)} vs {list(want)}")
    for name in want:
        if name in got and got[name] != want[name]:
            diffs.append(f"{name}: {got[name]} vs {want[name]}")
    return diffs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--compare")
    a = ap.parse_args(argv)
    got = digest(a.workbook)
    if not a.compare:
        print(json.dumps(got, indent=1))
        return 0
    with open(a.compare) as f:
        want = json.load(f)["sheets"]
    diffs = compare(got, want)
    print("workbook values match " + a.compare + " on all " + str(len(want)) + " sheets" if not diffs
          else "workbook values differ:\n  " + "\n  ".join(diffs))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
