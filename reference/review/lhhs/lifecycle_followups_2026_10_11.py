"""
Lifecycle follow-ups to PR #41 (owner, 2026-10-11). Changes data/staged.json; never an ID, figure or status.

    python reference/review/lhhs/lifecycle_followups_2026_10_11.py           # what would change
    python reference/review/lhhs/lifecycle_followups_2026_10_11.py --write   # apply

1. Account.start_basis 'unidentified' (with its required reason) on the Medicare Title XVIII limitation: its
   FY2026 provision is not yet identified in earlier years, so the lifecycle does not start at its first year
   with evidence -- its earlier cells read missing again, not blank. When the provision is identified the
   override is removed and the normal lifecycle rule applies.
2. The four proposals that fold whole accounts or agencies into a new or existing body are
   proposed_consolidation, not proposed_move (their IDs kept): HRSA and SAMHSA into the Administration for a
   Healthy America; NIDA and NIAAA into the proposed National Institute of Substance Use and Addiction Research.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import staged_data  # noqa: E402
STAGED = ROOT / "data" / "staged"
LIMITATION = "ACC-HHS-GP-MEDICARE-LIMITATION"
REASON = "FY2026 provision not yet identified; earlier years unknown."
# relationship -> (from, to) as they stand; each is re-typed proposed_move -> proposed_consolidation
CONSOLIDATIONS = {
    "REL-LHHS-0001": ("ACC-HHS-HRSA-TOTAL", "ACC-HHS-AHA-TOTAL"),
    "REL-LHHS-0002": ("ACC-HHS-SAMHSA-TOTAL", "ACC-HHS-AHA-TOTAL"),
    "REL-LHHS-0005": ("ACC-HHS-NIH-NIDA", "ACC-HHS-NIH-SUBSTANCE-USE"),
    "REL-LHHS-0006": ("ACC-HHS-NIH-NIAAA", "ACC-HHS-NIH-SUBSTANCE-USE"),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = staged_data.load()
    acct = next(x for x in data["accounts"] if x["canonical_account_id"] == LIMITATION)
    if acct.get("start_basis") != "unidentified":
        acct["start_basis"], acct["start_basis_reason"] = "unidentified", REASON
        print(f"{LIMITATION}: start_basis 'unidentified' -- {REASON}")
    rels = {r["relationship_id"]: r for r in data["relationships"]}
    for rid, (frm, to) in CONSOLIDATIONS.items():
        r = rels[rid]
        assert (r["from_account_id"], r["to_account_id"]) == (frm, to), rid
        assert r["evidence"].startswith(("Proposed, not enacted", "As REL-")), rid
        if r["relationship_type"] != "proposed_consolidation":
            assert r["relationship_type"] == "proposed_move", (rid, r["relationship_type"])
            print(f"{rid} {frm} -> {to}: proposed_move -> proposed_consolidation")
            r["relationship_type"] = "proposed_consolidation"
    if a.write:
        staged_data.save(data)
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
