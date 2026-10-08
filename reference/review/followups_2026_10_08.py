"""
Four follow-ups from PRs #30-#31 (owner, 2026-10-08). Appends and edits data/staged.json; no figure changes,
no ID reused.

    python reference/review/followups_2026_10_08.py           # what would change
    python reference/review/followups_2026_10_08.py --write   # apply

1. OBS-0771 (NSF R&RA, FY2025 request, emergency piece, 420,000): re-sourced to S.Rept. 118-198 p.227, which
   prints it ('Research and related activities (emergency)', Budget estimate column); a cross_document
   record that passes on H.Rept. 118-582 p.248's printed subtotal. The page-check item is resolved by the
   owner (page_check_read.py OWNER).
2. The status rule (validate_approps): below the confidence threshold, a passing check against another
   document (law_text, cross_document) confirms; statuses recomputed here.
3. LIHEAP FY2023 (OBS-LHHS-0014): law_text matches across divisions H + N of P.L. 117-328 (law_text.ACROSS);
   the cell carries the note.
4. SAMHSA Mental Health FY2023 (OBS-LHHS-1664): law_text stays info, with the reason (P.L. 117-180's
   $62,000,000 for 988 Suicide Lifeline; law_text.ACROSS).
"""

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import validate_approps as V  # noqa: E402
import law_text as L  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
NOTE_0771 = ("emergency piece of the FY2025 request; printed in the Senate report (p.227); not printed as its own "
             "line in H.Rept. 118-582")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    vals = data["validations"]

    # 1. OBS-0771
    o = obs["OBS-0771"]
    base, defense = obs["OBS-0695"], obs["OBS-0735"]
    assert (o["amount"], base["amount"], defense["amount"]) == (420_000_000, 7_519_320_000, 106_000_000)
    assert base["amount"] + defense["amount"] + o["amount"] == 8_045_320_000
    if o["source_document_id"] != "SRC-CRPT-118SRPT198":
        o.update({"source_document_id": "SRC-CRPT-118SRPT198", "source_page": "227", "extraction_method": "human-entered",
                  "source_table_or_section": f"{o['source_table_or_section'].split(' -- ')[0]} -- {NOTE_0771}"})
        n = max(int(v["validation_id"][4:]) for v in vals if v["validation_id"][4:].isdigit())
        vals.append({"validation_id": f"VAL-{n + 1:04d}", "observation_id": "OBS-0771", "rule_applied": "cross_document",
                     "expected_result": "H.Rept. 118-582 p.248 prints the NSF Research and related activities FY2025 "
                                        "request subtotal 8,045,320 = base 7,519,320 (OBS-0695) + defense 106,000 "
                                        "(OBS-0735) + 420,000",
                     "observed_result": "7,519,320 + 106,000 + 420,000 = 8,045,320 (thousands); 420,000 as recorded "
                                        "(S.Rept. 118-198 p.227, 'Research and related activities (emergency)', "
                                        "Budget estimate column)",
                     "result": "pass", "human_review_status": "", "reviewer": "", "resolution": ""})
        print("OBS-0771 re-sourced to SRC-CRPT-118SRPT198 p.227; cross_document", vals[-1]["validation_id"])

    # the Senate report prints the FY2025 request column it is now cited for
    srpt = next(d for d in data["source_docs"] if d["document_id"] == "SRC-CRPT-118SRPT198")
    if "FY2025 President's Budget" not in (srpt.get("also_covers") or ""):
        srpt["also_covers"] = "; ".join(x for x in (srpt.get("also_covers"), "FY2025 President's Budget") if x)
        print("SRC-CRPT-118SRPT198 also_covers:", srpt["also_covers"])

    # 3 and 4. the law_text records that read across divisions or laws
    for oid, (res, exp, obsd) in L.ACROSS.items():
        v = next(v for v in vals if v["observation_id"] == oid and v["rule_applied"] == "law_text")
        if (v["result"], v["expected_result"], v["observed_result"]) != (res, exp, obsd):
            v.update({"result": res, "expected_result": exp, "observed_result": obsd})
            print(oid, v["validation_id"], "law_text ->", res)

    # 2. statuses by the rule
    checks = collections.defaultdict(list)
    for v in vals:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                            v["human_review_status"], v["resolution"]))
    moved = []
    for o in data["observations"]:
        if o["verification_status"] in ("human-verified", "provisional", "superseded"):
            continue
        new = V.verification_status(o["confidence"], checks[o["observation_id"]])
        if new != o["verification_status"]:
            moved.append((o["observation_id"], o["canonical_account_id"], o["fiscal_year"], o["stage"],
                          o["confidence"], o["verification_status"], new))
            o["verification_status"] = new
    for m in moved:
        print("moved", *m)
    print(f"{len(moved)} status changes")
    if a.write:
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
