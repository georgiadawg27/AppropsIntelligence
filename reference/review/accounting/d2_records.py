"""
D-2: new observations read from the documents (data/staged.json; no figure changed, no ID reused).

    python reference/review/accounting/d2_records.py           # what would be added
    python reference/review/accounting/d2_records.py --write   # add them (idempotent: a record already held is skipped)

Item 3 (decision 3 A): ACF FY2023 Enacted's emergency lines, S.Rept. 118-84's comparative table, 2023
appropriation column (thousands; the lines print on pp.384-385, the totals on pp.386-387). Each is a supplemental
'(emergency)' line, which tag_components' retag rule turns into designated/emergency. 'Formula Grants (emergency)'
prints inside 'Total, LIHEAP' (1,500,000 + 2,500,000 = 4,000,000, OBS-LHHS-0014), so inside_lines.csv names that headline
(the store keeps headline_observation_id for contained lines only); the two CR lines print after their accounts' totals and inside the ACF total. Not recorded: the
'(100,000)' prior-year advance (PL 117-58 Div J) is a parenthetical non-add, and Head Start (emergency) 275,000 is
the committee recommendation column. 2,500,000 + 1,000,000 + 1,775,000 = 5,275,000 = 52,962,553 (Total, ACF)
- 47,687,553 (Total, ACF (excluding emergencies)).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import staged_data  # noqa: E402
STAGED = ROOT / "data" / "staged"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
# confirmed absences the new bill-text headlines contradict: each rested on the report table's silence, which a
# confirmed absence may not (CLAUDE.md, Data); retired, IDs never reused (decision 4 A, owner 2026-10-13)
# the bill pages the new headlines cite (a source document's source_page is the range its observations cite)
DOC_PAGES = {"SRC-BILLS-117HR8295RH": "64", "SRC-BILLS-117S4659IS": "65-109"}
PAGE_NOTE = " Page citations are PDF page numbers (the same as the bill's printed page numbers)."
RETIRE_ABSENCES = {
    "CA-LHHS-0038": "CDC-Wide Activities FY2023 House: rested on a table's silence; H.R. 8295 p.64 prints $958,570,000 "
                    "(decision 4 A, owner 2026-10-13; D-2)",
    "CA-LHHS-0039": "CDC-Wide Activities FY2023 Senate: rested on a table's silence; S. 4659 p.69 prints $817,070,000 "
                    "(decision 4 A, owner 2026-10-13; D-2)",
}

ACF_EMERGENCY = [
    # account, amount (thousands), page, printed line, headline it prints inside ('' = beside its account's total),
    # component: the two CR lines are another act's money (P.L. 117-180, the CR), so supplemental_act -- which also
    # keeps the store's one-fact-per-cell index (two LIHEAP supplemental lines in one cell); the retag rule reads a
    # supplemental_act line labelled '(emergency)' as designated/emergency
    ("ACC-HHS-ACF-LIHEAP", 2500000, "384", "Formula Grants (emergency)", "OBS-LHHS-0014", ""),
    ("ACC-HHS-ACF-LIHEAP", 1000000, "384", "C R funding (Public Law 117–180) (Sec 146) (emergency)", "",
     "supplemental_act"),
    ("ACC-HHS-ACF-REFUGEE", 1775000, "385", "C R Funding—Public Law 117–180 (emergency)", "", "supplemental_act"),
]


# Item 5 (decision 7b): SAMHSA Mental Health FY2023 Enacted's $62,000,000 gap is printed: S.Rept. 118-84 p.380,
# 2023 appropriation column, 'CR Funding—Public Law 117–180 Suicide Lifeline' 62,000, inside the PRNS 'Subtotal'
# (991,453 + 62,000 + 12,000 = 1,065,453) and 'Subtotal, Mental Health' (2,755,507, OBS-LHHS-1664). Money of another
# law (the CR, P.L. 117-180) printed inside the headline: a supplemental_act line naming its headline, which the
# retag rule records as other_law inside it. 2,693,507 (P.L. 117-328's paragraph) + 62,000 = 2,755,507.
SAMHSA_CR = ("ACC-HHS-SAMHSA-MENTAL-HEALTH", 62000, "380", "CR Funding—Public Law 117–180 Suicide Lifeline",
             "OBS-LHHS-1664")


# Items 5b and 5c (decisions 4 A and 5 A, owner-approved; CDC FY2023 Senate's remaining gap): headlines the bill
# text prints, where the report table prints none we hold. Bill text beats the table; the table figure is the note.
SUB = "Title II, Centers for Disease Control and Prevention -- printed in"
BILL_HEADLINES = [
    # account, stage, chamber, amount, bill document, bill page, bill, heading, note
    ("ACC-HHS-CDC-PROGRAM-SUPPORT", "House Reported", "House", 958570000, "SRC-BILLS-117HR8295RH", "64", "H.R. 8295",
     "CDC-Wide Activities and Program Support",
     "H.Rept. 117-403 p.813 prints no budget-authority line for the account, only its 'Subtotal' (1,118,570) in "
     "thousands in the Bill column: the bill's 958,570 plus the Prevention and Public Health Fund block grant's "
     "160,000 (decision 4 A, owner 2026-10-13)", SUB),
    ("ACC-HHS-CDC-PROGRAM-SUPPORT", "Senate Reported", "Senate", 817070000, "SRC-BILLS-117S4659IS", "69", "S. 4659",
     "CDC-Wide Activities and Program Support",
     "the FY2023 Senate explanatory statement prints the account's 'Subtotal' 977,070 thousand (VAL-LHHS-02215): "
     "the bill's 817,070 plus the Prevention and Public Health Fund block grant's 160,000 (decision 4 A, owner "
     "2026-10-13)", SUB),
    ("ACC-HHS-CDC-BIRTH-DEFECTS", "Senate Reported", "Senate", 231060000, "SRC-BILLS-117S4659IS", "65", "S. 4659",
     "Birth Defects, Developmental Disabilities, Disabilities and Health",
     "the FY2023 Senate explanatory statement's table line for the account is not held (its parser could not place "
     "it, and the document's host is not reachable from the D-2 session), so no table figure is noted (D-2 item 5c)",
     SUB),
    ("ACC-HHS-CDC-EEOICP", "Senate Reported", "Senate", 55358000, "SRC-BILLS-117S4659IS", "67", "S. 4659",
     "Energy Employees Occupational Illness Compensation Program",
     "the FY2023 Senate explanatory statement's table line for the account is not held (its parser could not place "
     "it, and the document's host is not reachable from the D-2 session), so no table figure is noted (D-2 item 5c)",
     SUB),
    ("ACC-HHS-OS-ONC", "Senate Reported", "Senate", 0, "SRC-BILLS-117S4659IS", "109", "S. 4659",
     "Office of the National Coordinator for Health Information Technology",
     "the paragraph's whole amount, $74,238,000, 'shall be from amounts made available under section 241 of the PHS "
     "Act' (headline $0 as the bill text prints it; the phase 1b ONC rule; decision 5 A, owner 2026-10-13); the FY2023 "
     "Senate explanatory statement's table line for ONC is not held",
     "Title II, Office of the Secretary -- printed in"),
]


def wanted():
    out = []
    for account, stage, chamber, amount, doc, page, bill, heading, note, sub in BILL_HEADLINES:
        what = "no new budget authority" if amount == 0 else f"${amount:,}"
        # only a report-table figure is a cell note ('-- Note:'); anything else is part of the section text
        tail = (f" -- Note: {note}" if note.startswith(("H.Rept.", "the FY2023 Senate explanatory statement prints"))
                else f" ({note})")
        out.append({"canonical_account_id": account, "fiscal_year": 2023, "stage": stage, "chamber": chamber,
                    "amount": amount, "amount_type": "budget authority", "component": "",
                    "headline_observation_id": "", "offsetting_collections": "FALSE", "transfer_link_account_id": "",
                    "source_document_id": doc, "source_page": page,
                    "source_table_or_section": f"{sub} {bill} under '{heading}': {what}; bill or law text controls "
                                               f"over the report table for the same scope (owner 2026-10-11)"
                                               + tail,
                    "extraction_method": "text-extracted", "confidence": 0.95, "verification_status": "unverified"})
    account, thousands, page, line, inside = SAMHSA_CR
    out.append({"canonical_account_id": account, "fiscal_year": 2023, "stage": "Enacted", "chamber": "N/A",
                "amount": thousands * 1000, "amount_type": "supplemental", "component": "supplemental_act",
                "headline_observation_id": "", "offsetting_collections": "FALSE", "transfer_link_account_id": "",
                "source_document_id": "SRC-CRPT-118SRPT84", "source_page": page,
                "source_table_or_section": f"Title II, Substance Abuse and Mental Health Services Administration -- "
                                           f"printed as '{line}' [2023 appropriation]",
                "extraction_method": "text-extracted", "confidence": 0.95, "verification_status": "unverified"})
    for account, thousands, page, line, inside, comp in ACF_EMERGENCY:
        out.append({"canonical_account_id": account, "fiscal_year": 2023, "stage": "Enacted", "chamber": "N/A",
                    "amount": thousands * 1000, "amount_type": "supplemental", "component": comp,
                    "headline_observation_id": "", "offsetting_collections": "FALSE",
                    "transfer_link_account_id": "", "source_document_id": "SRC-CRPT-118SRPT84", "source_page": page,
                    "source_table_or_section": f"Title II, Administration for Children and Families -- printed as "
                                               f"'{line}' [2023 appropriation]",
                    "extraction_method": "text-extracted", "confidence": 0.95, "verification_status": "unverified"})
    return out


def same(a, b):
    keys = ("canonical_account_id", "fiscal_year", "stage", "amount", "amount_type", "source_document_id",
            "source_page", "source_table_or_section")
    return all(a.get(k) == b.get(k) for k in keys)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    data = staged_data.load()
    obs = data["observations"]
    n = max(int(o["observation_id"].split("-")[-1]) for o in obs if o["observation_id"].startswith("OBS-LHHS-"))
    added = []
    for w in wanted():
        if any(same(o, w) for o in obs):
            continue
        n += 1
        rec = {"observation_id": f"OBS-LHHS-{n:04d}", **w}
        obs.append(rec)
        added.append(rec)
    for r in added:
        print(f"{r['observation_id']} {r['canonical_account_id']} FY{r['fiscal_year']} {r['stage']} "
              f"${r['amount']:,} p.{r['source_page']}: {r['source_table_or_section']}")
    print(f"{len(added)} new observations")
    docs_changed = []
    for x in data["source_docs"]:
        if x["document_id"] in DOC_PAGES and x["source_page"] != DOC_PAGES[x["document_id"]]:
            x["source_page"] = DOC_PAGES[x["document_id"]]
            if PAGE_NOTE.strip() not in x["notes"]:
                x["notes"] += PAGE_NOTE
            docs_changed.append(x["document_id"])
    print("source documents given their cited pages:", ", ".join(docs_changed) or "none")
    retired = json.loads(RETIRED.read_text())
    gone = [c for c in data["confirmed_absences"] if c["confirmed_absence_id"] in RETIRE_ABSENCES]
    for c in gone:
        print(f"retire {c['confirmed_absence_id']}: {RETIRE_ABSENCES[c['confirmed_absence_id']]}")
    if args.write and (added or gone or docs_changed):
        data["confirmed_absences"] = [c for c in data["confirmed_absences"]
                                      if c["confirmed_absence_id"] not in RETIRE_ABSENCES]
        retired["confirmed_absences"].update(RETIRE_ABSENCES)
        staged_data.save(data)
        RETIRED.write_text(json.dumps(retired, indent=1) + "\n")
        print("data/staged.json and retired_ids.json updated")


if __name__ == "__main__":
    sys.exit(main())
