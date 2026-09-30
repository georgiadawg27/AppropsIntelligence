"""
Title candidates for the accounts the workbook leaves without a title,
derived only from their own observations' source_table_or_section text
("Title II, ..."), for a person to confirm before it goes into the Account tab.

    python reference/review/mechanism_titles.py [workbook] > reference/review/mechanism_titles.csv

  basis = "own line"   -- the account's (nonzero) budget authority line sits in
                          one title: that title is where the bill appropriates
                          it. A $0 budget authority line (a rescission's
                          companion) doesn't count as one.
          "transfer or rescission only" -- no budget authority line loaded;
                          the only title evidence is a transfer / rescission
                          line, which a general provision can carry from
                          another title -- weak
          "ambiguous"  -- no budget authority line, and its other lines sit in
                          more than one title: no candidate is proposed
"""

import csv
import re
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import approps_store as S  # noqa: E402

TITLE_RE = re.compile(r"^\s*Title\s+([IVXL]+)\b", re.I)


def candidates(conn):
    out = []
    for a in conn.execute("SELECT canonical_account_id, canonical_name, agency FROM account WHERE title IS NULL "
                          "ORDER BY agency, canonical_account_id"):
        own, other, cites = OrderedDict(), OrderedDict(), []
        for o in conn.execute("SELECT observation_id, amount_type, amount, source_table_or_section FROM appropriations_observation "
                              "WHERE canonical_account_id = ? ORDER BY observation_id", (a["canonical_account_id"],)):
            m = TITLE_RE.match(o["source_table_or_section"] or "")
            title = f"Title {m.group(1).upper()}" if m else None
            cites.append(f"{o['observation_id']} {o['amount_type']}: {o['source_table_or_section']!r}")
            if title:
                (own if o["amount_type"] == "budget authority" and o["amount"] else other).setdefault(title, []).append(o["observation_id"])
        if len(own) == 1:
            cand, basis = next(iter(own)), "own line"
        elif own:
            cand, basis = "", "ambiguous"
        elif len(other) == 1:
            cand, basis = next(iter(other)), "transfer or rescission only"
        else:
            cand, basis = "", "ambiguous"
        seen = OrderedDict((t, None) for t in list(own) + list(other))
        out.append({"canonical_account_id": a["canonical_account_id"], "canonical_name": a["canonical_name"],
                    "agency": a["agency"], "candidate_title": cand, "basis": basis,
                    "titles_seen": "; ".join(seen), "evidence": " | ".join(cites)})
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    workbook = Path(argv[0]) if argv else S.reference_workbook()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        S.load(workbook, db)
        conn = S.connect(db, readonly=True)
        rows = candidates(conn)
        conn.close()
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main()
