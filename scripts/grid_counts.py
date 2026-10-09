"""
The published grid's counts, as a fixture the tests compare against: each subcommittee's state counts and the
number of headline cells whose observation is flagged. Regenerate with the workbook fingerprint whenever
data/staged.json changes on purpose (after export_static.py); the diff shows what a change moved.

    python scripts/grid_counts.py > tests/fixtures/grid_counts.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def headline_observations(g):
    """The observation each grid cell shows (the page's headOf: the budget authority line with no component, else
    the first line), over every row, title total and bill total."""
    rows = g["rows"] + [t["total"] for t in g.get("titles", []) if t.get("total")] + \
        ([g["bill_total"]] if g.get("bill_total") else [])
    for row in rows:
        for key, cell in row["cells"].items():
            if not cell["lines"]:
                continue
            h = next((l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"]),
                     cell["lines"][0])
            if h["observations"]:
                yield row, key, h["observations"][0]


def counts():
    out = {"note": "the published grid's counts (scripts/grid_counts.py); regenerate when data/staged.json changes "
                   "on purpose", "state_counts": {}, "flagged_cells": {}}
    for code in json.loads((DOCS / "data" / "subcommittees.json").read_text())["subcommittees"]:
        g = json.loads((DOCS / "data" / "subcommittees" / f"{code}.json").read_text())
        out["state_counts"][code] = g["state_counts"]
        out["flagged_cells"][code] = sum(1 for _, _, o in headline_observations(g) if o["verification_status"] == "flagged")
    return out


if __name__ == "__main__":
    json.dump(counts(), sys.stdout, indent=1)
    sys.stdout.write("\n")
