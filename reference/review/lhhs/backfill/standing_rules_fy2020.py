"""
FY2020 under the owner's standing rules (2026-10-08). Appends to data/staged.json; no existing figure, ID or status
changes.

    python reference/review/lhhs/backfill/standing_rules_fy2020.py           # what would change
    python reference/review/lhhs/backfill/standing_rules_fy2020.py --write   # apply

1. A confirmed absence cites the bill or law text, never a table's silence. For each general-provision line the
   FY2020 tables leave missing, and for CCPF, the text was searched (House: H.R. 2740 as reported; request: the FY2020
   Budget Appendix's proposed language, bracketed text excluded; Enacted: P.L. 116-94 Division A). A provision found is
   recorded from the text (section and page); none found is a confirmed absence citing the search. The Senate stage
   is not reported (no bill, no draft): nothing to search, the grid reads 'not reported'.
   Limitation for Title XVIII: its FY2026 provision (P.L. 119-75 div. B sec. 241, the critical-access-hospital
   distance rule) is in none of these texts -- per the owner's decision (2026-10-08) the cells stay missing.
2. LIHEAP's FY2020 request: H.R. 116-62's text layer prints no request figure on the 'Formula Grants' line, but the
   same line's 'Bill vs. Request' column is +3,840,304, equal to the Bill column: the request is 0 by the table's own
   arithmetic (the FY2020 budget proposed no LIHEAP funding). The vision transcription of p.346 already on file reads
   the cell as '---' (cross-check).
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
MEDOPS, NEF, ADOPT, CCPF, LIHEAP = ("ACC-HHS-GP-MEDICARE-OPERATIONS", "ACC-HHS-GP-NEF-RESCISSION",
                                    "ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", "ACC-HHS-HRSA-CCPF", "ACC-HHS-ACF-LIHEAP")

DOCS = {
    "SRC-BILLS-116HR2740RH": dict(
        file="BILLS-116hr2740rh.pdf", label="H.R. 2740 (reported)", publication_date="2019-05-16",
        stage="House Reported", congress_session="116-1", document_type="bill", source_page="42-116",
        source_agency="U.S. House of Representatives (bill; govinfo BILLS collection)", sha="574ef8fe18076696",
        url="https://www.govinfo.gov/content/pkg/BILLS-116hr2740rh/pdf/BILLS-116hr2740rh.pdf",
        notes="H.R. 2740 as reported (BILLS-116hr2740rh): the FY2020 House Labor-HHS bill (H.Rept. 116-62); Title II "
              "is PDF pp. 42-116."),
    "SRC-BUDGET-APP-FY2020": dict(
        file="BUDGET-2020-APP.pdf", label="the FY2020 Budget Appendix", publication_date="2019-03-11",
        stage="President's Budget", congress_session="", document_type="budget_appendix", source_page="421-491",
        source_agency="Office of Management and Budget", sha="9e32719df3802c25",
        url="https://www.govinfo.gov/content/pkg/BUDGET-2020-APP/pdf/BUDGET-2020-APP.pdf",
        notes="Budget of the U.S. Government, FY2020, Appendix (whole volume); the HHS chapter is PDF pp. 421-491. "
              "Proposed appropriations language: bracketed text is enacted language proposed for deletion."),
    "SRC-PLAW-116PUBL94": dict(
        file="PLAW-116publ94.pdf", label="P.L. 116-94", publication_date="2019-12-20", stage="Enacted",
        congress_session="116-1", document_type="public_law", source_page="5-79",
        source_agency="U.S. Congress (public law; govinfo PLAW collection)", sha="d724cc183016bdd5",
        url="https://www.govinfo.gov/content/pkg/PLAW-116publ94/pdf/PLAW-116publ94.pdf",
        notes="Further Consolidated Appropriations Act, 2020; Division A (Labor-HHS) is PDF pp. 5-79, its Title II "
              "pp. 24-54."),
}
TEXT = {"House Reported": "SRC-BILLS-116HR2740RH", "President's Budget": "SRC-BUDGET-APP-FY2020",
        "Enacted": "SRC-PLAW-116PUBL94"}
WHERE = {"SRC-BILLS-116HR2740RH": "Title II (pp. 42-116)",
         "SRC-BUDGET-APP-FY2020": "the HHS chapter's proposed language (pp. 421-491, bracketed text excluded)",
         "SRC-PLAW-116PUBL94": "Division A, Title II (pp. 24-54)"}
RESCISSIONS = {
    "SRC-BILLS-116HR2740RH": "sec. 230 (P.L. 114-10 sec. 301(b)(3) balances, $4,300,000,000, p.110)",
    "SRC-BUDGET-APP-FY2020": "sec. 219 (Nonrecurring Expenses Fund, $400,000,000 cancelled, p.490)",
    "SRC-PLAW-116PUBL94": "sec. 240 (Nonrecurring Expenses Fund, $350,000,000, p.54)",
}
# (account, stage, amount, amount_type, page, section, quote checked on the page)
FOUND = [
    (MEDOPS, "Enacted", 305_000_000, "budget authority", 51,
     "Division A, Title II, General Provisions, sec. 227 (Medicare Operations; 133 Stat. 2583) -- 'may transfer up to "
     "$305,000,000' to 'Centers for Medicare & Medicaid Services, Program Management' from the Medicare trust funds "
     "[law text]", r"may transfer up to \$305,000,000 to such account"),
    (NEF, "President's Budget", -400_000_000, "rescission", 490,
     "HHS General Provisions (proposed), sec. 219 (CANCELLATION) -- 'Of the unobligated balances available in the "
     "\"Nonrecurring Expenses Fund\" ..., $400,000,000 are hereby permanently cancelled' [Budget Appendix language]",
     r"\$400,000,000 are hereby (\[[^\]]*\] )?permanently cancelled"),
    (NEF, "Enacted", -350_000_000, "rescission", 54,
     "Division A, Title II, General Provisions, sec. 240 (RESCISSION; 133 Stat. 2586) -- 'Of the unobligated balances "
     "in the \"Nonrecurring Expenses Fund\" ..., $350,000,000 are hereby rescinded' [law text]",
     r"\$350,000,000 are hereby rescinded"),
]
MEDOPS_NONE = {
    "House Reported": "H.R. 2740 has no section letting the Secretary transfer Medicare trust fund amounts to 'Centers "
                      "for Medicare & Medicaid Services, Program Management' (whole text searched for /may transfer "
                      "up to $... to such account from the Federal Hospital/).",
    "President's Budget": "The FY2020 Budget Appendix's proposed HHS language has no section letting the Secretary "
                          "transfer Medicare trust fund amounts to CMS Program Management (pp. 421-491, bracketed text "
                          "excluded).",
}
CCPF_NONE = {
    "House Reported": "H.R. 2740 never mentions the Covered Countermeasures Process Fund (whole text searched).",
    "President's Budget": "The FY2020 Budget Appendix proposes no appropriations language for the Covered "
                          "Countermeasure Process Fund: its HHS entry (p.428) is a program and financing schedule "
                          "only (HHS chapter, pp. 421-491, searched).",
    "Enacted": "P.L. 116-94 Division A has no 'Covered Countermeasures Process Fund' heading or appropriation (pp. "
               "5-79 searched).",
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
            body = [re.sub(r"\[[^\[\]]*\]", "", p) for p in body[420:491]]
        txt = " ".join(body)
        if st in MEDOPS_NONE:
            assert not re.search(r"may transfer up to \$[\d,]+ to such account from the Federal Hospital", txt), st
        if st in CCPF_NONE:
            scope = body if not doc.startswith("SRC-PLAW") else body[4:79]
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
    assert "SRC-PLAW-116PUBL94" not in {d["document_id"] for d in data["source_docs"]}, "already applied"
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
        assert (aid, 2020, st, t, "") not in have, (aid, st)
        o = {"observation_id": next(oids), "canonical_account_id": aid, "fiscal_year": 2020, "stage": st,
             "chamber": {"House Reported": "House"}.get(st, "N/A"), "bill_id": None, "report_id": None,
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
        if (aid, 2020, st, t, comp) in have:
            return
        new_abs.append({"confirmed_absence_id": next(cids), "canonical_account_id": aid, "fiscal_year": 2020,
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
    o = observation(LIHEAP, "President's Budget", 0, "budget authority", "SRC-CRPT-116HRPT62", 346,
                    "Title II, Administration for Children and Families -- the 'Formula Grants' line under 'Low "
                    "Income Home Energy Assistance Program' [FY 2020 Request]: the text layer prints no figure; "
                    "'Bill vs. Request' +3,840,304 = Bill 3,840,304, so the request is 0 (H.Rept. 116-62 p.346)",
                    confidence=0.95)
    new_val.append({"observation_id": o["observation_id"], "rule_applied": "structural",
                    "expected_result": "request = Bill - (Bill vs. Request): 3,840,304 - 3,840,304 = 0 (thousands), "
                                       "H.Rept. 116-62 p.346 'Formula Grants' (LIHEAP)",
                    "observed_result": "0 as recorded", "result": "pass"})
    new_val.append({"observation_id": o["observation_id"], "rule_applied": "source_text",
                    "expected_result": "the vision transcription of H.Rept. 116-62 p.346 on file (extraction cache) "
                                       "reads the FY 2020 Request cell of 'Formula Grants' as '---'",
                    "observed_result": "0 as recorded", "result": "pass"})
    print("recorded LIHEAP FY2020 request 0", o["observation_id"])

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
                                        "congress_session": x["congress_session"], "fiscal_year": 2020,
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
