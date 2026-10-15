"""
D-6: the total cells whose accounts held, as printed, don't add up to the printed total (owner, 2026-10-14). Such a
total is not computable on every basis but As printed (site_accounting.Layer.total_layer); a printed total
measure_reconcile matched still gives its checked view.

    python reference/review/accounting/total_gaps.py          # writes total_gaps.csv beside this file

One row per cell: subcommittee, total, FY, stage, the printed total (with its source), the sum of the accounts
held, the gap, and the lines the data holds in those accounts' cells beside their headlines (supplemental,
rescission, ...), with document and page; 'lines_printed_inside' those the table prints inside the total
(owner, D-6 round 3: evidence in total_lines_inside.csv -- the row's position under the total or the table's own
printed arithmetic, never the sum matching). They count in the total's sum (site_accounting.held_lines,
line_counts); the total is published ('published' yes) only where 'short' is then 0, otherwise it stays not
computable. Lines no account holds can't be
named from the data: they need the table (blank).
"""

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import approps_store as S  # noqa: E402
import site_accounting as SA  # noqa: E402

OUT = Path(__file__).resolve().parent / "total_gaps.csv"
COLUMNS = ["subcommittee", "total", "agency", "fiscal_year", "stage", "printed_total", "printed_observation_id",
           "source_document_id", "source_page", "accounts_held", "sum_held", "gap", "lines_beside_headlines",
           "lines_printed_inside", "inside_sum", "short", "published"]


def gaps(db):
    """Every gap the layer records, over every subcommittee of the store."""
    found = []
    real = SA.Layer.annotate

    def capture(self, grid):
        out = real(self, grid)
        found.extend(self.gaps)
        return out
    SA.Layer.annotate = capture
    try:
        conn = S.connect(db, readonly=True)
        try:
            for sc in S.subcommittees(conn):
                S.subcommittee_grid(conn, sc)
        finally:
            conn.close()
    finally:
        SA.Layer.annotate = real
    return found


def rows(found):
    out = []
    for g in sorted(found, key=lambda g: (g["subcommittee"], g["total"], g["fiscal_year"], g["stage"])):
        out.append({"subcommittee": g["subcommittee"], "total": g["total"], "agency": g["agency"],
                    "fiscal_year": g["fiscal_year"], "stage": g["stage"], "printed_total": g["printed"],
                    "printed_observation_id": g["printed_observation_id"],
                    "source_document_id": g["source_document_id"], "source_page": g["source_page"] or "",
                    "accounts_held": len(g["lines"]), "sum_held": g["held"], "gap": g["gap"],
                    "lines_beside_headlines": "; ".join(SA.line_words(x) for x in g["beside"]),
                    "lines_printed_inside": "; ".join(SA.line_words(x) for x in g["inside"]),
                    "inside_sum": sum(x["amount"] for x in g["inside"]) if g["inside"] else "",
                    "short": g["gap"] - sum(x["amount"] for x in g["inside"]),
                    "published": "yes" if g["counted"] else "no"})
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        S.load(S.reference_workbook(), db)
        out = rows(gaps(db))
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    by = {}
    for r in out:
        by.setdefault((r["subcommittee"], r["total"]), []).append(r)
    for (sc, t), rs in sorted(by.items()):
        print(f"{sc} {t}: {len(rs)} cells, gap {min(r['gap'] for r in rs):,} to {max(r['gap'] for r in rs):,}; "
              f"{sum(1 for r in rs if r['published'] == 'yes')} published with the held lines counted")
    print(f"{len(out)} cells -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
