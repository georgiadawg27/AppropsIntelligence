"""
Proposed Account rows for the Labor-HHS Title II proof -- a scoped subset
(the ten agency totals, the title total, and nine accounts chosen for the
HHS structures they exercise), for a person to confirm before any of it
goes into a workbook. The labels each document actually prints are read
from cross_document_rows.csv, so the variants listed are observed, not
assumed.

    python reference/review/lhhs/accounts_proposed.py > reference/review/lhhs/accounts_proposed.csv
"""

import csv
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cross_document import CONCEPTS  # noqa: E402

AGENCY = {"HRSA total": "Health Resources and Services Administration", "Primary Health Care": "Health Resources and Services Administration",
          "Health Centers": "Health Resources and Services Administration",
          "Vaccine Injury Compensation Program Trust Fund": "Health Resources and Services Administration",
          "CDC total": "Centers for Disease Control and Prevention", "National Cancer Institute": "National Institutes of Health",
          "NIH total (with CURES Act funding)": "National Institutes of Health", "NIH program level": "National Institutes of Health",
          "SAMHSA total": "Substance Abuse and Mental Health Services Administration", "AHRQ total": "Agency for Healthcare Research and Quality",
          "Grants to States for Medicaid": "Centers for Medicare & Medicaid Services",
          "Grants to States for Medicaid, appropriated in this bill": "Centers for Medicare & Medicaid Services",
          "Payments to the Health Care Trust Funds": "Centers for Medicare & Medicaid Services",
          "CMS Program Management": "Centers for Medicare & Medicaid Services", "CMS total": "Centers for Medicare & Medicaid Services",
          "LIHEAP": "Administration for Children and Families", "Head Start": "Administration for Children and Families",
          "ACF total": "Administration for Children and Families", "ACL total": "Administration for Community Living",
          "ASPR total": "Administration for Strategic Preparedness and Response", "OS total": "Office of the Secretary",
          "Title II total": "Department of Health and Human Services", "Title II discretionary": "Department of Health and Human Services"}

NOTES = {
    "HRSA total": "Derived rollup (agency). Includes the Vaccine Injury Compensation trust fund and the Covered Countermeasures Process Fund (7,000 in FY2024: 8,888,090 + 276,697 + 7,000 = 9,171,787 in S.Rept. 118-207); 'Total, Health Resources and Services' (without 'Administration') is a different, smaller subtotal printed above it.",
    "Vaccine Injury Compensation Program Trust Fund": "Trust fund, mandatory: the law appropriates 'such sums as may be necessary' for claims plus a fixed HRSA administrative-expenses amount (15,200); the tables' claims figure is an estimate, not a law figure.",
    "NIH total (with CURES Act funding)": "Derived rollup (agency). NIH prints up to four parallel totals (with CURES; program level with CURES and PHS Evaluation; program level excluding ARPA-H; Subtotal). Proposed: this one, because the Public Health Service rollup and the Title II total are built on it. Label drift: FY2024 report prints '(NIH) with CURES Act funding'.",
    "NIH program level": "Parallel total, not proposed as the agency figure: adds PHS Evaluation (Sec. 241) transfers that are not new budget authority in this title. Listed so the grid can show it is not the same number.",
    "Grants to States for Medicaid": "Mandatory; the current-year appropriation. The same paragraph of the law also appropriates next year's first-quarter advance; tables print three different totals (program level available this fiscal year / current-year total / appropriated in this bill). S.Rept. 118-84 prints no current-year total, only 'appropriated in this bill'. Decision needed: which of these is the Account's figure and how the advance is recorded.",
    "Grants to States for Medicaid, appropriated in this bill": "Current-year plus the next fiscal year's advance (law: 406,956,850 + 245,580,414 = 652,537,264 for FY2024). Listed as a variant, not proposed as a separate Account.",
    "Payments to the Health Care Trust Funds": "Mandatory; label drift ('Payments to Trust Funds' in the Senate reports, 'Payments to Health Care Trust Funds' in the JES).",
    "CMS Program Management": "Funded by transfer from the Medicare trust funds (it sits in CMS's 'Trust Funds' memo line), not from the general fund.",
    "CMS total": "Derived rollup (agency). About 1.1-1.4 trillion of mandatory spending; the reason Title II is 1.25-1.56 trillion.",
    "LIHEAP": "No stable total row: the Senate reports print 'Total, LIHEAP, program level' (FY2024-25) or 'Total, LIHEAP' (FY2026); the FY2026 JES prints only 'Formula Grants'. FY2025 PB splits it into formula grants plus an emergency-designated line inside the total.",
    "Head Start": "The line item. S.Rept. 118-207's committee column adds an 'Additional funding (emergency)' line of 700,000 under it, so its 'Subtotal, Head Start' (12,971,820) is not the line (12,271,820); the extractor types the emergency line as supplemental.",
    "ACF total": "Derived rollup (agency). Parallel 'Total, ACF (excluding emergencies)' in some years; includes advance appropriations for the next fiscal year (Foster Care, Child Support) in the 'appropriated in this bill' sense.",
    "ASPR total": "Derived rollup (agency). A separate operating division in these tables (footnote 1/ in S.Rept. 118-207); counts toward Title II outside the Office of the Secretary total.",
    "OS total": "Derived rollup (agency). Includes Medicare Hearings and Appeals (trust fund), OIG, OCR, and Commissioned Officers' retirement pay (mandatory).",
    "Title II total": "Derived rollup (title). Reconciles as: ten agency totals + General Provisions lines (signed) - the CURES Act line (title_ii_totals.py). Not a sum of its agency rollups alone.",
    "Title II discretionary": "Parallel title total, printed from FY2025 on; not proposed as the title figure.",
}


def main():
    variants = OrderedDict((c, OrderedDict()) for c, _, _ in CONCEPTS)
    with open(HERE / "cross_document_rows.csv", newline="") as f:
        for r in csv.DictReader(f):
            variants[r["concept"]].setdefault(r["label_as_printed"], OrderedDict())[r["document"]] = None
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["concept", "kind", "proposed", "agency", "title", "labels_as_printed (documents)", "notes"])
    for concept, kind, _ in CONCEPTS:
        labels = " | ".join(f"{lab} ({', '.join(docs)})" for lab, docs in variants[concept].items())
        w.writerow([concept, kind, "no (variant)" if kind.endswith("_variant") else "yes", AGENCY[concept], "Title II",
                    labels, NOTES.get(concept, "")])


if __name__ == "__main__":
    main()
