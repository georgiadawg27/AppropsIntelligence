"""
FY2017 under the owner's standing rules (2026-10-08, 2026-10-09). Appends to data/staged.json; no existing figure, ID
or status changes.

    python reference/review/lhhs/backfill/standing_rules_fy2017.py           # what would change
    python reference/review/lhhs/backfill/standing_rules_fy2017.py --write   # apply

1. A confirmed absence cites the bill or law text, never a table's silence. For each general-provision line the
   FY2017 tables leave missing, and for CCPF, the text was searched (House: H.R. 5926 as reported; Senate: S. 3040 as
   placed on the calendar; request: the FY2017 Budget Appendix's proposed language, bracketed text excluded; Enacted:
   P.L. 115-31 Division H). A provision found is recorded from the text (section and page); none found is a
   confirmed absence citing the search.
2. A provision with no stated amount gets 'no printed total' plus a note -- never a confirmed absence, never an
   estimate (owner, 2026-10-09). H.R. 5926 sec. 226 terminates the Nonrecurring Expenses Fund and rescinds its
   entire unobligated balance with no dollar amount; H.Rept. 114-699's table does print a figure on the
   'Nonrecurring expenses fund (rescission)' line, so that figure is recorded as usual (build_year) and cites both
   the table and sec. 226. No other FY2017 text holds an NEF provision: the Senate bill, the request's language and
   P.L. 115-31 are confirmed absences.
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
FY = 2017
MEDOPS, NEF, ADOPT, CCPF = ("ACC-HHS-GP-MEDICARE-OPERATIONS", "ACC-HHS-GP-NEF-RESCISSION",
                            "ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", "ACC-HHS-HRSA-CCPF")

DOCS = {
    "SRC-BILLS-114HR5926RH": dict(
        file="BILLS-114hr5926rh.pdf", label="H.R. 5926 (reported)", publication_date="2016-07-22",
        stage="House Reported", congress_session="114-2", document_type="bill", source_page="40-102; 142-170",
        source_agency="U.S. House of Representatives (bill; govinfo BILLS collection)", sha="186f44858ca42f09",
        url="https://www.govinfo.gov/content/pkg/BILLS-114hr5926rh/pdf/BILLS-114hr5926rh.pdf",
        notes="H.R. 5926 as reported (BILLS-114hr5926rh): the FY2017 House Labor-HHS bill (H.Rept. 114-699); Title II "
              "is PDF pp. 40-102, Title V (General Provisions) pp. 142-170; p.99 holds sec. 226 (the Nonrecurring "
              "Expenses Fund's termination and the rescission of its unobligated balance, no amount stated)."),
    "SRC-BILLS-114S3040PCS": dict(
        file="BILLS-114s3040pcs.pdf", label="S. 3040 (placed on calendar)", publication_date="2016-06-09",
        stage="Senate Reported", congress_session="114-2", document_type="bill", source_page="43-103",
        source_agency="U.S. Senate (bill; govinfo BILLS collection)", sha="60e4abc840988a22",
        url="https://www.govinfo.gov/content/pkg/BILLS-114s3040pcs/pdf/BILLS-114s3040pcs.pdf",
        notes="S. 3040 as reported and placed on the calendar (BILLS-114s3040pcs): the FY2017 Senate Labor-HHS bill "
              "(S.Rept. 114-274); Title II is PDF pp. 43-103."),
    "SRC-BUDGET-APP-FY2017-HHS": dict(
        file="BUDGET-2017-APP.pdf", label="the FY2017 Budget Appendix", publication_date="2016-02-09",
        stage="President's Budget", congress_session="", document_type="budget_appendix", source_page="447-519",
        source_agency="Office of Management and Budget", sha="36c3cf59cc1f22a3",
        url="https://www.govinfo.gov/content/pkg/BUDGET-2017-APP/pdf/BUDGET-2017-APP.pdf",
        notes="Budget of the U.S. Government, FY2017, Appendix (whole volume); the HHS chapter is PDF pp. 447-519. "
              "Proposed appropriations language: bracketed text is enacted language proposed for deletion."),
    "SRC-PLAW-115PUBL31": dict(
        file="PLAW-115publ31.pdf", label="P.L. 115-31", publication_date="2017-05-05", stage="Enacted",
        congress_session="115-1", document_type="public_law", source_page="368-433",
        source_agency="U.S. Congress (public law; govinfo PLAW collection)", sha="2800a8d34079a7d7",
        url="https://www.govinfo.gov/content/pkg/PLAW-115publ31/pdf/PLAW-115publ31.pdf",
        notes="Consolidated Appropriations Act, 2017; Division H (Labor-HHS) is PDF pp. 368-433, its Title II pp. "
              "385-409."),
}
TEXT = {"House Reported": "SRC-BILLS-114HR5926RH", "Senate Reported": "SRC-BILLS-114S3040PCS",
        "President's Budget": "SRC-BUDGET-APP-FY2017-HHS", "Enacted": "SRC-PLAW-115PUBL31"}
SCOPE = {"SRC-BUDGET-APP-FY2017-HHS": (446, 519), "SRC-PLAW-115PUBL31": (367, 433)}
WHERE = {"SRC-BILLS-114HR5926RH": "the whole bill (Title II pp. 40-102, Title V pp. 142-170)",
         "SRC-BILLS-114S3040PCS": "the whole bill (Title II pp. 43-103)",
         "SRC-BUDGET-APP-FY2017-HHS": "the HHS chapter's proposed language (pp. 447-519, bracketed text excluded)",
         "SRC-PLAW-115PUBL31": "Division H (pp. 368-433; Title II pp. 385-409)"}
RESCISSIONS = {
    "SRC-BILLS-114HR5926RH": "sec. 226 (Nonrecurring Expenses Fund: terminated, its unobligated balance rescinded, no "
                             "amount stated, p.99)",
    "SRC-BILLS-114S3040PCS": "none in Title II",
    "SRC-BUDGET-APP-FY2017-HHS": "none in the HHS chapter's proposed language",
    "SRC-PLAW-115PUBL31": "none in Division H, Title II",
}
# (account, stage, amount, amount_type, page, section, quote checked on the page)
FOUND = [
    (MEDOPS, "Senate Reported", 305_000_000, "budget authority", 99,
     "Title II, General Provisions, sec. 226 (Medicare Operations) -- 'may transfer up to $305,000,000' to 'Centers "
     "for Medicare and Medicaid Services, Program Management' from the Medicare trust funds [bill text]",
     r"may transfer up to \$305,000,000 to such account"),
    (MEDOPS, "Enacted", 305_000_000, "budget authority", 409,
     "Division H, Title II, General Provisions, sec. 224 (Medicare Operations) -- 'may transfer up to $305,000,000' "
     "to 'Centers for Medicare and Medicaid Services, Program Management' from the Medicare trust funds [law text]",
     r"may transfer up to \$305,000,000 to such account"),
]
# The House bill's NEF provision states no amount (sec. 226, p.99). Standing rule (owner, 2026-10-09): a provision
# with no stated amount gets 'no printed total' plus a note -- never a confirmed absence, never an estimate. Here
# H.Rept. 114-699's table does print a figure on the line, so the figure is recorded as usual (build_year) and cites
# both the table and sec. 226.
NEF_SEC226 = (99, r"Nonrecurring expenses fund.{0,200}is terminated.{0,300}unobligated balance of amounts available "
                  r"in such Fund is rescinded",
              "H.R. 5926 sec. 226 (p.99) terminates the Nonrecurring Expenses Fund and rescinds its entire unobligated "
              "balance; no dollar amount is printed in the bill")
MEDOPS_NONE = {
    "House Reported": "H.R. 5926 has no section letting the Secretary transfer Medicare trust fund amounts to 'Centers "
                      "for Medicare and Medicaid Services, Program Management' (whole text searched for /may transfer "
                      "up to $... to such account/).",
    "President's Budget": "The FY2017 Budget Appendix's proposed HHS language has no section letting the Secretary "
                          "transfer Medicare trust fund amounts to CMS Program Management (pp. 447-519, bracketed text "
                          "excluded).",
}
CCPF_NONE = {
    "House Reported": "H.R. 5926 never mentions the Covered Countermeasures Process Fund (whole text searched).",
    "Senate Reported": "S. 3040 never mentions the Covered Countermeasures Process Fund (whole text searched).",
    "President's Budget": "The FY2017 Budget Appendix proposes no appropriations language for the Covered "
                          "Countermeasure Process Fund (HHS chapter, pp. 447-519, searched: only its schedules).",
    "Enacted": "P.L. 115-31 Division H has no 'Covered Countermeasures Process Fund' heading or appropriation (pp. "
               "368-433 searched).",
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
        if doc in SCOPE:
            body = body[SCOPE[doc][0]:SCOPE[doc][1]]
        if doc.startswith("SRC-BUDGET"):
            body = [re.sub(r"\[[^\[\]]*\]", "", p) for p in body]
        txt = " ".join(body)
        if st in MEDOPS_NONE:
            assert not re.search(r"may transfer up to \$[\d,]+ to such account from the Federal Hospital", txt), st
        if st in CCPF_NONE:
            scope = body
            pat = (r"(?i)covered countermeasures? process fund\s*✦?\s*for carrying out" if doc.startswith("SRC-BUDGET")
                   else r"(?i)covered countermeasures? process fund")
            assert not any(re.search(pat, p) for p in scope), st
        assert not re.search(r"(?i)adoption[^.]{0,200}(rescind|cancel)|(rescind|cancel)[^.]{0,300}adoption", txt), st
        assert not re.search(r"1820\(c\)\(2\)\(B\)|distance requirement", txt), st
        nef = re.search(r"(?i)nonrecurring expenses? fund", txt)
        assert bool(nef) == (st == "House Reported") or doc.startswith("SRC-BUDGET"), st   # (the request: schedules only)
        if doc.startswith("SRC-BUDGET"):
            assert not re.search(r"(?i)nonrecurring expenses? fund[^.]{0,300}rescind", txt), st
        n += 5
    page, pat, _ = NEF_SEC226
    if TEXT["House Reported"] in cache:
        assert re.search(pat, cache[TEXT["House Reported"]][page - 1]), "sec. 226"
        # (its one dollar figure is "reduced to $0": the reappropriation transfers, not an amount rescinded)
        assert not re.search(r"\$(?!0\b)[\d,]+", re.search(pat, cache[TEXT["House Reported"]][page - 1]).group(0))
        n += 1
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
        if not any(f[:2] == (NEF, st) for f in FOUND) and st != "House Reported":
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

    # the House NEF line: H.Rept. 114-699's table prints its figure (build_year recorded it); it cites sec. 226 too
    page, _, why = NEF_SEC226
    nef = [o for o in obs if o["canonical_account_id"] == NEF and o["fiscal_year"] == FY and o["stage"] == "House Reported"
           and not o["component"] and o["verification_status"] != "superseded"]
    assert len(nef) == 1, nef
    o = nef[0]
    if "sec. 226" not in o["source_table_or_section"]:
        o["source_table_or_section"] += f"; {why}: the figure is the table's [H.R. 5926 sec. 226, {DOCS[TEXT['House Reported']]['label']} p.{page}]"
        # (no source_text record: the bill states no amount, so there is nothing in it to check the figure against)
        print("cited sec. 226 on", o["observation_id"], f"{o['amount']:,}")

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
