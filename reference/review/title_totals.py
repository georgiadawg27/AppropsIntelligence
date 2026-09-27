"""
Reconcile each printed "Total, title III, Science" (title_iii_printed.csv,
read from the comparative tables) against the store, without adjusting
either: a printed figure is the document's own and stays as printed.

    python reference/review/title_totals.py [workbook] > reference/review/title_iii_totals.csv

For a fiscal year x stage cell the store holds, for the title's accounts
(rollups left out, so nothing counts twice):
  base    -- every budget authority line and every emergency (supplemental)
             line without a structural component: what a title's own lines
             always carry
  others  -- each rescission, budget amendment and supplemental-act line,
             one by one
A printed total reconciles when it equals base plus some set of the others;
the report names which of them the document's total includes and which it
leaves out (a document prints them in Title III, in Title V, or under
Other Appropriations -- nothing here assumes which).

The same check is made a second way, through the stored rollups: the
agency totals (NASA Total, NSF Total, as printed) stand in for their
accounts' base lines. A rollup edited away from its printed figure shows
up there even when its accounts reconcile.
"""

import csv
import itertools
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import approps_store as S  # noqa: E402

PRINTED = ROOT / "reference" / "review" / "title_iii_printed.csv"


def lines(conn, accts, fy, stage):
    return conn.execute("SELECT observation_id, canonical_account_id, amount_type, component, amount FROM appropriations_observation "
                        "WHERE fiscal_year = ? AND stage = ? AND canonical_account_id IN (%s) ORDER BY observation_id"
                        % ",".join("?" * len(accts)), (fy, stage, *accts)).fetchall()


def is_base(r):
    return r["amount_type"] == "budget authority" or (r["amount_type"] == "supplemental" and r["component"] is None)


def cell_lines(conn, title, fy, stage):
    """-> (base lines of the title's accounts, their other lines, base lines
    through the rollups: each agency rollup's budget authority in place of
    its accounts' base lines)."""
    accts = [dict(a) for a in conn.execute("SELECT canonical_account_id, agency, notes FROM account WHERE title = ?", (title,))]
    plain = [a for a in accts if not S.rollup_scope(a)]
    rollups = [a for a in accts if S.rollup_scope(a) == "agency"]
    rows = lines(conn, [a["canonical_account_id"] for a in plain], fy, stage)
    base = [r for r in rows if is_base(r)]
    others = [r for r in rows if not is_base(r)]
    rolled = {a["agency"] for a in rollups}
    via = [r for r in lines(conn, [a["canonical_account_id"] for a in rollups], fy, stage) if r["amount_type"] == "budget authority"]
    via += [r for r in base if next(a["agency"] for a in plain if a["canonical_account_id"] == r["canonical_account_id"]) not in rolled]
    return base, others, via


def reconcile(conn, printed, title="Title III"):
    out = []
    for p in printed:
        fy, stage, want = int(p["fiscal_year"]), p["stage"], int(p["printed_total_title_iii_thousands"]) * 1000
        base, others, via = cell_lines(conn, title, fy, stage)
        b = sum(r["amount"] for r in base)
        label = lambda r: f"{r['observation_id']} ({r['canonical_account_id']} {r['component'] or r['amount_type']} {r['amount'] // 1000:,})"

        def explain(total):
            for n in range(len(others) + 1):
                combo = next((c for c in itertools.combinations(others, n) if total + sum(r["amount"] for r in c) == want), None)
                if combo is not None:
                    return combo
            return None
        found = explain(b) if base else None
        via_found = explain(sum(r["amount"] for r in via)) if via else None
        out.append({**p, "store_base_thousands": b // 1000 if base else "",
                    "reconciles": "yes" if found is not None else ("no store figures" if not base else "no"),
                    "printed_total_includes": "; ".join(label(r) for r in found or ()),
                    "printed_total_leaves_out": "; ".join(label(r) for r in others if found is not None and r not in found),
                    "difference_thousands": "" if found is not None or not base else (want - b) // 1000,
                    "reconciles_through_rollups": "yes" if via_found is not None else ("no store figures" if not via else "no"),
                    "through_rollups_differs_by_thousands": "" if via_found is None and not via else
                        ("" if via_found is not None else (want - sum(r["amount"] for r in via)) // 1000)})
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    workbook = Path(argv[0]) if argv else next((ROOT / "reference").glob("*.xlsx"))
    with open(PRINTED, newline="") as f:
        printed = list(csv.DictReader(f))
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        S.load(workbook, db)
        conn = S.connect(db, readonly=True)
        rows = reconcile(conn, printed)
        conn.close()
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main()
