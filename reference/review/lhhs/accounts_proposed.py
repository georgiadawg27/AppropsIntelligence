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
          "Administration for a Healthy America (proposed)": "Administration for a Healthy America",
          "Title II total": "Department of Health and Human Services", "Title II discretionary": "Department of Health and Human Services"}

NOTES = {
    "HRSA total": "Derived rollup (agency). Includes the Vaccine Injury Compensation trust fund and the Covered Countermeasures Process Fund (7,000 in FY2024: 8,888,090 + 276,697 + 7,000 = 9,171,787 in S.Rept. 118-207); 'Total, Health Resources and Services' (without 'Administration') is a different, smaller subtotal printed above it.",
    "Vaccine Injury Compensation Program Trust Fund": "Trust fund, mandatory: the law appropriates 'such sums as may be necessary' for claims plus a fixed HRSA administrative-expenses amount (15,200); the tables' claims figure is an estimate, not a law figure.",
    "NIH total (with CURES Act funding)": "Derived rollup (agency); the headline (decision 2026-09-27), component none. The CURES Act line is also kept as its own observation, component CURES (accounts.INCLUDED_COMPONENTS: inside this total, never added to it); the Title II total subtracts it. NIH's other totals are parallel siblings, stored under their printed scope: program_level_with_cures_and_phs_evaluation_act_funding, program_level_excluding_arpa_h. Label drift: FY2024 report prints '(NIH) with CURES Act funding'.",
    "NIH program level": "Parallel total, not proposed as the agency figure: adds PHS Evaluation (Sec. 241) transfers that are not new budget authority in this title. Listed so the grid can show it is not the same number.",
    "Grants to States for Medicaid": "Mandatory. Three printed totals, each its own observation under its printed scope (decision 2026-09-27): 'Total, Medicaid program level, available this fiscal year' (component program_level_available_this_fiscal_year; FY2026-27 House reports add a comma: 'Total, Medicaid, program level, ...'), 'Total, Grants to States for Medicaid' (the current-year appropriation, component none; S.Rept. 118-84 doesn't print it), 'Total, Grants to States for Medicaid, appropriated in this bill' (component appropriated_in_this_bill). The new first-quarter advance line is amount_type 'advance', fiscal_year = the year appropriated, the year it is for in its evidence.",
    "Grants to States for Medicaid, appropriated in this bill": "Current-year plus the next fiscal year's advance (law: 406,956,850 + 245,580,414 = 652,537,264 for FY2024); what CMS's total sums. An observation of Grants to States for Medicaid, component appropriated_in_this_bill -- not a separate Account.",
    "Payments to the Health Care Trust Funds": "Mandatory; label drift ('Payments to Trust Funds' in the Senate reports, 'Payments to Health Care Trust Funds' in the JES).",
    "CMS Program Management": "Funded by transfer from the Medicare trust funds (it sits in CMS's 'Trust Funds' memo line), not from the general fund.",
    "CMS total": "Derived rollup (agency). About 1.1-1.4 trillion of mandatory spending; the reason Title II is 1.25-1.56 trillion.",
    "LIHEAP": "No stable total row: the Senate reports print 'Total, LIHEAP, program level' (FY2024-25) or 'Total, LIHEAP' (FY2026); the FY2026 JES prints only 'Formula Grants'. FY2025 PB splits it into formula grants plus an emergency-designated line inside the total.",
    "Head Start": "The line item. S.Rept. 118-207's committee column adds an 'Additional funding (emergency)' line of 700,000 under it, so its 'Subtotal, Head Start' (12,971,820) is not the line (12,271,820); the extractor types the emergency line as supplemental.",
    "ACF total": "Derived rollup (agency). Parallel 'Total, ACF (excluding emergencies)' in some years; includes advance appropriations for the next fiscal year (Foster Care, Child Support) in the 'appropriated in this bill' sense.",
    "ASPR total": "Derived rollup (agency). A separate operating division in these tables (footnote 1/ in S.Rept. 118-207); counts toward Title II outside the Office of the Secretary total.",
    "OS total": "Derived rollup (agency). Includes Medicare Hearings and Appeals (trust fund), OIG, OCR, and Commissioned Officers' retirement pay (mandatory).",
    "Title II total": "Derived rollup (title). Reconciles as: ten agency totals (eleven in the FY2026-27 requests: the proposed Administration for a Healthy America) + General Provisions lines (signed) - the CURES Act line, in all 16 columns printed (title_ii_totals.py). The House and Senate reports scope FY2024's general provisions differently (the House counts two FY2024 rescissions), so their FY2024 Title II totals differ by 1,320,000 as printed.",
    "Title II discretionary": "Parallel title total, printed from FY2025 on; stored under component 'discretionary', not the title figure.",
    "Administration for a Healthy America (proposed)": "A reorganization proposed in the FY2026 and FY2027 requests (HRSA, SAMHSA and others folded in), inside Public Health Service; printed only in request columns, never enacted or reported by a committee. H.Rept. 119-696 prints no plain total, only 'Administration for a Healthy America, Discretionary 1/'. Proposed as an Account with request-stage figures only -- your call.",
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
        w.writerow([concept, kind, "no (variant)" if kind.endswith("_variant") else ("your call" if kind.endswith("_proposed") else "yes"), AGENCY[concept], "Title II",
                    labels, NOTES.get(concept, "")])


if __name__ == "__main__":
    main()
