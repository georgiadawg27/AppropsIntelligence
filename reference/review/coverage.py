"""
What each account's records cover, computed from the store -- never a date
to put in the workbook (Account.effective_start / effective_end were removed
in v33: each was only the first year of data on file).

    python reference/review/coverage.py [workbook] > reference/review/coverage.csv

Per account:
  first_observed_fy / last_observed_fy -- the fiscal years of its first and
      last observation (any amount type); blank if it has none
  unchecked -- the cells its subcommittee's documents on file cover (each
      cited document's own fiscal year and stage plus its also_covers) where
      the account has neither an observation nor a confirmed absence: real
      remaining work, in fiscal year and stage order
"""

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import approps_store as S  # noqa: E402

STAGE_RANK = {s: i for i, s in enumerate(S.STAGE_ORDER)}


def subcommittee_cells(conn):
    """{subcommittee: {(fiscal_year, stage)}}: every cell the documents its
    accounts' figures and confirmed absences cite cover (own stage + also_covers)."""
    docs = {d["document_id"]: dict(d) for d in conn.execute("SELECT * FROM source_document")}
    out = {}
    for sub, doc in conn.execute(
            "SELECT a.subcommittee, o.source_document_id FROM appropriations_observation o JOIN account a USING "
            "(canonical_account_id) UNION SELECT a.subcommittee, c.source_document_id FROM confirmed_absence c "
            "JOIN account a USING (canonical_account_id)"):
        out.setdefault(sub, set()).update(S.covered_cells(docs[doc])[0])
    return out


def coverage(conn):
    cells = subcommittee_cells(conn)
    out = []
    for a in conn.execute("SELECT * FROM account ORDER BY subcommittee, agency, canonical_account_id"):
        aid = a["canonical_account_id"]
        years = [r[0] for r in conn.execute(
            "SELECT fiscal_year FROM appropriations_observation WHERE canonical_account_id = ?", (aid,))]
        n_ca = conn.execute("SELECT count(*) FROM confirmed_absence WHERE canonical_account_id = ?", (aid,)).fetchone()[0]
        seen = {tuple(r) for r in conn.execute(
            "SELECT fiscal_year, stage FROM appropriations_observation WHERE canonical_account_id = ? UNION "
            "SELECT fiscal_year, stage FROM confirmed_absence WHERE canonical_account_id = ?", (aid, aid))}
        unchecked = sorted((c for c in cells.get(a["subcommittee"], set()) if c not in seen),
                           key=lambda c: (c[0], STAGE_RANK[c[1]]))
        out.append({"canonical_account_id": aid, "canonical_name": a["canonical_name"], "subcommittee": a["subcommittee"],
                    "agency": a["agency"], "first_observed_fy": min(years) if years else "",
                    "last_observed_fy": max(years) if years else "", "observations": len(years),
                    "confirmed_absences": n_ca, "unchecked_cells": len(unchecked),
                    "unchecked": "; ".join(f"FY{y} {st}" for y, st in unchecked)})
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    workbook = Path(argv[0]) if argv else S.reference_workbook()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        S.load(workbook, db)
        conn = S.connect(db, readonly=True)
        rows = coverage(conn)
        conn.close()
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main()
