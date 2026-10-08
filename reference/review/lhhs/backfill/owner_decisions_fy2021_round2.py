"""
The owner's second round of FY2021 decisions (PR #35, 2026-10-08). Appends to data/staged.json; retired IDs are
listed in reference/review/lhhs/retired_ids.json and never reused.

    python reference/review/lhhs/backfill/owner_decisions_fy2021_round2.py           # what would change
    python reference/review/lhhs/backfill/owner_decisions_fy2021_round2.py --write   # apply

1. The 8 bill, appendix and law documents added for the text checks get their page ranges (Title II of each bill;
   the Budget Appendix's HHS chapter; Division H of each law), so every citation falls inside its document.
2. ONC, FY2022 (decision b): the Senate cell is a 0 headline (S. 3062 provides no new budget authority) plus a
   program-level line of 86,614,000 from the PHS evaluation set-aside (S. 3062 p.110); the House, request and Enacted
   cells' 0 headlines get the same program-level line from their tables' 'Evaluation Tap Funding' row (H.Rept. 117-96
   p.493; H.Rept. 117-403 p.834). The corner note ('Funded through the PHS evaluation set-aside ($x), not new budget
   authority.') is the store's rule (approps_store.history).
3. Limitation for Title XVIII (decision c): its confirmed absences are retired (they rested on tables' silence) and
   the cells read missing. The FY2026 line is P.L. 119-75 Division B sec. 241 (PDF p.120, 140 Stat. 293): CMS shall
   not apply the distance requirements of SSA sec. 1820(c)(2)(B)(i)(I) to certain critical access hospitals -- a
   title XVIII provision the FY2026 explanatory statement lists as 'a new provision related to title XVIII of the
   Social Security Act' (CREC p.245) and its table scores at 2,000 (p.296). The account notes say so.
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

STAGED = ROOT / "data" / "staged.json"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
TODAY = "2026-10-08"
ONC, LIMIT = "ACC-HHS-OS-ONC", "ACC-HHS-GP-MEDICARE-LIMITATION"
PAGES = {"SRC-BILLS-116HR7614RH": "46-125", "SRC-BILL-LHHS-FY2021-SENATE": "49-123", "SRC-BILLS-117HR4502RH": "46-133",
         "SRC-BILLS-117S3062IS": "54-137", "SRC-BUDGET-APP-FY2021": "435-508", "SRC-BUDGET-APP-FY2022": "437-510",
         "SRC-PLAW-116PUBL260": "367-447", "SRC-PLAW-117PUBL103": "373-453"}
# ONC FY2022: stage -> (headline document, page, section text, program-level amount, the program-level row, checks)
ONC_FY2022 = {
    "Senate Reported": ("SRC-BILLS-117S3062IS", "110",
                        "Title II, Office of the Secretary, 'OFFICE OF THE NATIONAL COORDINATOR FOR HEALTH INFORMATION "
                        "TECHNOLOGY' -- '$86,614,000 shall be from amounts made available under section 241 of the PHS "
                        "Act': no new budget authority [bill text]", 86_614_000,
                        "Title II, Office of the Secretary, 'OFFICE OF THE NATIONAL COORDINATOR FOR HEALTH INFORMATION "
                        "TECHNOLOGY' -- '$86,614,000 shall be from amounts made available under section 241 of the PHS "
                        "Act' (the PHS evaluation set-aside) [bill text]",
                        [("cross_document", "SRC-EXPL-LHHS-FY2022-SENATE p.359 prints 'Evaluation Tap Funding' (86,614) "
                                            "under ONC, ONC itself '---' [Committee recommendation]")]),
    "House Reported": (None, "493", None, 86_614_000,
                       "Title II, Office of the Secretary -- printed as 'Evaluation Tap Funding' under 'Office of the "
                       "National Coordinator for Health Information Technology' [Bill]",
                       [("cross_document", "SRC-BILLS-117HR4502RH p.103: '$86,614,000 shall be available from amounts "
                                           "available under section 241 of the PHS Act'")]),
    "President's Budget": (None, "493", None, 86_614_000,
                           "Title II, Office of the Secretary -- printed as 'Evaluation Tap Funding' under 'Office of the "
                           "National Coordinator for Health Information Technology' [FY 2022 Request]",
                           [("cross_document", "SRC-EXPL-LHHS-FY2022-SENATE p.359 prints 'Evaluation Tap Funding' "
                                               "(86,614) [Budget estimate]")]),
    "Enacted": (None, "834", None, 64_238_000,
                "Title II, Office of the Secretary -- printed as 'Evaluation Tap Funding' under 'Information "
                "Technology' (the Office of the National Coordinator) [FY 2022 Enacted]",
                [("law_text", "P.L. 117-103 div. H, 'OFFICE OF THE NATIONAL COORDINATOR FOR HEALTH INFORMATION "
                              "TECHNOLOGY': '$64,238,000 shall be from amounts made available under section 241 of the "
                              "PHS Act' (p.416)")]),
}
LIMIT_NOTE = (" The FY2026 line is P.L. 119-75 Division B sec. 241 (PDF p.120, 140 Stat. 293): CMS shall not apply the "
              "distance requirements of SSA sec. 1820(c)(2)(B)(i)(I) to certain critical access hospitals -- a title "
              "XVIII provision the FY2026 explanatory statement lists as 'a new provision related to title XVIII of the "
              "Social Security Act' (p.245) and its table scores at 2,000 (p.296). Not in the FY2021 or FY2022 bills, "
              "Budget Appendix language or laws (searched for '1820(c)(2)(B)' and 'distance requirement'). Its "
              "confirmed absences, which rested on tables' silence, were retired 2026-10-08 (owner).")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = data["observations"]
    retired = json.loads(RETIRED.read_text())
    assert not any(o["canonical_account_id"] == ONC and o["component"] == "program_level" for o in obs), "already applied"

    def ids(prefix, field, rows_, width, gone=()):
        n = max(int(i[len(prefix):]) for i in [r[field] for r in rows_] + list(gone)
                if i.startswith(prefix) and i[len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    oids = ids("OBS-LHHS-", "observation_id", obs, 4)
    vids = ids("VAL-LHHS-", "validation_id", data["validations"], 5)

    # 1. page ranges
    for d in data["source_docs"]:
        if d["document_id"] in PAGES:
            assert d["source_page"] == "", d
            d["source_page"] = PAGES[d["document_id"]]
    assert sum(d["document_id"] in PAGES for d in data["source_docs"]) == len(PAGES)

    # 2. ONC FY2022
    new_obs, new_val = [], []
    head = {o["stage"]: o for o in obs if o["canonical_account_id"] == ONC and o["fiscal_year"] == 2022
            and not o["component"] and o.get("verification_status") != "superseded"}
    tmpl = head["House Reported"]
    for st, (doc, page, section, pl, pl_section, checks) in ONC_FY2022.items():
        if doc:                                          # the Senate cell: a headline from the bill text
            assert st not in head
            h = {**tmpl, "observation_id": next(oids), "stage": st, "chamber": "Senate", "amount": 0,
                 "source_document_id": doc, "source_page": page, "source_table_or_section": section,
                 "extraction_method": "text-extracted", "confidence": 0.95, "verification_status": ""}
            new_obs.append(h)
            new_val.append({"observation_id": h["observation_id"], "rule_applied": "source_text",
                            "expected_result": f"S. 3062 (introduced) p.{page}: ONC's paragraph appropriates no new "
                                               "budget authority ('$86,614,000 shall be from amounts made available "
                                               "under section 241 of the PHS Act')",
                            "observed_result": "0 as recorded", "result": "pass"})
        else:
            h = head[st]
            assert h["amount"] == 0, h
        p = {**h, "observation_id": next(oids), "amount": pl, "component": "program_level",
             "headline_observation_id": h["observation_id"], "source_page": page,
             "source_table_or_section": pl_section, "verification_status": ""}
        if not doc:
            p["extraction_method"] = "AI-extracted"
        new_obs.append(p)
        for rule, expected in checks:
            new_val.append({"observation_id": p["observation_id"], "rule_applied": rule, "expected_result": expected,
                            "observed_result": f"{pl:,} as recorded", "result": "pass"})
        print("ONC", st, "headline", h["observation_id"], "0; program level", f"{pl:,}", p["observation_id"])

    # 3. Limitation for Title XVIII
    gone = [x for x in data["confirmed_absences"] if x["canonical_account_id"] == LIMIT]
    for x in gone:
        retired["confirmed_absences"][x["confirmed_absence_id"]] = (
            f"Limitation for Title XVIII, FY{x['fiscal_year']} {x['stage']}: rested on a table's silence (owner "
            f"2026-10-08)")
    acct = next(x for x in data["accounts"] if x["canonical_account_id"] == LIMIT)
    print("retired", len(gone), "confirmed absences:", ", ".join(x["confirmed_absence_id"] for x in gone))

    for v in new_val:
        v.update({"validation_id": next(vids), "human_review_status": "", "reviewer": "", "resolution": ""})
    recs = collections.defaultdict(list)
    for v in data["validations"] + new_val:
        recs[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                          v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], recs[o["observation_id"]])
    print(f"{len(new_obs)} observations ({collections.Counter(o['verification_status'] for o in new_obs)}), "
          f"{len(new_val)} validations, {len(PAGES)} page ranges")
    if a.write:
        acct["notes"] = acct["notes"] + LIMIT_NOTE
        data["confirmed_absences"] = [x for x in data["confirmed_absences"] if x["canonical_account_id"] != LIMIT]
        order = list(obs[0])
        data["observations"] += [{k: o.get(k, "") for k in order} for o in new_obs]
        vorder = list(data["validations"][0])
        data["validations"] += [{k: v[k] for k in vorder} for v in new_val]
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        RETIRED.write_text(json.dumps(retired, indent=1) + "\n")
        print("wrote", STAGED.relative_to(ROOT), "and", RETIRED.relative_to(ROOT))


if __name__ == "__main__":
    main()
