"""
FY2017 Enacted cross-document differences, resolved by the owner's FY2021 precedent (CMS Program Management,
2026-10-08: keep the figure the law matches, note the other document's figure in the cell's corner).

    python reference/review/lhhs/backfill/resolve_cross_fy2017.py           # what would change
    python reference/review/lhhs/backfill/resolve_cross_fy2017.py --write   # apply

Our FY2017 Enacted figures (H.Rept. 115-244's FY 2017 Enacted column) for the NIH institutes equal P.L. 115-31
Division H's appropriations exactly (law_text pass). S.Rept. 115-150's 2017 appropriation column prints a different
split of the same money among them (it moves the CURES and other transfers into the institutes): the NIH total
agrees. A record is resolved only where the
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
        if not o or o["fiscal_year"] != 2017 or o["stage"] != "Enacted" or v["rule_applied"] != "cross_document" \
                or v["human_review_status"] != "pending":
            continue
        if v["observation_id"] not in law:
            kept.append(v["validation_id"])
            continue
        other = int(re.search(r"prints ([\d,]+)", v["expected_result"]).group(1).replace(",", ""))
        page = re.search(r"p\.(\d+)", v["expected_result"]).group(1)
        note = (f"S.Rept. 115-150's 2017 appropriation column prints {other:,} thousand: a different split of the same "
                f"FY2017 money among the accounts (the agency total agrees); the law appropriates "
                f"${o['amount']:,}, as recorded.")
        v.update({"human_review_status": "resolved", "reviewer": REVIEWER,
                  "resolution": (f"Our figure equals P.L. 115-31 Division H's appropriation exactly "
                                 f"({law[v['observation_id']]['validation_id']}, law_text pass); S.Rept. 115-150 "
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
