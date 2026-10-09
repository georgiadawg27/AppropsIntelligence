"""
FY2020 Enacted cross-document differences, resolved by the owner's FY2021 precedent (CMS Program Management,
2026-10-08: keep the figure the law matches, note the other document's figure in the cell's corner).

    python reference/review/lhhs/backfill/resolve_cross_fy2020.py           # what would change
    python reference/review/lhhs/backfill/resolve_cross_fy2020.py --write   # apply

Our FY2020 Enacted figures (H.Rept. 116-450's FY 2020 Enacted column) for 17 NIH institutes and SAMHSA's Mental
Health and Substance Abuse Treatment accounts equal P.L. 116-94 Division A's appropriations exactly (law_text pass).
The FY2021 Senate draft's 2020 column prints a different split of the same money among them: the differences net to
zero within NIH and within SAMHSA, and the NIH, SAMHSA and Title II totals agree. A record is resolved only where the
law_text record on the same observation passes.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
REVIEWER = "owner rules (2026-10-08)"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    law = {v["observation_id"]: v for v in data["validations"] if v["rule_applied"] == "law_text"
           and v["result"] == "pass" and "(enrolled)" in (v["expected_result"] or "")}
    done, kept = [], []
    for v in data["validations"]:
        o = obs.get(v["observation_id"])
        if not o or o["fiscal_year"] != 2020 or o["stage"] != "Enacted" or v["rule_applied"] != "cross_document" \
                or v["human_review_status"] != "pending":
            continue
        if v["observation_id"] not in law:
            kept.append(v["validation_id"])
            continue
        other = int(re.search(r"prints ([\d,]+)", v["expected_result"]).group(1).replace(",", ""))
        page = re.search(r"p\.(\d+)", v["expected_result"]).group(1)
        note = (f"The FY2021 Senate draft's 2020 column prints {other:,} thousand: a different split of the same "
                f"FY2020 money among the accounts (the agency total agrees); the law appropriates "
                f"${o['amount']:,}, as recorded.")
        v.update({"human_review_status": "resolved", "reviewer": REVIEWER,
                  "resolution": (f"Our figure equals P.L. 116-94 Division A's appropriation exactly "
                                 f"({law[v['observation_id']]['validation_id']}, law_text pass); the FY2021 Senate draft "
                                 f"(p.{page}) prints another split of the same money -- the differences net to zero "
                                 f"within the agency and its total agrees. Kept the figure the law matches (the owner's "
                                 f"FY2021 CMS precedent, 2026-10-08). Note: {note}")})
        done.append((v["validation_id"], o["canonical_account_id"], o["amount"] // 1000, other))
    checks = {}
    for v in data["validations"]:
        checks.setdefault(v["observation_id"], []).append((v["rule_applied"], v["result"], v["expected_result"],
                                                           v["human_review_status"], v["resolution"]))
    changed = []
    for vid, aid, *_ in done:
        oid = next(v["observation_id"] for v in data["validations"] if v["validation_id"] == vid)
        o = obs[oid]
        new = V.verification_status(o["confidence"], checks[oid])
        if new != o["verification_status"]:
            changed.append((oid, o["verification_status"], new))
            o["verification_status"] = new
    for d in done:
        print("resolved", *d)
    print(f"{len(done)} resolved, {len(kept)} kept pending {kept}; statuses: {changed}")
    if a.write:
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
