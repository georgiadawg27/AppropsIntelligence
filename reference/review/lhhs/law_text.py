"""
The enacted appropriation, as the public law itself states it, for the
scoped accounts the bill language appropriates a dollar figure to --
checked against what the report tables print for the same Enacted year.

    python reference/review/lhhs/law_text.py > reference/review/lhhs/law_text.csv

Title II's page span is found from the law's own headers ("TITLE II
DEPARTMENT OF HEALTH AND HUMAN SERVICES" up to "TITLE III DEPARTMENT OF
EDUCATION"), which is also the check that Title II is HHS in that act.
Agency totals are not in bill language -- only the reports and JES print
them -- so they are not here.

  Vaccine Injury Compensation: the law says "such sums as may be necessary"
  for claims; only the administrative-expenses figure is a dollar amount.
  Medicaid: the law appropriates the current-year amount and, in the same
  paragraph, the next fiscal year's first-quarter advance.
"""

import csv
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
LAWS = [("PLAW-118publ47", 2024), ("PLAW-119publ75", 2026)]

# (concept, heading as the law prints it, which dollar figures after it are wanted)
HEADINGS = [("Primary Health Care", "PRIMARY HEALTH CARE", ["appropriation"]),
            ("National Cancer Institute", "NATIONAL CANCER INSTITUTE", ["appropriation"]),
            ("LIHEAP", "LOW INCOME HOME ENERGY ASSISTANCE", ["appropriation"]),
            ("Grants to States for Medicaid", "GRANTS TO STATES FOR MEDICAID", ["appropriation", "advance for next fiscal year"]),
            ("Payments to the Health Care Trust Funds", "PAYMENTS TO THE HEALTH CARE TRUST FUNDS", ["appropriation"]),
            ("CMS Program Management", "PROGRAM MANAGEMENT", ["appropriation"]),
            ("AHRQ", "AGENCY FOR HEALTHCARE RESEARCH AND QUALITY HEALTHCARE RESEARCH AND QUALITY", ["appropriation"]),
            ("Vaccine Injury Compensation Program Trust Fund", "VACCINE INJURY COMPENSATION PROGRAM TRUST FUND", ["administrative expenses"])]


def title_ii(pdf):
    pages = [p.get_text() for p in pymupdf.open(pdf)]
    start = next(i for i, s in enumerate(pages) if re.search(r"TITLE II\s+DEPARTMENT OF HEALTH AND HUMAN SERVICES", s))
    end = next(i for i in range(start + 1, len(pages)) if re.search(r"TITLE III\s+DEPARTMENT OF EDUCATION", pages[i]))
    return start + 1, end + 1, re.sub(r"\s+", " ", " ".join(pages[start:end + 1]))


def main():
    out = []
    for pkg, fy in LAWS:
        first, last, text = title_ii(ROOT / "document_store" / f"{pkg}.pdf")
        for concept, heading, wanted in HEADINGS:
            m = re.search(re.escape(heading) + r" For", text)
            figures = re.findall(r"\$([\d,]+\d)", text[m.end():m.end() + 700]) if m else []
            for what, fig in zip(wanted, figures):
                out.append({"law": pkg, "fiscal_year": fy, "title_ii_pdf_pages": f"{first}-{last}", "concept": concept,
                            "figure": what, "amount_thousands": int(fig.replace(",", "")) // 1000,
                            "context": text[m.end() - len(heading) - 4:m.end() + 160]})
        m = re.search(r"Provided, That \$([\d,]+) shall be for making payments under the Head Start Act", text)
        if m:
            out.append({"law": pkg, "fiscal_year": fy, "title_ii_pdf_pages": f"{first}-{last}", "concept": "Head Start",
                        "figure": "appropriation", "amount_thousands": int(m.group(1).replace(",", "")) // 1000,
                        "context": text[m.start() - 60:m.end() + 40]})
    w = csv.DictWriter(sys.stdout, fieldnames=list(out[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(out)


if __name__ == "__main__":
    main()
