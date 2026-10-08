"""
The owner's decisions on the FY2021 backfill (PR #35, 2026-10-08). Appends to data/staged.json; no existing figure,
ID or status changes except where the owner says so.

    python reference/review/lhhs/backfill/owner_decisions_fy2021.py           # what would change
    python reference/review/lhhs/backfill/owner_decisions_fy2021.py --write   # apply

1. CMS Program Management, FY2021 Enacted: 3,669,744 stays (Division H's heading, exactly). The FY2022 Senate draft's
   FY2021 figure (3,974,744) is 305,000 more: P.L. 116-260 Division H sec. 227 lets the Secretary transfer up to
   $305,000,000 to the account from the Medicare trust funds (PDF p.415, 134 Stat. 1595) -- recorded as the 'Medicare
   Operations' general-provision line (3. below). The three pending records (CMS Program Management, CMS total,
   Title II total) are resolved with a corner note. All divisions were searched; the other CMS Program Management
   amounts (Division CC: $2M, $9M, $10M transfers for other purposes) do not make up the 305,000.
2. ASPR: the FY2023 reports' FY2022 column prints no ASPR structure ('Total, PHSSEF' 3,199,678: H.Rept. 117-403
   p.836; the FY2023 Senate draft p.419), so the test runs one year later: the FY2024 report's FY2023 column
   (S.Rept. 118-84) restates FY2023 as ASPR 3,629,677 (RDP 3,062,991 + OPER 566,686, pp.389-390) + a residual PHSSEF
   137,892 (p.392) = 3,767,569 = the FY2023 law's PHSSEF paragraphs exactly (P.L. 117-328 div. H: 1,647,569,000 +
   820,000,000 + 965,000,000 + 335,000,000). REL-LHHS-0009/0010 become confirmed split_from; ASPR's agency total gets
   the same relationship (REL-LHHS-0016) so all three accounts read 'no figure' before FY2023, with the note
   'Funded within PHSSEF before FY2023.' on their first year.
3. A confirmed absence cites the bill or law text, never a table's silence. For each general-provision line the
   FY2021/FY2022 tables leave missing, and for CCPF, the bill text (House: the reported bill; Senate: the chair's
   draft / S. 3062; request: the Budget Appendix's proposed language, bracketed text being deleted law; Enacted: the
   law's Labor-HHS division) was searched: a provision found is recorded from the text (section and page); none
   found is a confirmed absence citing the search. CCPF's FY2021 money came only through a supplemental (Division
   M's transfer authority): a note on the FY2021 Enacted cell.
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
TODAY = "2026-10-08"
REVIEWER = "owner rules (2026-10-08)"
MEDOPS, NEF, ADOPT, LIMIT, CCPF = ("ACC-HHS-GP-MEDICARE-OPERATIONS", "ACC-HHS-GP-NEF-RESCISSION",
                                   "ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", "ACC-HHS-GP-MEDICARE-LIMITATION",
                                   "ACC-HHS-HRSA-CCPF")

# the bill and law texts: (document id, file, label, publication date, stage, fiscal year, congress-session, agency, type)
DOCS = {
    "SRC-BILLS-116HR7614RH": ("BILLS-116hr7614rh.pdf", "H.R. 7614 (reported)", "2020-07-15", "House Reported", 2021,
                              "116-2", "U.S. House of Representatives (bill; govinfo BILLS collection)", "bill",
                              "https://www.govinfo.gov/content/pkg/BILLS-116hr7614rh/pdf/BILLS-116hr7614rh.pdf",
                              "H.R. 7614 as reported (BILLS-116hr7614rh): the FY2021 House Labor-HHS bill (H.Rept. 116-450)."),
    "SRC-BILL-LHHS-FY2021-SENATE": ("LHHSFY2021.pdf", "the Senate chair's draft bill, FY2021", "2020-11-10",
                                    "Senate Reported", 2021, "116-2", "Senate Committee on Appropriations (chair's draft bill)",
                                    "bill", "https://www.appropriations.senate.gov/imo/media/doc/LHHSFY2021.pdf",
                                    "The Senate chair's draft Labor-HHS bill for FY2021, released 2020-11-10 with the "
                                    "explanatory statement (SRC-EXPL-LHHS-FY2021-SENATE); never introduced."),
    "SRC-BILLS-117HR4502RH": ("BILLS-117hr4502rh.pdf", "H.R. 4502 (reported)", "2021-07-19", "House Reported", 2022,
                              "117-1", "U.S. House of Representatives (bill; govinfo BILLS collection)", "bill",
                              "https://www.govinfo.gov/content/pkg/BILLS-117hr4502rh/pdf/BILLS-117hr4502rh.pdf",
                              "H.R. 4502 as reported (BILLS-117hr4502rh): the FY2022 House Labor-HHS bill (H.Rept. 117-96)."),
    "SRC-BILLS-117S3062IS": ("BILLS-117s3062is.pdf", "S. 3062 (introduced)", "2021-10-25", "Senate Reported", 2022,
                             "117-1", "U.S. Senate (bill; govinfo BILLS collection)", "bill",
                             "https://www.govinfo.gov/content/pkg/BILLS-117s3062is/pdf/BILLS-117s3062is.pdf",
                             "S. 3062 as introduced (BILLS-117s3062is): the FY2022 Senate chair's draft Labor-HHS bill "
                             "(SRC-EXPL-LHHS-FY2022-SENATE), introduced 2021-10-25, never reported."),
    "SRC-BUDGET-APP-FY2021": ("BUDGET-2021-APP.pdf", "the FY2021 Budget Appendix", "2020-02-10", "President's Budget",
                              2021, "", "Office of Management and Budget", "budget_appendix",
                              "https://www.govinfo.gov/content/pkg/BUDGET-2021-APP/pdf/BUDGET-2021-APP.pdf",
                              "Budget of the U.S. Government, FY2021, Appendix (whole volume). Proposed appropriations "
                              "language: bracketed text is enacted language proposed for deletion."),
    "SRC-BUDGET-APP-FY2022": ("BUDGET-2022-APP.pdf", "the FY2022 Budget Appendix", "2021-05-28", "President's Budget",
                              2022, "", "Office of Management and Budget", "budget_appendix",
                              "https://www.govinfo.gov/content/pkg/BUDGET-2022-APP/pdf/BUDGET-2022-APP.pdf",
                              "Budget of the U.S. Government, FY2022, Appendix (whole volume). Proposed appropriations "
                              "language: bracketed text is enacted language proposed for deletion."),
    "SRC-PLAW-116PUBL260": ("PLAW-116publ260.pdf", "P.L. 116-260", "2020-12-27", "Enacted", 2021, "116-2",
                            "U.S. Congress (public law; govinfo PLAW collection)", "public_law",
                            "https://www.govinfo.gov/content/pkg/PLAW-116publ260/pdf/PLAW-116publ260.pdf",
                            "Consolidated Appropriations Act, 2021; Division H (Labor-HHS) is PDF pp. 367-447."),
    "SRC-PLAW-117PUBL103": ("PLAW-117publ103.pdf", "P.L. 117-103", "2022-03-15", "Enacted", 2022, "117-2",
                            "U.S. Congress (public law; govinfo PLAW collection)", "public_law",
                            "https://www.govinfo.gov/content/pkg/PLAW-117publ103/pdf/PLAW-117publ103.pdf",
                            "Consolidated Appropriations Act, 2022; Division H (Labor-HHS) is PDF pp. 373-453."),
}
SHA = {"SRC-BILLS-116HR7614RH": "86944d5feceb1c77", "SRC-BILL-LHHS-FY2021-SENATE": "f7f91278ec09cd06",
       "SRC-BILLS-117HR4502RH": "2338d7b8862f234f", "SRC-BILLS-117S3062IS": "6409256665fd4f6c",
       "SRC-BUDGET-APP-FY2021": "3c490c8fce7d2664", "SRC-BUDGET-APP-FY2022": "20b539e8ee140736",
       "SRC-PLAW-116PUBL260": "32285d91badb98bb", "SRC-PLAW-117PUBL103": "e2c47b8c6d20fb65"}
TEXT = {(2021, "House Reported"): "SRC-BILLS-116HR7614RH", (2021, "Senate Reported"): "SRC-BILL-LHHS-FY2021-SENATE",
        (2021, "President's Budget"): "SRC-BUDGET-APP-FY2021", (2021, "Enacted"): "SRC-PLAW-116PUBL260",
        (2022, "House Reported"): "SRC-BILLS-117HR4502RH", (2022, "Senate Reported"): "SRC-BILLS-117S3062IS",
        (2022, "President's Budget"): "SRC-BUDGET-APP-FY2022", (2022, "Enacted"): "SRC-PLAW-117PUBL103"}
WHERE = {"SRC-PLAW-116PUBL260": "Division H", "SRC-PLAW-117PUBL103": "Division H",
         "SRC-BUDGET-APP-FY2021": "the HHS chapter's proposed language (pp. 425-511, bracketed text excluded)",
         "SRC-BUDGET-APP-FY2022": "the HHS chapter's proposed language (pp. 425-511, bracketed text excluded)"}
# each text's Title II (HHS) rescissions / cancellations, read in full (the evidence for the rescission lines)
TITLE_II_RESCISSIONS = {
    "SRC-BILLS-116HR7614RH": "sec. 241 (Nonrecurring Expenses Fund, $600,000,000, p.122)",
    "SRC-BILL-LHHS-FY2021-SENATE": "none (sec. 237 makes $87,000,000 and other Nonrecurring Expenses Fund balances "
                                   "available, p.121; it rescinds nothing)",
    "SRC-BUDGET-APP-FY2021": "sec. 224 (Nonrecurring Expenses Fund, $500,000,000 cancelled, p.506)",
    "SRC-PLAW-116PUBL260": "sec. 238 (Nonrecurring Expenses Fund, $375,000,000, p.418)",
    "SRC-BILLS-117HR4502RH": "sec. 237 (Nonrecurring Expenses Fund, $500,000,000, p.127) and sec. 239 (a biosafety "
                             "level 4 laboratory's balances rescinded and re-appropriated, p.128)",
    "SRC-BILLS-117S3062IS": "sec. 234 (Nonrecurring Expenses Fund, $500,000,000, pp.131-132) and sec. 236 (the "
                            "biosafety level 4 laboratory's balances rescinded and re-appropriated, p.133)",
    "SRC-BUDGET-APP-FY2022": "sec. 225 (Nonrecurring Expenses Fund, $500,000,000 cancelled, p.507) and the biosafety "
                             "level 4 laboratory's balances cancelled and re-appropriated (p.508)",
    "SRC-PLAW-117PUBL103": "sec. 236 (Nonrecurring Expenses Fund, $650,000,000, p.426) and sec. 237 (the biosafety "
                           "level 4 laboratory's balances rescinded and re-appropriated, p.426)",
}

# provisions found: (account, fy, stage, amount, amount_type, page, section text, the quote checked on the page)
FOUND = [
    (MEDOPS, 2021, "Senate Reported", 305_000_000, "budget authority", 114,
     "Title II, General Provisions, sec. 227 (Medicare Operations) -- 'the Secretary ... may transfer up to "
     "$305,000,000 to such account from the Federal Hospital Insurance Trust Fund and the Federal Supplementary "
     "Medical Insurance Trust Fund' ['Centers for Medicare & Medicaid Services, Program Management'] [bill text]",
     r"may transfer up to \$305,000,000 to such account"),
    (MEDOPS, 2021, "Enacted", 305_000_000, "budget authority", 415,
     "Division H, Title II, General Provisions, sec. 227 (Medicare Operations; 134 Stat. 1594-1595) -- 'may transfer "
     "up to $305,000,000' to 'Centers for Medicare & Medicaid Services, Program Management' from the Medicare trust "
     "funds [law text]", r"\$305,000,000 to such account"),
    (MEDOPS, 2022, "Enacted", 355_000_000, "budget authority", 423,
     "Division H, Title II, General Provisions, sec. 227 (Medicare Operations; 136 Stat. 471) -- 'may transfer up to "
     "$355,000,000' to 'Centers for Medicare & Medicaid Services, Program Management' from the Medicare trust funds "
     "[law text]", r"may transfer up to \$355,000,000 to such account"),
    (NEF, 2021, "House Reported", -600_000_000, "rescission", 122,
     "Title II, General Provisions, sec. 241 (RESCISSION) -- 'Of the unobligated balances in the \"Nonrecurring "
     "Expenses Fund\" established in section 223 of division G of Public Law 110-161, $600,000,000 are hereby "
     "rescinded' [bill text]", r"\$600,000,000 are hereby rescinded"),
    (NEF, 2021, "President's Budget", -500_000_000, "rescission", 506,
     "HHS General Provisions (proposed), sec. 224 (CANCELLATION) -- 'Of the unobligated balances available in the "
     "\"Nonrecurring Expenses Fund\" ..., $500,000,000 are hereby permanently cancelled' [Budget Appendix language]",
     r"\$500,000,000 are hereby (\[rescinded[^\]]*\] )?permanently cancelled"),
    (NEF, 2021, "Enacted", -375_000_000, "rescission", 418,
     "Division H, Title II, General Provisions, sec. 238 (RESCISSION; 134 Stat. 1598) -- 'Of the unobligated balances "
     "in the \"Nonrecurring Expenses Fund\" ..., $375,000,000 are hereby rescinded' [law text]",
     r"\$375,000,000 are hereby rescinded"),
    (NEF, 2022, "House Reported", -500_000_000, "rescission", 127,
     "Title II, General Provisions, sec. 237 (RESCISSION) -- 'Of the unobligated balances in the \"Nonrecurring "
     "Expenses Fund\" ..., $500,000,000 are hereby rescinded' [bill text]", r"\$500,000,000 are hereby rescinded"),
    (NEF, 2022, "Senate Reported", -500_000_000, "rescission", 132,
     "Title II, General Provisions, sec. 234 (RESCISSION; pp.131-132) -- 'Of the unobligated balances in the "
     "\"Nonrecurring Expenses Fund\" ..., $500,000,000 are hereby rescinded' [bill text]",
     r"\$500,000,000 are hereby rescinded"),
    (NEF, 2022, "President's Budget", -500_000_000, "rescission", 507,
     "HHS General Provisions (proposed), sec. 225 (CANCELLATION) -- 'Of the unobligated balances in the "
     "\"Nonrecurring Expenses Fund\" ..., $500,000,000 are hereby permanently cancelled' [Budget Appendix language]",
     r"\$500,000,000 are hereby (\[rescinded[^\]]*\] )?permanently cancelled"),
    (NEF, 2022, "Enacted", -650_000_000, "rescission", 426,
     "Division H, Title II, General Provisions, sec. 236 (RESCISSION; 136 Stat. 474) -- 'Of the unobligated balances "
     "in the \"Nonrecurring Expenses Fund\" ..., $650,000,000 are hereby rescinded' [law text]",
     r"\$650,000,000 are hereby rescinded"),
]
# what each search found where no provision exists: {(account, component): (amount_type, evidence(doc, fy, stage))}
MEDOPS_NONE = {
    (2021, "House Reported"): "H.R. 7614 has no section letting the Secretary transfer Medicare trust fund amounts to "
                              "'Centers for Medicare & Medicaid Services, Program Management' (whole text searched for "
                              "/may transfer up to $... to such account from the Federal Hospital/; the CMS Program "
                              "Management heading itself provides 'not to exceed $3,984,744,000', p.79).",
    (2021, "President's Budget"): "The FY2021 Budget Appendix brackets the FY2020 law's sec. 227 ($305,000,000 "
                                  "transfer) for deletion (p.505) and proposes no such section.",
    (2022, "House Reported"): "H.R. 4502 has no section letting the Secretary transfer Medicare trust fund amounts to "
                              "'Centers for Medicare & Medicaid Services, Program Management' (whole text searched; the "
                              "CMS Program Management heading itself provides 'not to exceed $4,315,843,000', p.81).",
    (2022, "Senate Reported"): "S. 3062 has no section letting the Secretary transfer Medicare trust fund amounts to "
                               "'Centers for Medicare & Medicaid Services, Program Management' (whole text searched; the "
                               "CMS Program Management heading itself provides 'not to exceed $4,250,843,000', p.89).",
    (2022, "President's Budget"): "The FY2022 Budget Appendix brackets the FY2021 law's sec. 227 ($305,000,000 "
                                  "transfer) for deletion (p.506) and proposes no such section.",
}
CCPF_NONE = {
    (2021, "House Reported"): "H.R. 7614 has no 'Covered Countermeasures Process Fund' heading or appropriation (whole "
                              "text searched); its only mention is authority to transfer funds from another heading to "
                              "the fund (p.203).",
    (2021, "Senate Reported"): "The Senate chair's draft bill for FY2021 never mentions the Covered Countermeasures "
                               "Process Fund (whole text searched).",
    (2021, "President's Budget"): "The FY2021 Budget Appendix proposes no appropriations language for the Covered "
                                  "Countermeasure Process Fund: its HHS entry (p.443) is a program and financing "
                                  "schedule only.",
}
CCPF_NOTE = ("No CCPF appropriation in Division H; Division M (Coronavirus Response and Relief Supplemental "
             "Appropriations Act, 2021) lets its PHSSEF funds be transferred to the fund (134 Stat. 1917).")

# 1. CMS Program Management
CMS_NOTE = ("The FY2022 Senate draft's FY2021 figure also includes $305,000,000 from Division H sec. 227 (a transfer "
            "from the Medicare trust funds; P.L. 116-260, 134 Stat. 1595), recorded as Medicare Operations.")
CMS_VALS = ("VAL-LHHS-10823", "VAL-LHHS-10824", "VAL-LHHS-10881")
CMS_RESOLUTION = ("Our figure is Division H's heading exactly; the FY2022 Senate draft's FY2021 column adds the "
                  "$305,000,000 Division H sec. 227 lets the Secretary transfer to the account (P.L. 116-260 PDF p.415). "
                  "All divisions were searched for CMS Program Management amounts: Division CC's ($2M, $9M, $10M) are "
                  "for other purposes. Owner 2026-10-08. Note: " + CMS_NOTE)

# 2. ASPR
ASPR_TEST = ("Confirmed by the owner's test (2026-10-08), run on the first restated year: the FY2023 reports' FY2022 "
             "column prints no ASPR structure ('Total, PHSSEF' 3,199,678: H.Rept. 117-403 p.836; the FY2023 Senate "
             "draft p.419), so the FY2024 report's FY2023 column was used: S.Rept. 118-84 restates FY2023 as ASPR "
             "3,629,677 (Research, Development, and Procurement 3,062,991 + Operations, Preparedness, and Emergency "
             "Response 566,686; pp.389-390) + a residual PHSSEF 137,892 (p.392) = 3,767,569 (thousands), exactly the "
             "FY2023 law's PHSSEF paragraphs (P.L. 117-328 div. H: $1,647,569,000 + $820,000,000 + $965,000,000 + "
             "$335,000,000 = $3,767,569,000). Before FY2023 ASPR's programs were funded within PHSSEF. "
             "Note: Funded within PHSSEF before FY2023.")
ASPR_TOTAL_REL = {"from_account_id": "ACC-HHS-ASPR-TOTAL", "to_account_id": "ACC-HHS-OS-PHSSEF",
                  "relationship_type": "split_from", "effective_fiscal_year": "2024",
                  "evidence": "ASPR's agency total, split from PHSSEF with its two accounts (REL-LHHS-0009, "
                              "REL-LHHS-0010). " + ASPR_TEST, "confidence": 1.0, "human_reviewed": "TRUE"}


def pages(path):
    """A PDF's text layer, one string per page, the bill print's line numbers and hyphenation removed."""
    import subprocess
    raw = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True).stdout
    out = []
    for t in raw.split("\f"):
        t = re.sub(r"(?m)^\s*\d{1,2}\s*$", "", t)
        t = re.sub(r"(?m)^\s*\d{1,2}\s+(?=\S)", "", t)
        t = re.sub(r"([a-z])-\n\s*([a-z])", r"\1\2", t)
        t = re.sub(r"([a-z])\d{1,2} ([a-z])", r"\1\2", t)
        out.append(" ".join(t.split()).replace("’’", '"').replace("‘‘", '"'))
    return out


def check_texts():
    """Each recorded provision's quote is on its cited page; each absence's search finds nothing (where the texts
    are on file). Returns the number of checks run."""
    n, cache = 0, {}
    for doc, (fn, *_rest) in DOCS.items():
        if (STORE / fn).exists():
            cache[doc] = pages(STORE / fn)
    for aid, fy, st, amount, t, page, _s, quote in FOUND:
        doc = TEXT[(fy, st)]
        if doc in cache:
            assert re.search(quote, cache[doc][page - 1]), (aid, fy, st, page)
            n += 1
    for (fy, st), _e in list(MEDOPS_NONE.items()):
        doc = TEXT[(fy, st)]
        if doc in cache:
            txt = " ".join(cache[doc])
            if doc.startswith("SRC-BUDGET"):
                txt = re.sub(r"\[[^\[\]]*\]", "", " ".join(cache[doc][424:511]))
            assert not re.search(r"may transfer up to \$[\d,]+ to such account from the Federal Hospital", txt), (fy, st)
            n += 1
    for (fy, st), _e in CCPF_NONE.items():
        doc = TEXT[(fy, st)]
        if doc in cache:
            hits = [i + 1 for i, p in enumerate(cache[doc]) if re.search(r"(?i)covered countermeasures? process fund", p)]
            assert all(h in (203, 443, 1371, 1380) for h in hits), (fy, st, hits)
            n += 1
    for doc, txt in cache.items():
        body = txt if not doc.startswith("SRC-BUDGET") else [re.sub(r"\[[^\[\]]*\]", "", p) for p in txt]
        assert not any(re.search(r"(?i)adoption[^.]{0,200}(rescind|cancel)|(rescind|cancel)[^.]{0,300}adoption", p)
                       for p in body), doc
        assert not any(re.search(r"(?i)limitation[^.]{0,80}title XVIII", p) for p in body), doc
        n += 2
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = data["observations"]
    by_id = {o["observation_id"]: o for o in obs}
    acct = {x["canonical_account_id"]: x for x in data["accounts"]}
    assert "SRC-PLAW-116PUBL260" not in {d["document_id"] for d in data["source_docs"]}, "already applied"
    print("text checks:", check_texts())

    def ids(prefix, field, rows_, width):
        n = max(int(r[field][len(prefix):]) for r in rows_ if r[field].startswith(prefix) and r[field][len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    oids = ids("OBS-LHHS-", "observation_id", obs, 4)
    vids = ids("VAL-LHHS-", "validation_id", data["validations"], 5)
    cids = ids("CA-LHHS-", "confirmed_absence_id", data["confirmed_absences"], 4)
    rids = ids("REL-LHHS-", "relationship_id", data["relationships"], 4)
    have = {(o["canonical_account_id"], o["fiscal_year"], o["stage"], o["amount_type"], o["component"] or "")
            for o in obs if o.get("verification_status") != "superseded"}
    have |= {(x["canonical_account_id"], x["fiscal_year"], x["stage"], x["amount_type"], x["component"] or "")
             for x in data["confirmed_absences"]}
    new_obs, new_val, new_abs = [], [], []

    # 1. CMS
    for vid in CMS_VALS:
        v = next(v for v in data["validations"] if v["validation_id"] == vid)
        o = by_id[v["observation_id"]]
        assert v["human_review_status"] == "pending" and o["fiscal_year"] == 2021 and o["stage"] == "Enacted"
        assert re.search(r"(3,974,744|906,932,157|1,016,887,685)", v["expected_result"]), v
        v.update({"human_review_status": "resolved", "reviewer": REVIEWER, "resolution": CMS_RESOLUTION})
        print("resolved", vid, o["canonical_account_id"])

    # 2. ASPR
    for r in data["relationships"]:
        if r["relationship_id"] in ("REL-LHHS-0009", "REL-LHHS-0010"):
            assert r["relationship_type"] == "uncertain" and r["human_reviewed"] == "FALSE"
            r.update({"relationship_type": "split_from", "confidence": 1.0, "human_reviewed": "TRUE",
                      "evidence": r["evidence"] + " " + ASPR_TEST})
            print("confirmed", r["relationship_id"], r["from_account_id"], "split_from", r["to_account_id"])
    rel = {"relationship_id": next(rids), **ASPR_TOTAL_REL}
    print("new", rel["relationship_id"], rel["from_account_id"], "split_from", rel["to_account_id"])

    # 3. the general-provision lines and CCPF, from the texts
    def text_val(oid, doc, page, quote_text, amount):
        new_val.append({"observation_id": oid, "rule_applied": "source_text",
                        "expected_result": f"{DOCS[doc][1]} p.{page}: {quote_text}",
                        "observed_result": f"{amount:,} as recorded", "result": "pass"})

    for aid, fy, st, amount, t, page, section, _q in FOUND:
        assert (aid, fy, st, t, "") not in have, (aid, fy, st)
        doc = TEXT[(fy, st)]
        o = {"observation_id": next(oids), "canonical_account_id": aid, "fiscal_year": fy, "stage": st,
             "chamber": {"House Reported": "House", "Senate Reported": "Senate"}.get(st, "N/A"),
             "bill_id": None, "report_id": None, "amount": amount, "amount_type": t, "offsetting_collections": "FALSE",
             "transfer_link_account_id": "", "source_document_id": doc, "source_page": str(page),
             "source_table_or_section": section, "extraction_method": "text-extracted", "confidence": 0.95,
             "verification_status": "", "component": "", "headline_observation_id": ""}
        new_obs.append(o)
        text_val(o["observation_id"], doc, page, re.search(r"'[^']*\$[\d,]+[^']*'", section).group(0), amount)
        print("recorded", aid, fy, st, f"{amount:,}", o["observation_id"], doc, f"p.{page}")

    def absent(aid, fy, st, t, comp, evidence):
        if (aid, fy, st, t, comp) in have:
            return
        new_abs.append({"confirmed_absence_id": next(cids), "canonical_account_id": aid, "fiscal_year": fy,
                        "stage": st, "amount_type": t, "component": comp, "source_document_id": TEXT[(fy, st)],
                        "evidence": evidence, "confirmed_date": TODAY})

    for (fy, st), doc in TEXT.items():
        where = WHERE.get(doc, "the whole text")
        name = DOCS[doc][1]
        if (fy, st) in MEDOPS_NONE:
            absent(MEDOPS, fy, st, "budget authority", "", MEDOPS_NONE[(fy, st)])
        nef = [f for f in FOUND if f[:3] == (NEF, fy, st)]
        if not nef:
            absent(NEF, fy, st, "rescission", "", f"{name}: no rescission of Nonrecurring Expenses Fund balances in "
                                                  f"{where}. Its Title II rescissions: {TITLE_II_RESCISSIONS[doc]}.")
        absent(NEF, fy, st, "rescission", "emergency",
               f"{name}: no emergency-designated rescission of Nonrecurring Expenses Fund balances in {where}. Its "
               f"Title II rescissions: {TITLE_II_RESCISSIONS[doc]}; none is designated an emergency requirement.")
        absent(ADOPT, fy, st, "rescission", "",
               f"{name}: no rescission of Adoption Incentives (or other adoption) funds in {where} (searched for "
               f"'adoption' in the same sentence as a rescission or cancellation: none). Its Title II rescissions: "
               f"{TITLE_II_RESCISSIONS[doc]}.")
        absent(LIMIT, fy, st, "budget authority", "",
               f"{name}: no limitation on amounts under title XVIII of the Social Security Act in {where} (searched "
               f"for 'limitation' within a sentence of 'title XVIII': none; 'XVIII' appears only in the CMS Program "
               f"Management heading's list of Social Security Act titles). The FY2026 line this account records is "
               f"not itself identified in P.L. 119-75 (account notes).")
        if (fy, st) in CCPF_NONE:
            absent(CCPF, fy, st, "budget authority", "", CCPF_NONE[(fy, st)])

    # CCPF FY2021 Enacted: the recorded '---' (0) is confirmed by the law's text, with the supplemental's note
    z = [o for o in obs if o["canonical_account_id"] == CCPF and (o["fiscal_year"], o["stage"]) == (2021, "Enacted")
         and o.get("verification_status") != "superseded"]
    assert len(z) == 1 and z[0]["amount"] == 0, z
    new_val.append({"observation_id": z[0]["observation_id"], "rule_applied": "law_text",
                    "expected_result": "P.L. 116-260 div. H (pp. 367-447): no 'Covered Countermeasures Process Fund' "
                                       "heading or appropriation (whole division searched); Division M p.737 lets "
                                       "PHSSEF supplemental funds be transferred to the fund",
                    "observed_result": "0 as recorded. Note: " + CCPF_NOTE, "result": "pass"})

    for v in new_val:
        v.update({"validation_id": next(vids), "human_review_status": "", "reviewer": "", "resolution": ""})
    checks = collections.defaultdict(list)
    for v in data["validations"] + new_val:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                            v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    for vid in CMS_VALS:                         # resolved: the status rule no longer counts the difference
        o = by_id[next(v["observation_id"] for v in data["validations"] if v["validation_id"] == vid)]
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
        print("status", o["observation_id"], o["verification_status"])
    print(f"{len(new_obs)} observations ({collections.Counter(o['verification_status'] for o in new_obs)}), "
          f"{len(new_val)} validations, {len(new_abs)} confirmed absences "
          f"({collections.Counter(x['canonical_account_id'][8:] + ' ' + (x['component'] or '-') for x in new_abs)}), "
          f"1 relationship, 2 relationships confirmed, 3 records resolved, {len(DOCS)} source documents")
    if a.write:
        tmpl = data["source_docs"][0]
        for doc, (fn, label, date, st, fy, cs, agency, typ, url, note) in DOCS.items():
            data["source_docs"].append({**{k: "" for k in tmpl}, "document_id": doc, "source_agency": agency,
                                        "url_or_identifier": url, "document_type": typ, "congress_session": cs,
                                        "fiscal_year": fy, "publication_date": date, "stage": st,
                                        "retrieval_timestamp": TODAY, "source_page": "",
                                        "notes": f"{note} sha256 {SHA[doc]}... (the file at the link). Page "
                                                 f"citations are PDF page numbers."})
        data["relationships"].append(rel)
        order = list(dict.fromkeys(list(obs[0])))
        data["observations"] += [{k: o.get(k, "") for k in order if k in o} for o in new_obs]
        vorder = list(data["validations"][0])
        data["validations"] += [{k: v[k] for k in vorder} for v in new_val]
        data["confirmed_absences"] += new_abs
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
