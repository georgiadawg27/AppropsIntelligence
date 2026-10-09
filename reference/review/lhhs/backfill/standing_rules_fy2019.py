"""
FY2019 under the owner's standing rules (2026-10-08). Appends to data/staged.json; no existing figure, ID or status
changes.

    python reference/review/lhhs/backfill/standing_rules_fy2019.py           # what would change
    python reference/review/lhhs/backfill/standing_rules_fy2019.py --write   # apply

1. A confirmed absence cites the bill or law text, never a table's silence. For each general-provision line the
   FY2019 tables leave missing, and for CCPF, the text was searched (House: H.R. 6470 as reported; Senate: S. 3158 as
   placed on the calendar; request: the FY2019 Budget Appendix's proposed language, bracketed text excluded; Enacted:
   P.L. 115-245 Division B). A provision found is recorded from the text (section and page); none found is a
   confirmed absence citing the search. The House bill rescinds Nonrecurring Expenses Fund balances in Title V (sec.
   530), not Title II: recorded, the section says where.
   Limitation for Title XVIII: its FY2026 provision (P.L. 119-75 div. B sec. 241, the critical-access-hospital
   distance rule) is in none of these texts -- per the owner's decision (2026-10-08) the cells stay missing.
2. LIHEAP's FY2019 request: H.Rept. 115-862 prints no request figure on the 'Formula Grants' line, and the same
   line's 'Bill vs. Request' column is +3,640,304, equal to the Bill column: the request is 0 by the table's own
   arithmetic (the FY2019 budget proposed no LIHEAP funding). S.Rept. 115-289 p.253 agrees: its budget-estimate cell
   is a dot leader and 'compared with budget estimate' is +3,690,304, equal to the committee recommendation.
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
STORE = ROOT / "document_store"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
TODAY = "2026-10-09"
FY = 2019
MEDOPS, NEF, ADOPT, CCPF, LIHEAP = ("ACC-HHS-GP-MEDICARE-OPERATIONS", "ACC-HHS-GP-NEF-RESCISSION",
                                    "ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", "ACC-HHS-HRSA-CCPF", "ACC-HHS-ACF-LIHEAP")

DOCS = {
    "SRC-BILLS-115HR6470RH": dict(
        file="BILLS-115hr6470rh.pdf", label="H.R. 6470 (reported)", publication_date="2018-07-23",
        stage="House Reported", congress_session="115-2", document_type="bill", source_page="48-114",
        source_agency="U.S. House of Representatives (bill; govinfo BILLS collection)", sha="e8057d59fb43fc5e",
        url="https://www.govinfo.gov/content/pkg/BILLS-115hr6470rh/pdf/BILLS-115hr6470rh.pdf",
        notes="H.R. 6470 as reported (BILLS-115hr6470rh): the FY2019 House Labor-HHS bill (H.Rept. 115-862); Title II "
              "is PDF pp. 48-114, Title V (General Provisions) pp. 157-176."),
    "SRC-BILLS-115S3158PCS": dict(
        file="BILLS-115s3158pcs.pdf", label="S. 3158 (placed on calendar)", publication_date="2018-06-28",
        stage="Senate Reported", congress_session="115-2", document_type="bill", source_page="48-112",
        source_agency="U.S. Senate (bill; govinfo BILLS collection)", sha="d2a208ae844d62e1",
        url="https://www.govinfo.gov/content/pkg/BILLS-115s3158pcs/pdf/BILLS-115s3158pcs.pdf",
        notes="S. 3158 as reported and placed on the calendar (BILLS-115s3158pcs): the FY2019 Senate Labor-HHS bill "
              "(S.Rept. 115-289); Title II is PDF pp. 48-112."),
    "SRC-BUDGET-APP-FY2019-HHS": dict(
        file="BUDGET-2019-APP.pdf", label="the FY2019 Budget Appendix", publication_date="2018-02-12",
        stage="President's Budget", congress_session="", document_type="budget_appendix", source_page="419-489",
        source_agency="Office of Management and Budget", sha="ecc31a5d851b4882",
        url="https://www.govinfo.gov/content/pkg/BUDGET-2019-APP/pdf/BUDGET-2019-APP.pdf",
        notes="Budget of the U.S. Government, FY2019, Appendix (whole volume; SRC-BUDGET-APP-FY2019 is the CJS part "
              "file); the HHS chapter is PDF pp. 419-489. "
              "Proposed appropriations language: bracketed text is enacted language proposed for deletion."),
    "SRC-PLAW-115PUBL245": dict(
        file="PLAW-115publ245.pdf", label="P.L. 115-245", publication_date="2018-09-28", stage="Enacted",
        congress_session="115-2", document_type="public_law", source_page="68-142",
        source_agency="U.S. Congress (public law; govinfo PLAW collection)", sha="011a80ff7a5f45cc",
        url="https://www.govinfo.gov/content/pkg/PLAW-115publ245/pdf/PLAW-115publ245.pdf",
        notes="Department of Defense and Labor, Health and Human Services, and Education Appropriations Act, 2019 and "
              "Continuing Appropriations Act, 2019; Division B (Labor-HHS) is PDF pp. 68-142, its Title II pp. 88-116."),
}
TEXT = {"House Reported": "SRC-BILLS-115HR6470RH", "Senate Reported": "SRC-BILLS-115S3158PCS",
        "President's Budget": "SRC-BUDGET-APP-FY2019-HHS", "Enacted": "SRC-PLAW-115PUBL245"}
WHERE = {"SRC-BILLS-115HR6470RH": "the whole bill (Title II pp. 48-114, Title V pp. 157-176)",
         "SRC-BILLS-115S3158PCS": "the whole bill (Title II pp. 48-112)",
         "SRC-BUDGET-APP-FY2019-HHS": "the HHS chapter's proposed language (pp. 419-489, bracketed text excluded)",
         "SRC-PLAW-115PUBL245": "Division B (pp. 68-142; Title II pp. 88-116)"}
RESCISSIONS = {
    "SRC-BILLS-115HR6470RH": "Title V sec. 530 (Nonrecurring Expenses Fund, $400,000,000, p.173), sec. 529 (CHIP "
                             "balances, p.173) and sec. 531 (Child Enrollment Contingency Fund, p.173)",
    "SRC-BILLS-115S3158PCS": "sec. 230 (Nonrecurring Expenses Fund, $350,000,000, p.111)",
    "SRC-BUDGET-APP-FY2019-HHS": "none in the HHS chapter's proposed language",
    "SRC-PLAW-115PUBL245": "sec. 228 (Nonrecurring Expenses Fund, $400,000,000, p.114)",
}
# (account, stage, amount, amount_type, page, section, quote checked on the page)
FOUND = [
    (MEDOPS, "Enacted", 305_000_000, "budget authority", 114,
     "Division B, Title II, General Provisions, sec. 227 (Medicare Operations; 132 Stat. 3094) -- 'may transfer up to "
     "$305,000,000' to 'Centers for Medicare & Medicaid Services, Program Management' from the Medicare trust funds "
     "[law text]", r"may transfer up to \$305,000,000 to such account"),
    (NEF, "House Reported", -400_000_000, "rescission", 173,
     "Title V, General Provisions, sec. 530 (RESCISSION) -- 'Of the funds unobligated balances in the \"Nonrecurring "
     "expenses fund\" ... $400,000,000 is rescinded' [bill text; the House bill places it in Title V, not Title II]",
     r"\$400,000,000 is rescinded"),
    (NEF, "Senate Reported", -350_000_000, "rescission", 111,
     "Title II, General Provisions, sec. 230 (RESCISSION) -- 'Of the unobligated balances available in the "
     "\"Nonrecurring Expenses Fund\" ..., $350,000,000 are hereby rescinded' [bill text]",
     r"\$350,000,000 are hereby rescinded"),
    (NEF, "Enacted", -400_000_000, "rescission", 114,
     "Division B, Title II, General Provisions, sec. 228 (RESCISSION; 132 Stat. 3094) -- 'Of the unobligated balances "
     "available in the \"Nonrecurring Expenses Fund\" ..., $400,000,000 are hereby rescinded' [law text]",
     r"\$400,000,000 are hereby rescinded"),
]
MEDOPS_NONE = {
    "House Reported": "H.R. 6470 has no section letting the Secretary transfer Medicare trust fund amounts to 'Centers "
                      "for Medicare & Medicaid Services, Program Management' (whole text searched for /may transfer "
                      "up to $... to such account from the Federal Hospital/).",
    "Senate Reported": "S. 3158 has no section letting the Secretary transfer Medicare trust fund amounts to 'Centers "
                       "for Medicare & Medicaid Services, Program Management' (whole text searched for /may transfer "
                       "up to $... to such account from the Federal Hospital/).",
    "President's Budget": "The FY2019 Budget Appendix's proposed HHS language has no section letting the Secretary "
                          "transfer Medicare trust fund amounts to CMS Program Management (pp. 419-489, bracketed text "
                          "excluded).",
}
CCPF_NONE = {
    "House Reported": "H.R. 6470 never mentions the Covered Countermeasures Process Fund (whole text searched).",
    "Senate Reported": "S. 3158 never mentions the Covered Countermeasures Process Fund (whole text searched).",
    "President's Budget": "The FY2019 Budget Appendix proposes no appropriations language for the Covered "
                          "Countermeasure Process Fund: its HHS entry (pp. 425-426) is a program and financing schedule "
                          "with a mandatory legislative proposal subject to PAYGO, not an appropriation (HHS chapter, "
                          "pp. 419-489, searched).",
    "Enacted": "P.L. 115-245 Division B has no 'Covered Countermeasures Process Fund' heading or appropriation (pp. "
               "68-142 searched).",
}


def pages(path):
    import subprocess
    raw = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True).stdout
    out = []
    for t in raw.split("\f"):
        t = re.sub(r"(?m)^\s*\d{1,2}\s*$", "", t)
        t = re.sub(r"(?m)^\s*\d{1,2}\s+(?=\S)", "", t)
        t = re.sub(r"([a-z])-\n\s*([a-z])", r"\1\2", t)
        t = re.sub(r"([a-z])\d{1,2} ([a-z])", r"\1\2", t)
        out.append(" ".join(t.split()))
    return out


def check_texts():
    n = 0
    cache = {d: pages(STORE / x["file"]) for d, x in DOCS.items() if (STORE / x["file"]).exists()}
    for aid, st, amount, t, page, _s, quote in FOUND:
        if TEXT[st] in cache:
            assert re.search(quote, cache[TEXT[st]][page - 1]), (aid, st, page)
            n += 1
    for st, doc in TEXT.items():
        if doc not in cache:
            continue
        body = cache[doc]
        if doc.startswith("SRC-BUDGET"):
            body = [re.sub(r"\[[^\[\]]*\]", "", p) for p in body[418:489]]
        txt = " ".join(body if not doc.startswith("SRC-PLAW") else body[67:142])
        if st in MEDOPS_NONE:
            assert not re.search(r"may transfer up to \$[\d,]+ to such account from the Federal Hospital", txt), st
        if st in CCPF_NONE:
            scope = body if not doc.startswith("SRC-PLAW") else body[67:142]
            pat = (r"(?i)covered countermeasures? process fund\s*✦?\s*for carrying out" if doc.startswith("SRC-BUDGET")
                   else r"(?i)covered countermeasures? process fund")
            assert not any(re.search(pat, p) for p in scope), st
        assert not re.search(r"(?i)adoption[^.]{0,200}(rescind|cancel)|(rescind|cancel)[^.]{0,300}adoption", txt), st
        assert not re.search(r"1820\(c\)\(2\)\(B\)|distance requirement", txt), st
        n += 4
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = data["observations"]
    assert not set(DOCS) & {d["document_id"] for d in data["source_docs"]}, "already applied (or an ID in use)"
    print("text checks:", check_texts())
    retired = json.loads(RETIRED.read_text())

    def ids(prefix, field, rows_, width):
        gone = [i for v in retired.values() if isinstance(v, dict) for i in v]
        n = max(int(i[len(prefix):]) for i in [r[field] for r in rows_] + gone
                if i.startswith(prefix) and i[len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    oids = ids("OBS-LHHS-", "observation_id", obs, 4)
    vids = ids("VAL-LHHS-", "validation_id", data["validations"], 5)
    cids = ids("CA-LHHS-", "confirmed_absence_id", data["confirmed_absences"], 4)
    have = {(o["canonical_account_id"], o["fiscal_year"], o["stage"], o["amount_type"], o["component"] or "")
            for o in obs if o.get("verification_status") != "superseded"}
    have |= {(x["canonical_account_id"], x["fiscal_year"], x["stage"], x["amount_type"], x["component"] or "")
             for x in data["confirmed_absences"]}
    new_obs, new_val, new_abs = [], [], []

    def observation(aid, st, amount, t, doc, page, section, method="text-extracted", confidence=0.95):
        assert (aid, FY, st, t, "") not in have, (aid, st)
        o = {"observation_id": next(oids), "canonical_account_id": aid, "fiscal_year": FY, "stage": st,
             "chamber": {"House Reported": "House", "Senate Reported": "Senate"}.get(st, "N/A"), "bill_id": None, "report_id": None,
             "amount": amount, "amount_type": t, "offsetting_collections": "FALSE", "transfer_link_account_id": "",
             "source_document_id": doc, "source_page": str(page), "source_table_or_section": section,
             "extraction_method": method, "confidence": confidence, "verification_status": "", "component": "",
             "headline_observation_id": ""}
        new_obs.append(o)
        return o

    for aid, st, amount, t, page, section, _q in FOUND:
        doc = TEXT[st]
        o = observation(aid, st, amount, t, doc, page, section)
        new_val.append({"observation_id": o["observation_id"], "rule_applied": "source_text",
                        "expected_result": f"{DOCS[doc]['label']} p.{page}: "
                                           + re.search(r"'[^']*\$[\d,]+[^']*'", section).group(0),
                        "observed_result": f"{amount:,} as recorded", "result": "pass"})
        print("recorded", aid, st, f"{amount:,}", o["observation_id"], f"p.{page}")

    def absent(aid, st, t, comp, evidence):
        if (aid, FY, st, t, comp) in have:
            return
        new_abs.append({"confirmed_absence_id": next(cids), "canonical_account_id": aid, "fiscal_year": FY,
                        "stage": st, "amount_type": t, "component": comp, "source_document_id": TEXT[st],
                        "evidence": evidence, "confirmed_date": TODAY})

    for st, doc in TEXT.items():
        name, where = DOCS[doc]["label"], WHERE[doc]
        if st in MEDOPS_NONE:
            absent(MEDOPS, st, "budget authority", "", MEDOPS_NONE[st])
        if not any(f[:2] == (NEF, st) for f in FOUND):
            absent(NEF, st, "rescission", "", f"{name}: no rescission of Nonrecurring Expenses Fund balances in "
                                              f"{where}. Its Title II rescissions: {RESCISSIONS[doc]}.")
        absent(NEF, st, "rescission", "emergency",
               f"{name}: no emergency-designated rescission of Nonrecurring Expenses Fund balances in {where}. Its "
               f"Title II rescissions: {RESCISSIONS[doc]}; none is designated an emergency requirement.")
        absent(ADOPT, st, "rescission", "",
               f"{name}: no rescission of Adoption Incentives (or other adoption) funds in {where} (searched for "
               f"'adoption' in the same sentence as a rescission or cancellation: none). Its Title II rescissions: "
               f"{RESCISSIONS[doc]}.")
        absent(CCPF, st, "budget authority", "", CCPF_NONE[st])

    # LIHEAP's request: 0 by the table's own arithmetic
    o = observation(LIHEAP, "President's Budget", 0, "budget authority", "SRC-CRPT-115HRPT862", 374,
                    "Title II, Administration for Children and Families -- the 'Formula Grants' line under 'Low "
                    "Income Home Energy Assistance (LIHEAP)' [FY 2019 Request]: no figure printed; 'Bill vs. Request' "
                    "+3,640,304 = Bill 3,640,304, so the request is 0 (H.Rept. 115-862 p.374)",
                    confidence=0.95)
    new_val.append({"observation_id": o["observation_id"], "rule_applied": "structural",
                    "expected_result": "request = Bill - (Bill vs. Request): 3,640,304 - 3,640,304 = 0 (thousands), "
                                       "H.Rept. 115-862 p.374 'Formula Grants' (LIHEAP)",
                    "observed_result": "0 as recorded", "result": "pass"})
    new_val.append({"observation_id": o["observation_id"], "rule_applied": "cross_document",
                    "expected_result": "S.Rept. 115-289 p.253 'Formula Grants' (LIHEAP): budget estimate printed as a "
                                       "dot leader; 'compared with budget estimate' +3,690,304 = committee "
                                       "recommendation 3,690,304, so the request is 0",
                    "observed_result": "0 as recorded", "result": "pass"})
    print("recorded LIHEAP FY2019 request 0", o["observation_id"])

    for v in new_val:
        v.update({"validation_id": next(vids), "human_review_status": "", "reviewer": "", "resolution": ""})
    recs = collections.defaultdict(list)
    for v in data["validations"] + new_val:
        recs[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                          v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], recs[o["observation_id"]])
    print(f"{len(new_obs)} observations ({collections.Counter(o['verification_status'] for o in new_obs)}), "
          f"{len(new_val)} validations, {len(new_abs)} confirmed absences "
          f"({collections.Counter(x['canonical_account_id'][8:] + ' ' + (x['component'] or '-') for x in new_abs)}), "
          f"{len(DOCS)} source documents")
    if a.write:
        tmpl = data["source_docs"][0]
        for doc, x in DOCS.items():
            data["source_docs"].append({**{k: "" for k in tmpl}, "document_id": doc, "source_agency": x["source_agency"],
                                        "url_or_identifier": x["url"], "document_type": x["document_type"],
                                        "congress_session": x["congress_session"], "fiscal_year": FY,
                                        "publication_date": x["publication_date"], "stage": x["stage"],
                                        "retrieval_timestamp": TODAY, "source_page": x["source_page"],
                                        "notes": f"{x['notes']} sha256 {x['sha']}... (the file at the link). Page "
                                                 "citations are PDF page numbers."})
        order = list(obs[0])
        data["observations"] += [{k: o.get(k, "") for k in order} for o in new_obs]
        vorder = list(data["validations"][0])
        data["validations"] += [{k: v[k] for k in vorder} for v in new_val]
        data["confirmed_absences"] += new_abs
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
