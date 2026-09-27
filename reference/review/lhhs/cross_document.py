"""
Labor-HHS Title II (HHS) proof: the scoped accounts, read from every
extraction on hand, and whether documents that print the same fiscal year x
stage agree.

    python reference/review/lhhs/cross_document.py
        -> reference/review/lhhs/cross_document_rows.csv   (one row per figure found)
           reference/review/lhhs/cross_document.csv        (one row per concept x FY x stage printed by 2+ documents)

Reads extractions/*.title-ii.json (extract_approps.py --title "TITLE II";
gitignored, so rerun the extractor first). A concept is matched on the row's
printed label only -- never on its position in the extractor's hierarchy,
which the HHS tables' indentation doesn't encode reliably. Labels are
compared after folding dash variants (the text layer prints "ARPA–H", the
vision transcription "ARPA-H"). Memo lines (printed in parentheses) are
left out.
"""

import csv
import glob
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent

# (concept, kind, printed-label pattern). kind: agency_total / account / title_total,
# or *_variant for a parallel total the documents print beside the one scoped.
CONCEPTS = [
    ("HRSA total", "agency_total", r"^Total, Health Resources and Services Administration$"),
    ("Primary Health Care", "account", r"^Total, Primary Health Care$"),
    ("Health Centers", "account", r"^Health Centers$"),
    ("Vaccine Injury Compensation Program Trust Fund", "account", r"^Total, Vaccine Injury Compensation (Program )?Trust Fund$"),
    ("CDC total", "agency_total", r"^Total, Centers for Disease Control and Prevention$"),
    ("National Cancer Institute", "account", r"^National Cancer Institute( \(NCI\))?$"),
    ("NIH total (with CURES Act funding)", "agency_total",
     r"^Total, National Institutes of Health (\(NIH\) with CURES Act funding|\(with CURES Act funding\))$"),
    ("NIH program level", "agency_total_variant", r"^Total, National Institutes of Health,? program level"),
    ("SAMHSA total", "agency_total", r"^Total, (SAMHSA|Substance Abuse and Mental Health Services Administration)$"),
    ("AHRQ total", "agency_total", r"^Total, (AHRQ|Agency for Healthcare Research and Quality)$"),
    ("Grants to States for Medicaid", "account", r"^Total, Grants to States for Medicaid$"),
    ("Grants to States for Medicaid, appropriated in this bill", "account_variant",
     r"^Total, Grants to States for Medicaid, appropriated in this bill$"),
    ("Payments to the Health Care Trust Funds", "account", r"^Total, Payments to (the )?(Health Care )?Trust Funds$"),
    ("CMS Program Management", "account", r"^Total, Program Management$"),
    ("CMS total", "agency_total", r"^Total, Centers for Medicare (and|&) Medicaid Services$"),
    ("LIHEAP", "account", r"^Total, LIHEAP(, program level)?$|^Total, Low Income Home Energy Assistance"),
    ("Head Start", "account", r"^Head Start$"),
    ("ACF total", "agency_total", r"^Total, Administration for Children and Families$"),
    ("ACL total", "agency_total", r"^Total, Administration for Community Living$"),
    ("ASPR total", "agency_total", r"^Total, (Administration|Office of the Assistant Secretary) for (Strategic )?Preparedness and Response$"),
    ("OS total", "agency_total", r"^Total, Office of the Secretary$"),
    ("Title II total", "title_total", r"^Total, Title II, Department of Health and Human Services$"),
    ("Title II discretionary", "title_total_variant", r"^Total, Title II, Department of Health and Human Services,? discretionary$"),
]
DASHES = re.compile(r"[‐-―−]")


def fold(label):
    return re.sub(r"\s+", " ", DASHES.sub("-", label)).strip().rstrip(".")


def collect(paths):
    rows = []
    for path in paths:
        d = json.load(open(path))
        pkg = d["source_document"]["package_id"]
        for o in d["observations"]:
            if o["is_memo"] or o["amount"] is None or o["fiscal_year"] is None:
                continue
            label = fold(o["account_name_as_written"])
            for concept, kind, rx in CONCEPTS:
                if re.match(DASHES.sub("-", rx), label, re.I):
                    rows.append({"concept": concept, "kind": kind, "fiscal_year": o["fiscal_year"], "stage": o["stage"],
                                 "document": pkg, "column_header": o["column_header"], "page": o["source_page"],
                                 "label_as_printed": o["account_name_as_written"], "amount_thousands": o["amount"] // 1000,
                                 "extraction_method": o["extraction_method"]})
    return rows


def agreement(rows):
    cells = defaultdict(list)
    for r in rows:
        cells[(r["concept"], r["fiscal_year"], r["stage"])].append(r)
    out = []
    for (concept, fy, stage), rs in cells.items():
        if len({r["document"] for r in rs}) < 2:
            continue
        out.append({"concept": concept, "fiscal_year": fy, "stage": stage,
                    "agree": "yes" if len({r["amount_thousands"] for r in rs}) == 1 else "NO",
                    "figures": "; ".join(f"{r['document']} p.{r['page']} ({r['extraction_method']}) {r['amount_thousands']:,}" for r in rs)})
    return sorted(out, key=lambda r: (r["concept"], r["fiscal_year"], r["stage"]))


def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    rows = collect(sorted(glob.glob(str(ROOT / "extractions" / "*.title-ii.json"))))
    write(OUT / "cross_document_rows.csv", rows)
    agree = agreement(rows)
    write(OUT / "cross_document.csv", agree)
    print(f"{len(rows)} figures; {len(agree)} cells printed by 2+ documents; "
          f"{sum(r['agree'] == 'yes' for r in agree)} agree", file=sys.stderr)


if __name__ == "__main__":
    main()
