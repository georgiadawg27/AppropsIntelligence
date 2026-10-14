"""
D-6: the total cells whose accounts held, as printed, don't add up to the printed total (owner, 2026-10-14). Such a
total is not computable on every basis but As printed (site_accounting.Layer.total_layer); a printed total
measure_reconcile matched still gives its checked view.

    python reference/review/accounting/total_gaps.py          # writes total_gaps.csv beside this file

One row per cell: subcommittee, total, FY, stage, the printed total (with its source), the sum of the accounts
held, the gap, and the lines the data holds in those accounts' cells beside their headlines (supplemental,
rescission, ...: printed under an account, counted by the table's total but not by the account's headline), with
document and page: the fewest of them that add up to the gap exactly ('explains' yes), else all of them (no). Lines no account holds can't be
named from the data: they need the table (blank).
"""

import csv
import itertools
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
           "lines_sum", "explains"]


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


def beside(lines):
    """The lines held beside each account's headline in the cell (not budget authority, or a component line that
    adds to the headline), as (text, amount)."""
    out = []
    for m, mc in lines:
        for l in mc["lines"]:
            if (l["amount_type"] == "budget authority" and not l["component"]) or not l["observations"]:
                continue
            if l.get("component_kind") in ("contained", "view"):
                continue
            o = l["observations"][0]
            what = l["component_label"] or l["component"] or l["amount_type"]
            out.append((f"{m} {what} {o['amount']:,} ({o['source_document_id']} p.{o.get('source_page') or '?'})",
                        o["amount"]))
    return out


def rows(found):
    out = []
    for g in sorted(found, key=lambda g: (g["subcommittee"], g["total"], g["fiscal_year"], g["stage"])):
        b = beside(g["lines"])
        # the lines held beside the headlines that add up to the gap exactly (the fewest; all of them when none do)
        hit = next((list(c) for k in range(1, min(len(b), 8) + 1) for c in itertools.combinations(b, k)
                    if sum(x for _, x in c) == g["gap"]), None)
        b = hit or b
        s = sum(x for _, x in b)
        out.append({"subcommittee": g["subcommittee"], "total": g["total"], "agency": g["agency"],
                    "fiscal_year": g["fiscal_year"], "stage": g["stage"], "printed_total": g["printed"],
                    "printed_observation_id": g["printed_observation_id"],
                    "source_document_id": g["source_document_id"], "source_page": g["source_page"] or "",
                    "accounts_held": len(g["lines"]), "sum_held": g["held"], "gap": g["gap"],
                    "lines_beside_headlines": "; ".join(t for t, _ in b), "lines_sum": s if b else "",
                    "explains": ("yes" if s == g["gap"] else "no") if b else ""})
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
              f"{sum(1 for r in rs if r['explains'] == 'yes')} explained by lines held beside headlines")
    print(f"{len(out)} cells -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
