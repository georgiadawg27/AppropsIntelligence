"""
Grants to States for Medicaid (owner, 2026-10-08): FY2023 (every stage) and FY2024 (President's Budget, Senate
Reported) held only their advance lines, the headline a confirmed absence (CA-LHHS-0005, 0028, 0029, 0032; 0006,
0007) -- but the account is funded in each document, which prints 'Total, Grants to States for Medicaid,
appropriated in this bill' and the program level. Never a confirmed absence on a funded account: record the two
printed views, derive the headline (derived_headlines), and retire those confirmed absences (IDs not reused).

    python reference/review/lhhs/backfill/fix_medicaid_headlines.py           # what would change
    python reference/review/lhhs/backfill/fix_medicaid_headlines.py --write   # apply
"""

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
import validate_approps as V  # noqa: E402
import derived_headlines as DH  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
AID = "ACC-HHS-CMS-MEDICAID"
# (fiscal year, stage): (confirmed absence retired, document, page, column, printed labels of the two views,
# 'appropriated in this bill', program level), in thousands (the text layers of S.Rept. 118-84 p.382 and the FY2023
# Senate draft p.409; H.Rept. 117-403 p.821 read by vision)
CASES = {
    (2023, "President's Budget"): ("CA-LHHS-0028", "SRC-CRPT-117HRPT403", "821", "FY 2023 Request",
                                   "Total, Grants to States for Medicaid, appropriated in this bill",
                                   "Total, Medicaid Program Level, available this fiscal year", 564_937_564, 533_079_108),
    (2023, "House Reported"): ("CA-LHHS-0029", "SRC-CRPT-117HRPT403", "821", "Bill",
                               "Total, Grants to States for Medicaid, appropriated in this bill",
                               "Total, Medicaid Program Level, available this fiscal year", 564_937_564, 533_079_108),
    (2023, "Senate Reported"): ("CA-LHHS-0032", "SRC-EXPL-LHHS-FY2023-SENATE", "409", "Committee recommendation",
                                "Total, Grants to States for Medicaid, appropriated in this bill",
                                "Total, Medicaid Program Level, available this fiscal year", 564_937_564, 533_079_108),
    (2023, "Enacted"): ("CA-LHHS-0005", "SRC-CRPT-118SRPT84", "382", "2023 appropriation",
                        "Total, Grants to States for Medicaid, appropriated in this bill",
                        "Total, Medicaid program level, available this fiscal year", 564_937_564, 533_079_108),
    (2024, "President's Budget"): ("CA-LHHS-0006", "SRC-CRPT-118SRPT84", "382", "Budget estimate",
                                   "Total, Grants to States for Medicaid, appropriated in this bill",
                                   "Total, Medicaid program level, available this fiscal year", 652_537_264, 604_537_324),
    (2024, "Senate Reported"): ("CA-LHHS-0007", "SRC-CRPT-118SRPT84", "382", "Committee recommendation",
                                "Total, Grants to States for Medicaid, appropriated in this bill",
                                "Total, Medicaid program level, available this fiscal year", 652_537_264, 604_537_324),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    n, bad = DH.check_rule(data["observations"], AID)
    assert n and not bad, bad
    obs = [o for o in data["observations"] if o["canonical_account_id"] == AID and o["fiscal_year"] in (2023, 2024)]
    assert not any(o["amount_type"] == "budget authority" and (o["fiscal_year"], o["stage"]) in CASES for o in obs), \
        "these Medicaid cells already have their lines"

    def nxt(rows, field, prefix, width):
        n = max(int(r[field][len(prefix):]) for r in rows if r[field].startswith(prefix) and r[field][len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    oids = nxt(data["observations"], "observation_id", "OBS-LHHS-", 4)
    vids = nxt(data["validations"], "validation_id", "VAL-LHHS-", 5)
    new_obs, new_val, retired = [], [], []
    for (fy, stage), (ca, src, page, col, lab_a, lab_p, APPROPRIATED, PROGRAM) in CASES.items():
        adv = next(o for o in obs if (o["fiscal_year"], o["stage"]) == (fy, stage) and o["amount_type"] == "advance")
        prior = next(o for o in obs if (o["fiscal_year"], o["stage"]) == (fy, stage) and o["amount_type"] == "prior_year_advance")
        assert (adv["source_document_id"], adv["source_page"]) == (src, page), stage
        adv_label = adv["source_table_or_section"].split("printed as '")[1].split("'")[0]
        lines = {("budget authority", "appropriated_in_this_bill"): APPROPRIATED * 1000, ("advance", ""): adv["amount"]}
        labels = {("budget authority", "appropriated_in_this_bill"): lab_a, ("advance", ""): adv_label}
        amount, note, (exp, obsd) = DH.derive(AID, lines, labels.get, lambda k: page)
        base = {k: adv[k] for k in ("canonical_account_id", "fiscal_year", "stage", "chamber", "bill_id", "report_id",
                                    "offsetting_collections", "transfer_link_account_id", "source_document_id",
                                    "source_page", "confidence")}
        head = dict(base, observation_id=next(oids), amount=amount, amount_type="budget authority", component="",
                    headline_observation_id="", extraction_method="derived",
                    source_table_or_section=f"Title II, Centers for Medicare & Medicaid Services -- {note}")
        views = [dict(base, observation_id=next(oids), amount=amt * 1000, amount_type="budget authority", component=comp,
                      headline_observation_id=head["observation_id"], extraction_method=adv["extraction_method"],
                      source_table_or_section=f"Title II, Centers for Medicare & Medicaid Services -- printed as '{lab}' [{col}]")
                 for comp, amt, lab in (("appropriated_in_this_bill", APPROPRIATED, lab_a),
                                        ("program_level_available_this_fiscal_year", PROGRAM, lab_p))]
        new_obs += [head, *views]
        new_val.append({"observation_id": head["observation_id"], "rule_applied": "structural", "expected_result": exp,
                        "observed_result": obsd, "result": "pass"})
        # the printed lines agree with one another: program level + new advance - prior-year advance = appropriated
        got = PROGRAM * 1000 + adv["amount"] + prior["amount"]
        new_val += [{"observation_id": view["observation_id"], "rule_applied": "structural",
                        "expected_result": f"'{lab_p}' {PROGRAM:,} + new advance {adv['amount'] // 1000:,} ({adv['observation_id']}) "
                                           f"- prior-year advance {-prior['amount'] // 1000:,} ({prior['observation_id']}) "
                                           f"= {got // 1000:,} (thousands)",
                        "observed_result": f"{APPROPRIATED:,} as printed (p.{page})",
                        "result": "pass" if got == APPROPRIATED * 1000 else "fail"} for view in views]
        retired.append(ca)
    for v in new_val:
        v.update({"validation_id": next(vids), "human_review_status": "", "reviewer": "", "resolution": ""})
    checks = collections.defaultdict(list)
    for v in new_val:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"], "", ""))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    order = list(data["observations"][0])
    new_obs = [{k: o[k] for k in order} for o in new_obs]
    vorder = list(data["validations"][0])
    new_val = [{k: v[k] for k in vorder} for v in new_val]
    for o in new_obs:
        print(o["observation_id"], o["fiscal_year"], o["stage"], o["component"] or "headline", f"{o['amount']:,}", o["extraction_method"],
              o["verification_status"])
    print("retired confirmed absences:", ", ".join(retired))
    if a.write:
        data["observations"].extend(new_obs)
        data["validations"].extend(new_val)
        data["confirmed_absences"] = [c for c in data["confirmed_absences"] if c["confirmed_absence_id"] not in retired]
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
