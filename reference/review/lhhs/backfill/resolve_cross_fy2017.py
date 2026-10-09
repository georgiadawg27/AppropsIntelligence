"""
FY2017 Enacted cross-document differences, resolved by the owner's FY2021 precedent (CMS Program Management,
2026-10-08: keep the figure the law matches, note the other document's figure in the cell's corner).

    python reference/review/lhhs/backfill/resolve_cross_fy2017.py           # what would change
    python reference/review/lhhs/backfill/resolve_cross_fy2017.py --write   # apply

Our FY2017 Enacted figures (H.Rept. 115-244's FY 2017 Enacted column) for the NIH institutes equal P.L. 115-31
Division H's appropriations exactly (law_text pass). S.Rept. 115-150's 2017 appropriation column prints a different
split of the same money among them (it moves the CURES and other transfers into the institutes): the NIH total
agrees. A record is resolved only where the
law_text record on the same observation passes -- or, for the three whose law_text record can't pass on its own
(LAW_SUMS: an amount in another paragraph, or a set-aside inside the first amount), where our figure is the exact sum
of amounts the law states, each quoted and checked on its page here.
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
LAW_PDF = ROOT / "document_store" / "PLAW-115publ31.pdf"
# account -> ([(signed amount, page, quote checked on the page)], what the sum is)
LAW_SUMS = {
    "ACC-HHS-NIH-CURES": ([(300_000_000, 390, r"\$300,000,000 was previously appropriated for fiscal year 2017 by section "
                                              r"194"),
                           (52_000_000, 393, r"\$52,000,000 in the NIH Innovation Fund previously appropriated for fiscal "
                                             r"year 2017")],
                          "the 21st Century Cures Act money P.L. 115-31 names as previously appropriated for FY2017 (by P.L. "
                          "114-254, sec. 194): $300,000,000 under 'National Cancer Institute' (p.390) + $52,000,000 under "
                          "'Office of the Director' (p.393)"),
    "ACC-HHS-NIH-NIGMS": ([(2_650_838_000, 391, r"general medical sciences, \$2,650,838,000"),
                           (-824_443_000, 391, r"of which \$824,443,000 shall be from funds available under section 241")],
                          "'National Institute of General Medical Sciences' (p.391): $2,650,838,000, of which $824,443,000 "
                          "from the PHS evaluation set-aside (section 241) -- the budget authority is the difference"),
    "ACC-HHS-NIH-OD": ([(1_665_183_000, 393, r"Office of the Director, NIH, \$1,665,183,000"),
                        (12_600_000, 393, r"\$12,600,000 is appropriated to the Common Fund from the 10-year Pediatric "
                                          r"Research Initiative Fund")],
                       "'Office of the Director' (p.393): $1,665,183,000 + the $12,600,000 for the Gabriella Miller Kids "
                       "First Research Act appropriated under the same heading"),
}


def law_sum(aid):
    sys.path.insert(0, str(HERE))
    from standing_rules_fy2017 import pages
    text = pages(LAW_PDF)
    parts, why = LAW_SUMS[aid]
    for _, page, quote in parts:
        assert re.search(quote, text[page - 1]), (aid, page, quote)
    return sum(a for a, _, _ in parts), why


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
            if o["canonical_account_id"] not in LAW_SUMS or o["component"]:
                kept.append(v["validation_id"])
                continue
            total, why = law_sum(o["canonical_account_id"])
            assert total == o["amount"], (o["observation_id"], total, o["amount"])
            other = int(re.search(r"prints ([\d,]+)", v["expected_result"]).group(1).replace(",", ""))
            page = re.search(r"p\.(\d+)", v["expected_result"]).group(1)
            v.update({"human_review_status": "resolved", "reviewer": REVIEWER,
                      "resolution": (f"Our figure is exactly what P.L. 115-31 Division H states: {why} = "
                                     f"${total:,}. S.Rept. 115-150 (p.{page}) prints {other:,} thousand: another split "
                                     "of the same FY2017 money. Kept the figure the law matches (the owner's FY2021 CMS "
                                     f"precedent, 2026-10-08). Note: S.Rept. 115-150's 2017 appropriation column prints "
                                     f"{other:,} thousand; the law's amounts sum to ${total:,}, as recorded.")})
            done.append((v["validation_id"], o["canonical_account_id"], o["amount"] // 1000, other))
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
