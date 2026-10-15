"""
Every account's derived lifecycle (approps_store.lifecycle), from the published grid, for the owner to check.

    python reference/review/account_lifecycle.py > reference/review/account_lifecycle.csv

Columns: subcommittee, account, name, lifecycle type, first (the first evidence: 'before FY2017' when it is the
first year on file), proposed first-last and enacted first-last (each from figures and confirmed absences), the
year created (a new account's first enacted figure), predecessor / successor, enacted figures not yet collected,
the start_basis override and its reason (an 'unidentified' start: the years before the first evidence stay
missing), the account's relationships (id, type, other account; each listed on its 'from' side), and the cells
blank outside the account's life.
"""

import csv
import json
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "data"
sys.path.insert(0, str(DOCS.parents[1]))
from export_static import read_grid  # noqa: E402


def rows():
    for code in json.loads((DOCS / "subcommittees.json").read_text())["subcommittees"]:
        g = read_grid(DOCS, code)
        every = g["rows"] + [t["total"] for t in g["titles"] if t["total"]] + ([g["bill_total"]] if g.get("bill_total") else [])
        for r in every:
            L, a = r["lifecycle"], r["account"]
            span = lambda s: f"FY{s['first']}-FY{s['last']}" if s and s["first"] is not None else ""
            yield {"subcommittee": code, "account": a["canonical_account_id"], "name": a["canonical_name"],
                   "type": L["type"], "first": L["first_label"] or "", "proposed": span(L["proposed"]),
                   "enacted": span(L["enacted"]), "created": L.get("created", ""),
                   "predecessor": L.get("predecessor", ""), "successor": L.get("successor", ""),
                   "enacted_not_collected": "yes" if L.get("enacted_not_collected") else "",
                   "start_basis": L.get("start_basis", ""), "start_basis_reason": L.get("start_basis_reason") or "",
                   "relationships": "; ".join(f"{x['relationship_id']} {x['relationship_type']} -> {x['to_account_id']}"
                                              for x in r["relationships"] if x["from_account_id"] == a["canonical_account_id"]),
                   "blank_cells": sum(1 for c in r["cells"].values() if c.get("outside_life"))}


if __name__ == "__main__":
    out = sorted(rows(), key=lambda x: (x["subcommittee"], x["type"] != "ongoing", x["type"], x["account"]))
    w = csv.DictWriter(sys.stdout, fieldnames=list(out[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(out)
