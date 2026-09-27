"""
Account effective_start / effective_end computed from the observations on
file, for review before they go into the workbook's Account tab.

    python reference/review/effective_dates.py [workbook] > reference/review/effective_dates.csv

  effective_start = 1 October before the account's earliest observed fiscal
                    year (a fiscal year starts 1 October of the prior year).
  effective_end   = 30 September of the account's last observed fiscal year,
                    set only when that is earlier than the last fiscal year
                    observed for any account of the same agency -- i.e. the
                    agency's figures go on without it. An account whose last
                    figure is simply the last one collected for its agency is
                    left open: the data can't tell "ended" from "not collected
                    yet".

Observations only (confirmed absences say a line wasn't printed, not that the
account existed). Both dates describe what is on file, which for the
FY2027-only accounts is the pilot's coverage, not when the account began.
"""

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import approps_store as S  # noqa: E402

STAGE_RANK = {s: i for i, s in enumerate(S.STAGE_ORDER)}


def spans(conn):
    agency_last = dict(conn.execute(
        "SELECT a.agency, max(o.fiscal_year) FROM appropriations_observation o "
        "JOIN account a USING (canonical_account_id) GROUP BY a.agency"))
    out = []
    for a in conn.execute("SELECT * FROM account ORDER BY agency, canonical_account_id"):
        obs = sorted(conn.execute(
            "SELECT observation_id, fiscal_year, stage, source_document_id FROM appropriations_observation "
            "WHERE canonical_account_id = ?", (a["canonical_account_id"],)),
            key=lambda o: (o["fiscal_year"], STAGE_RANK[o["stage"]], o["observation_id"]))
        first, last = obs[0], obs[-1]
        start = f"{first['fiscal_year'] - 1}-10-01"
        ended = last["fiscal_year"] < agency_last[a["agency"]]
        end = f"{last['fiscal_year']}-09-30" if ended else ""
        cite = lambda o: f"{o['observation_id']} FY{o['fiscal_year']} {o['stage']} ({o['source_document_id']})"
        changes = [c for c, old, new in (("start", a["effective_start"], start), ("end", a["effective_end"] or "", end))
                   if old != new]
        out.append({
            "canonical_account_id": a["canonical_account_id"], "canonical_name": a["canonical_name"],
            "agency": a["agency"],
            "workbook_effective_start": a["effective_start"], "workbook_effective_end": a["effective_end"] or "",
            "computed_effective_start": start, "computed_effective_end": end,
            "change": " + ".join(changes) or "none",
            "first_observation": cite(first), "last_observation": cite(last),
            "agency_last_observed_fy": agency_last[a["agency"]],
            "basis": ("last figure FY%d; the agency's figures continue to FY%d" % (last["fiscal_year"], agency_last[a["agency"]])
                      if ended else "open: last figure is the agency's last collected fiscal year")
                     + ("; start is the pilot's first collected year (FY2027 only), not the account's origin"
                        if first["fiscal_year"] == agency_last[a["agency"]] and first["fiscal_year"] > 2017 else ""),
        })
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    workbook = Path(argv[0]) if argv else next((ROOT / "reference").glob("*.xlsx"))
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        S.load(workbook, db)
        conn = S.connect(db, readonly=True)
        rows = spans(conn)
        conn.close()
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main()
