"""
Labor-HHS Title II backfill, one fiscal year at a time (FY2022 back to FY2017). Appends to data/staged.json;
never changes an existing figure, ID or status (an existing source document may gain an also_covers entry).

    python reference/review/lhhs/backfill/build_year.py 2022            # report only
    python reference/review/lhhs/backfill/build_year.py 2022 --write    # append

Each stage of the year reads one column of one extracted comparative table (extract_approps.py --title
"TITLE II"; House reports' image-only tables carry a tesseract text layer first, ocr_pdf.py, so vision reads
only the pages whose OCR fails the table's arithmetic):
  House Reported      -- the year's House report, "Bill" column
  President's Budget  -- the same report's request column
  Senate Reported     -- the Senate report or chair's draft, "Committee recommendation"
  Enacted             -- the FY+1 House report's "FY<N> Enacted" column
The figures are found as in reference/review/lhhs/fy2024_house/build_rows.py: every Labor-HHS fact on file
(account x amount_type x component) carries the labels it was printed under (and its account's former names);
the same label, cleaned the same way, is looked up in the column, inside the account's agency section; exact
label matches only. A label that matches different rows takes the label the same chamber's later tables use.

Checks: the extractor's own records on the row (table_total, structural, source_text, unit, semantic) --
routine memo / parse notes resolved at creation with the triage's standard reasons (reviewer "triage rules"),
anything else pending; the sum checks (agency totals, the NIH institutes, the Title II total); cross_document
(the Enacted column against the other chamber's FY+1 table, the request column against the other chamber's);
law_text runs separately (reference/review/law_text.py). Statuses by validate_approps.verification_status.
Writes reference/review/lhhs/backfill/fy<N>_report.csv (every fact: found / not printed / ambiguous) and
reference/review/backfill_unmatched_<FY>.csv (headings no account matches).
"""

import argparse
import collections
import csv
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "reference" / "review"))
sys.path.insert(0, str(ROOT / "reference" / "review" / "lhhs" / "fy2024_house"))

import validate_approps as V  # noqa: E402
import triage as T  # noqa: E402
import derived_headlines as DH  # noqa: E402
from build_rows import AGENCY_TOTALS, AGENCY_KEY, PRINTED  # noqa: E402
from build_rows import clean as _clean  # noqa: E402


def clean(label):
    """build_rows.clean, with the fiscal year in a label made generic ('New advance, 1st quarter, FY 2023' is the
    same row in every year's table) and a bare footnote digit dropped."""
    s = re.sub(r"\b(fy|fiscal year)\s*(19|20)\d\d\b", r"\1 ####", _clean(label), flags=re.I)
    # a footnote mark printed as a bare digit after a word ('Total, AHRQ Program Level 3', 'Federal funds 3')
    return re.sub(r"(?<=[a-z]) \d$", "", s)

STAGED = ROOT / "data" / "staged.json"
TODAY = "2026-10-08"
HOUSE_TABLES = ["SRC-CRPT-119HRPT696", "SRC-CRPT-119HRPT271", "SRC-CRPT-118HRPT585", "SRC-EXPL-LHHS-FY2024-HOUSE",
                "SRC-CRPT-117HRPT403", "SRC-CRPT-117HRPT96", "SRC-CRPT-116HRPT450", "SRC-CRPT-116HRPT62",
                "SRC-CRPT-115HRPT862", "SRC-CRPT-115HRPT244", "SRC-CRPT-114HRPT699"]
SENATE_TABLES = ["SRC-CRPT-119SRPT55", "SRC-CRPT-118SRPT207", "SRC-CRPT-118SRPT84", "SRC-EXPL-LHHS-FY2023-SENATE",
                 "SRC-EXPL-LHHS-FY2022-SENATE", "SRC-EXPL-LHHS-FY2021-SENATE", "SRC-CRPT-115SRPT289",
                 "SRC-CRPT-115SRPT150", "SRC-CRPT-114SRPT274"]
TRIAGE_REVIEWER = "triage rules"
NIH_OD = "ACC-HHS-NIH-OD"
OWNER_RULE = "owner rules (2026-10-08)"

# ---- the years ---------------------------------------------------------------------------------------------
YEARS = {
    2022: {
        "stages": [
            # stage, chamber, extraction package, column header, source document
            ("House Reported", "House", "CRPT-117hrpt96", "Bill", "SRC-CRPT-117HRPT96"),
            ("President's Budget", "N/A", "CRPT-117hrpt96", "FY 2022 Request", "SRC-CRPT-117HRPT96"),
            ("Senate Reported", "Senate", "MANUAL-LHHS-FY2022-SenateReported-explanatory_statement-b033ae17",
             "Committee recommendation", "SRC-EXPL-LHHS-FY2022-SENATE"),
            ("Enacted", "N/A", "CRPT-117hrpt403", "FY 2022 Enacted", "SRC-CRPT-117HRPT403"),
        ],
        # where the stage's table has no readable row for a fact, the other FY+1 committee table's column
        "fallback": {"Enacted": ("MANUAL-LHHS-FY2023-SenateReported-explanatory_statement-88301550", "2022 appropriation",
                                 "SRC-EXPL-LHHS-FY2023-SENATE")},
        # the owner's approved former names (2026-10-08): written to Historical Name; 'printed' are the table labels
        "historical_names": [
            {"canonical_account_id": "ACC-HHS-HRSA-HEALTH-SYSTEMS", "former_name": "Health Care Systems",
             "printed": ["Subtotal, Health Care Systems Bureau, appropriation"],
             "evidence": "H.Rept. 117-96 p.470 and the FY2022 Senate chair's draft explanatory statement p.345 print the "
                         "group as 'Health Care Systems' with 'Subtotal, Health Care Systems Bureau, appropriation' "
                         "(Organ Transplantation, Cord Blood, C.W. Bill Young, 340B, Poison Control, Hansen's Disease); "
                         "the FY2023+ tables print the same programs as 'Health Systems'. Approved by the owner 2026-10-08."},
            {"canonical_account_id": "ACC-HHS-NIH-NICHD",
             "former_name": "National Institute of Child Health and Human Development",
             "printed": ["National Institute of Child Health and Human Development (NICHD)"],
             "evidence": "H.Rept. 117-96 p.476 and the FY2022 Senate chair's draft explanatory statement p.348 print "
                         "'National Institute of Child Health and Human Development (NICHD)'; later tables print 'Eunice "
                         "Kennedy Shriver National Institute of Child Health and Human Development'. Approved by the owner "
                         "2026-10-08."},
        ],
        # a short agency sum (owner's rule 3, 2026-10-08): the printed lines that make up the shortfall. 'inside': money
        # already inside an existing account (an unrecorded line of it); 'other_law': another law's line; 'outside':
        # money in none of our accounts -- the check stays pending and the owner decides (the proposal)
        "sum_items": [
            {"total": "ACC-HHS-ACF-TOTAL", "label": r"new advance, 1st quarter, fy ####", "path": "Child Support Enforcement",
             "kind": "inside", "why": "ACC-HHS-ACF-CHILD-SUPPORT's new advance for the next fiscal year, printed below its "
             "headline ('available in this bill') and counted in the ACF total -- an advance line of the account, not "
             "recorded (follow-up)"},
            {"total": "ACC-HHS-CDC-TOTAL", "label": r"cr funding - p\.l\. 117-43 vessel sanitation program.*",
             "kind": "other_law", "why": "another law's line (the P.L. 117-43 continuing resolution, Vessel Sanitation "
             "Program, sec. 138) printed under Environmental Health and counted in the CDC total"},
            {"total": "ACC-HHS-HRSA-TOTAL", "label": r"program management", "kind": "outside",
             "proposal": "money outside our accounts: HRSA's FY2022 'Program Management' heading -- proposed Historical "
                         "Name 'Program Management' for ACC-HHS-HRSA-PROGRAM-SUPPORT (the later tables' 'Program Support'); "
                         "the owner decides"},
            {"total": "ACC-HHS-NIH-TOTAL", "label": r"gabriella miller kids first research act.*", "kind": "outside",
             "proposal": "money outside our accounts: printed under the NIH Office of the Director, but our FY2022 "
                         "ACC-HHS-NIH-OD figure is the 'Office of the Director' line alone, while FY2023-FY2026 record "
                         "'Subtotal, Office of the Director', which includes this line -- proposed: derive FY2022 OD as "
                         "Office of the Director + this line (the later scope); the owner decides"},
            {"total": "ACC-HHS-ACF-TOTAL", "label": r"diaper grants", "kind": "outside",
             "proposal": "money outside our accounts: printed under Social Services Block Grant (House bill only), but "
                         "our ACC-HHS-ACF-SSBG figure is the SSBG line alone -- proposed: a new account (Diaper Grants), "
                         "or count it in SSBG for FY2022 House; the owner decides"},
        ],
        # the Title II total counts the CURES Act lines inside NIH's total (it prints no 'less' line for them)
        "title_counts_cures": True,
        # a request column read from another request baseline (owner's rule 5): keep the House report's column
        "request_baseline": {
            "stage": "President's Budget", "other": "SRC-EXPL-LHHS-FY2022-SENATE", "difference": -612_000,
            "reason": "The Senate draft's budget estimate is the request before the -$1 million permanent reduction the "
                      "budget appendix applies to Payments to States for Child Support Enforcement and Family Support "
                      "Programs (075-1501: appropriation $2,795 million, $2,794 million after the reduction); the House report's request "
                      "column is after it: Repatriation 9,388 (H.Rept. 117-96 p.484) vs 10,000 (Senate draft p.353), "
                      "612 thousand. Kept the House report's request column (owner's rule, 2026-10-08).",
            "note": "The Senate draft prints this request $612,000 higher: Repatriation 10,000 thousand before the "
                    "budget's permanent reduction (the House report prints 9,388 after it).",
        },
        # rows the label rule can't reach, read by hand: (stage, account) -> (cleaned label, why)
        "proposals": {
            "national institute of child health and human development": "Historical Name 'National Institute of Child Health and "
                "Human Development' for ACC-HHS-NIH-NICHD (renamed 'Eunice Kennedy Shriver ...' in later tables)",
            "subtotal, health care systems bureau, appropriation": "Historical Name 'Health Care Systems' for "
                "ACC-HHS-HRSA-HEALTH-SYSTEMS (the FY2023+ 'Health Systems' heading); its FY2022 lines are the same programs",
            "subtotal, ncats": "not an account: NCATS's subtotal with its transfer line; ACC-HHS-NIH-NCATS is recorded from its own line",
            "subtotal, buildings and facilities": "not an account: Buildings and Facilities' subtotal; ACC-HHS-NIH-BUILDINGS-FACILITIES "
                "is recorded from its own line",
            "total, nih program level (with transfer)": "not an account: a program-level view of the NIH total "
                "(ACC-HHS-NIH-TOTAL, component program_level, if wanted)",
            "total, substance abuse prevention": "not an account: the same figure as ACC-HHS-SAMHSA-PREVENTION, recorded from "
                "its 'Programs of Regional and National Significance' line",
            "mental and behavorial health": "not an account: a program line inside ACC-HHS-HRSA-HEALTH-WORKFORCE "
                "(Interdisciplinary Community-Based Linkages; 'Behavioral' misspelled in the table)",
            "340b drug pricing program/office of pharmacy affairs": "not an account: a program line of the FY2022 'Health "
                "Care Systems' group (see the Historical Name proposal for ACC-HHS-HRSA-HEALTH-SYSTEMS)",
            "public health loan repayment program": "not an account: a HRSA Health Workforce program line (printed '---')",
            "assistant secretary for administration, cybersecurity": "not an account: a PHSSEF line (ACC-HHS-OS-PHSSEF)",
            "office of security and strategic information": "not an account: a PHSSEF line (ACC-HHS-OS-PHSSEF)",
            "mental health crisis response grants": "not an account: a SAMHSA Mental Health program line",
        },
        "override": {("Enacted", "ACC-HHS-NIH-ARPA-H"): (
            "advanced research projects",
            "H.Rept. 117-403 prints ARPA-H twice: under NIH with '---' for FY2022, and under its own heading after the "
            "Office of the Secretary with 1,000,000 (P.L. 117-103 appropriated FY2022's ARPA-H funds to the Office of "
            "the Secretary); the second is the FY2022 figure")},
        "cross": [
            # stage of our new figure, other document's package, its column, its source document
            ("Enacted", "MANUAL-LHHS-FY2023-SenateReported-explanatory_statement-88301550", "2022 appropriation",
             "SRC-EXPL-LHHS-FY2023-SENATE"),
            ("President's Budget", "MANUAL-LHHS-FY2022-SenateReported-explanatory_statement-b033ae17", "Budget estimate",
             "SRC-EXPL-LHHS-FY2022-SENATE"),
        ],
        "also_covers": {"SRC-CRPT-117HRPT403": "FY2022 Enacted", "SRC-EXPL-LHHS-FY2023-SENATE": "FY2022 Enacted"},
        "docs": [
            {"document_id": "SRC-CRPT-117HRPT96", "source_agency": "House Committee on Appropriations",
             "url_or_identifier": "https://www.govinfo.gov/content/pkg/CRPT-117hrpt96/pdf/CRPT-117hrpt96.pdf",
             "document_type": "committee_report", "congress_session": "117-1", "fiscal_year": 2022,
             "publication_date": "2021-07-19 00:00:00", "stage": "House Reported",
             "retrieval_timestamp": "2026-10-08 00:00:00", "source_page": "466-496",
             "also_covers": "FY2022 President's Budget",
             "notes": "H.Rept. 117-96 (H.R. 4502). sha256 0c9efae48e754378... (the govinfo content PDF at the link). "
                      "Page citations are PDF page numbers; the Title II comparative statement (pp. 466-496) is "
                      "image-only: read from a tesseract text layer (400 dpi), 30 pages re-read by vision where the "
                      "OCR failed the table's arithmetic."},
            {"document_id": "SRC-EXPL-LHHS-FY2022-SENATE", "source_agency": "Senate Committee on Appropriations",
             "url_or_identifier": "https://www.appropriations.senate.gov/imo/media/doc/LHHSREPT_FINAL3.PDF",
             "document_type": "explanatory_statement", "congress_session": "117-1", "fiscal_year": 2022,
             "publication_date": "2021-10-18 00:00:00", "stage": "Senate Reported",
             "retrieval_timestamp": "2026-10-08 00:00:00", "source_page": "334-375",
             "also_covers": "FY2022 President's Budget",
             "notes": "Senate chair's draft explanatory statement for FY2022, released with S. 3062 (introduced "
                      "2021-10-25, never reported). sha256 b033ae17fc2e948b... (the file at the link). Text layer; "
                      "page citations are PDF page numbers."},
        ],
        "brr": [
            {"reference_id": "BR-LHHS-FY2022-HOUSE", "stage": "House Reported", "bill_id": "H.R.4502",
             "report_id": "H.Rept.117-96",
             "bill_url": "https://www.govinfo.gov/content/pkg/BILLS-117hr4502rh/pdf/BILLS-117hr4502rh.pdf",
             "report_jes_url": "https://www.govinfo.gov/content/pkg/CRPT-117hrpt96/pdf/CRPT-117hrpt96.pdf", "draft": "FALSE"},
            {"reference_id": "BR-LHHS-FY2022-SENATE", "stage": "Senate Reported", "bill_id": "S.3062", "report_id": "N/A",
             "bill_url": "https://www.congress.gov/117/bills/s3062/BILLS-117s3062is.pdf",
             "report_jes_url": "https://www.appropriations.senate.gov/imo/media/doc/LHHSREPT_FINAL3.PDF",
             "notes": "Senate chair's draft released 2021-10-18; S. 3062 introduced 2021-10-25 and referred, never reported",
             "draft": "TRUE"},
            {"reference_id": "BR-LHHS-FY2022-PB", "stage": "President's Budget", "bill_id": "PREX 2.8:2022/APP",
             "report_id": "N/A", "bill_url": "https://www.govinfo.gov/content/pkg/BUDGET-2022-APP/pdf/BUDGET-2022-APP.pdf",
             "report_jes_url": "N/A", "draft": "FALSE"},
            {"reference_id": "BR-LHHS-FY2022-ENACTED", "stage": "Enacted", "bill_id": "P.L.117-103", "report_id": "N/A",
             "bill_url": "https://www.govinfo.gov/content/pkg/PLAW-117publ103/pdf/PLAW-117publ103.pdf",
             "report_jes_url": "N/A", "vehicle_bill_id": "H.R. 2471", "division": "H", "enactment_date": "2022-03-15",
             "funding_type": "omnibus", "draft": "FALSE"},
        ],
    },
}

YEARS[2021] = {
    "stages": [
        ("House Reported", "House", "CRPT-116hrpt450", "Bill", "SRC-CRPT-116HRPT450"),
        ("President's Budget", "N/A", "CRPT-116hrpt450", "FY 2021 Request", "SRC-CRPT-116HRPT450"),
        ("Senate Reported", "Senate", "MANUAL-LHHS-FY2021-SenateReported-explanatory_statement-6c401e04",
         "Committee recommendation", "SRC-EXPL-LHHS-FY2021-SENATE"),
        ("Enacted", "N/A", "CRPT-117hrpt96", "FY 2021 Enacted", "SRC-CRPT-117HRPT96"),
    ],
    "fallback": {"Enacted": ("MANUAL-LHHS-FY2022-SenateReported-explanatory_statement-b033ae17", "2021 appropriation",
                             "SRC-EXPL-LHHS-FY2022-SENATE")},
    "historical_names": [],
    "sum_items": [it for it in YEARS[2022]["sum_items"] if it["kind"] == "inside"],
    # one-off proposals under another heading (owner's standing rule): a part line of the account, with a note
    "proposal_parts": [
        {"account": "ACC-HHS-NIH-TOTAL", "agency": "NIH", "label": r"national institute for research on safety and quality.*",
         "component": "request_proposal",
         "note": "The President's request proposed moving AHRQ into NIH as the National Institute for Research on Safety "
                 "and Quality (NIRSQ), printed inside NIH's total (not enacted); AHRQ's own request lines are 0."}],
    "components": [{"component_id": "request_proposal", "label": "National Institute for Research on Safety and Quality (NIRSQ)",
                    "kind": "contained", "description": "a line the President's request proposed, printed inside the "
                                                        "account's total (not enacted); the cell shows it as a note"}],
    "title_counts_cures": True,
    # accounts that did not exist yet: a printed dash/zero in this year's column is a confirmed absence, not a figure
    "absent_before": {"ACC-HHS-NIH-ARPA-H": 2022},
    "request_appendix": {"stage": "President's Budget", "other": "SRC-EXPL-LHHS-FY2021-SENATE",
                         "appendix": "document_store/BUDGET-2021-APP.pdf"},
    "proposals": {**YEARS[2022]["proposals"], **{'304b drug pricing': "not an account: the OCR's '304B' for '340B Drug Pricing', a line of the Health Care Systems group (inside ACC-HHS-HRSA-HEALTH-SYSTEMS)", 'subtotal, health care systems, program level': 'not an account: a program-level view of ACC-HHS-HRSA-HEALTH-SYSTEMS (with its PHS evaluation funding)',
        'total, nih, program level with title vi emergency funding': "not an account: a program-level view of ACC-HHS-NIH-TOTAL (with the House bill's Title VI emergency funding)", 'total, ryan white hiv/aids program level': 'not an account: a program-level view of ACC-HHS-HRSA-RYAN-WHITE',
        'transfers from nonrecurring expenses fund': 'not an account: a transfer from the Nonrecurring Expenses Fund, not new budget authority in this title',
        'total, payments for foster care and permanency': "not an account: another scope of ACC-HHS-ACF-FOSTER-CARE (its lines, before the advance adjustments; the headline is 'Total, Payments to States, available in this bill' on the same page)"}},
    "cross": [
        ("Enacted", "MANUAL-LHHS-FY2022-SenateReported-explanatory_statement-b033ae17", "2021 appropriation",
         "SRC-EXPL-LHHS-FY2022-SENATE"),
        ("President's Budget", "MANUAL-LHHS-FY2021-SenateReported-explanatory_statement-6c401e04", "Budget estimate",
         "SRC-EXPL-LHHS-FY2021-SENATE"),
    ],
    "also_covers": {"SRC-CRPT-117HRPT96": "FY2021 Enacted", "SRC-EXPL-LHHS-FY2022-SENATE": "FY2021 Enacted"},
    "docs": [
        {"document_id": "SRC-CRPT-116HRPT450", "source_agency": "House Committee on Appropriations",
         "url_or_identifier": "https://www.govinfo.gov/content/pkg/CRPT-116hrpt450/pdf/CRPT-116hrpt450.pdf",
         "document_type": "committee_report", "congress_session": "116-2", "fiscal_year": 2021,
         "publication_date": "2020-07-15 00:00:00", "stage": "House Reported",
         "retrieval_timestamp": "2026-10-08 00:00:00", "source_page": "393-426",
         "also_covers": "FY2021 President's Budget",
         "notes": "H.Rept. 116-450 (H.R. 7614). sha256 98cbfd0bd79f3822... (the govinfo content PDF at the link). "
                  "Page citations are PDF page numbers; the Title II comparative statement (pp. 393-426) is "
                  "image-only: read from a tesseract text layer (400 dpi), re-read by vision where the OCR failed "
                  "the table's arithmetic."},
        {"document_id": "SRC-EXPL-LHHS-FY2021-SENATE", "source_agency": "Senate Committee on Appropriations",
         "url_or_identifier": "https://www.appropriations.senate.gov/imo/media/doc/LHHSRept.pdf",
         "document_type": "explanatory_statement", "congress_session": "116-2", "fiscal_year": 2021,
         "publication_date": "2020-11-10 00:00:00", "stage": "Senate Reported",
         "retrieval_timestamp": "2026-10-08 00:00:00", "source_page": "243-263",
         "also_covers": "FY2021 President's Budget",
         "notes": "Senate chair's draft explanatory statement for FY2021, released 2020-11-10 with the chair's draft "
                  "bill (LHHSFY2021.pdf), which was never introduced. sha256 6c401e044f6cb1bb... (the file at the "
                  "link). Text layer; page citations are PDF page numbers."},
    ],
    "brr": [
        {"reference_id": "BR-LHHS-FY2021-HOUSE", "stage": "House Reported", "bill_id": "H.R.7614",
         "report_id": "H.Rept.116-450",
         "bill_url": "https://www.govinfo.gov/content/pkg/BILLS-116hr7614rh/pdf/BILLS-116hr7614rh.pdf",
         "report_jes_url": "https://www.govinfo.gov/content/pkg/CRPT-116hrpt450/pdf/CRPT-116hrpt450.pdf", "draft": "FALSE"},
        {"reference_id": "BR-LHHS-FY2021-SENATE", "stage": "Senate Reported", "bill_id": "Senate chair's draft, FY2021",
         "report_id": "N/A", "bill_url": "https://www.appropriations.senate.gov/imo/media/doc/LHHSFY2021.pdf",
         "report_jes_url": "https://www.appropriations.senate.gov/imo/media/doc/LHHSRept.pdf",
         "notes": "Senate chair's draft bill and explanatory statement released 2020-11-10; never introduced, so no bill "
                  "number (Congress.gov lists none)", "draft": "TRUE"},
        {"reference_id": "BR-LHHS-FY2021-PB", "stage": "President's Budget", "bill_id": "PREX 2.8:2021/APP",
         "report_id": "N/A", "bill_url": "https://www.govinfo.gov/content/pkg/BUDGET-2021-APP/pdf/BUDGET-2021-APP.pdf",
         "report_jes_url": "N/A", "draft": "FALSE"},
        {"reference_id": "BR-LHHS-FY2021-ENACTED", "stage": "Enacted", "bill_id": "P.L.116-260", "report_id": "N/A",
         "bill_url": "https://www.govinfo.gov/content/pkg/PLAW-116publ260/pdf/PLAW-116publ260.pdf",
         "report_jes_url": "N/A", "vehicle_bill_id": "H.R. 133", "division": "H", "enactment_date": "2020-12-27",
         "funding_type": "omnibus", "draft": "FALSE"},
    ],
}


def load_extraction(pkg):
    x = json.loads((ROOT / "extractions" / f"{pkg}.title-ii.json").read_text())
    recs = collections.defaultdict(list)
    for r in x["validation_records"]:
        recs[r["observation_id"]].append(r)
    by_col = collections.defaultdict(list)
    for o in x["observations"]:
        by_col[o["column_header"]].append(dict(o, _clean=clean(o["account_name_as_written"]), _records=recs[o["observation_id"]]))
    sections = {}
    for col, rows in by_col.items():
        rows.sort(key=lambda f: f["node_id"])
        secs, cur, closing = [], [], None
        for f in rows:
            if closing and f.get("is_memo"):
                # the memo lines printed under an agency total ('Federal funds', '(Evaluation Tap Funding)')
                # belong to that agency, not the next one
                secs[-1][1].append(dict(f, _after_total=True))
                continue
            closing = None
            cur.append(f)
            ag = next((a for a, rx in AGENCY_TOTALS if re.match(rx, f["_clean"])), None)
            if ag:
                secs.append((ag, cur))
                cur, closing = [], ag
        secs.append(("TAIL", cur))
        sections[col] = secs
    return x, sections


def rows_for(sections, col, agency):
    secs = sections[col]
    if agency == "TAIL":
        return [f for _, rows in secs for f in rows]
    return [f for a, rows in secs if a == agency for f in rows]


def later_labels(exclude):
    """Every cleaned row label printed in the Labor-HHS tables of FY2023 on (the extractions on file)."""
    out = set()
    for f in glob.glob(str(ROOT / "extractions" / "*.title-ii.json")):
        name = Path(f).name.split(".")[0]
        if name in exclude or not re.search(r"117hrpt403|118|119|FY2023|FY2024|FY2026", name):
            continue
        x = json.loads(Path(f).read_text())
        if "HEALTH AND HUMAN" not in json.dumps(x["observations"][:5]).upper() and "Title II" not in name:
            pass
        out |= {clean(o["account_name_as_written"]) for o in x["observations"]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("fy", type=int)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    fy, cfg = a.fy, YEARS[a.fy]
    data = json.loads(STAGED.read_text())
    acct = {x["canonical_account_id"]: x for x in data["accounts"] if x["subcommittee"] == "LHHS"}
    comps = {c["component_id"]: c for c in data["components"] + cfg.get("components", [])}
    lhhs_obs = [o for o in data["observations"] if o["canonical_account_id"] in acct]
    have = {(o["canonical_account_id"], o["fiscal_year"], o["stage"]) for o in lhhs_obs}
    assert not any(k[1] == fy for k in have), f"FY{fy} already has Labor-HHS observations"
    for aid in DH.RULES:                                  # the derivation holds on every later year that prints it
        n, bad = DH.check_rule(data["observations"], aid)
        assert n and not bad, f"{aid}: derived-headline rule fails on {bad} (of {n} cells)"
        print(f"{aid}: the derived-headline rule holds on all {n} cells that print the headline and both lines")
    later_cols = []
    for path in sorted(glob.glob(str(ROOT / "extractions" / "*.title-ii.json"))):
        pkg = Path(path).name.split(".")[0]
        if pkg in {st[2] for st in cfg["stages"]}:
            continue
        try:
            _, secs = load_extraction(pkg)
        except (KeyError, ValueError):
            continue
        later_cols += [(pkg, col, secs) for col in secs]
    for aid, (ag, _, _) in DH.SUM_RULES.items():
        n, bad = DH.check_sum_rule([(f"{p}:{c}", rows_for(secs, c, ag)) for p, c, secs in later_cols], aid)
        assert n and not bad, f"{aid}: derived-headline sum rule fails on {bad} (of {n} printed subtotals)"
        print(f"{aid}: the derived-headline sum rule holds on all {n} printed subtotals")

    # 1. facts and their labels (printed labels, former names), with the chamber tables that printed them
    facts = collections.defaultdict(lambda: {"labels": collections.Counter(), "rank": []})
    for o in lhhs_obs:
        m = PRINTED.search(o["source_table_or_section"] or "")
        if not m:
            continue
        key = (o["canonical_account_id"], o["amount_type"], o["component"] or "")
        lab = clean(m.group(2))
        facts[key]["labels"][lab] += 1
        src = o["source_document_id"]
        for chamber, tables in (("House", HOUSE_TABLES), ("Senate", SENATE_TABLES)):
            if src in tables:
                facts[key]["rank"].append((chamber, tables.index(src), lab))
    for aid, lab in former_names(data, cfg, fy):
        if aid in acct:
            facts[(aid, "budget authority", "")]["labels"][lab] += 1

    extractions = {}

    def ext(pkg):
        if pkg not in extractions:
            extractions[pkg] = load_extraction(pkg)
        return extractions[pkg]

    def find(key, pkg, col, chamber, anywhere=True):
        x, sections = ext(pkg)
        aid = key[0]
        ag = AGENCY_KEY.get(acct[aid]["agency"], "TAIL")
        if col not in sections:
            return []
        pool = rows_for(sections, col, ag)
        hits = [f for f in pool if f["_clean"] in facts[key]["labels"]]
        if not hits and ag != "TAIL":
            # not in its agency's section: anywhere in the table -- or, for a cross-document lookup, only among the
            # lines outside every agency section (a generic label like 'Formula Grants' recurs under other agencies)
            pool = (rows_for(sections, col, "TAIL") if anywhere else
                    [f for a_, rs in sections[col] if a_ == "TAIL" for f in rs])
            hits = [f for f in pool if f["_clean"] in facts[key]["labels"]]
        if len({h["amount"] for h in hits}) > 1:
            for _, _, lab in sorted(r for r in facts[key]["rank"] if r[0] == (chamber if chamber != "N/A" else "House")):
                pick = [h for h in hits if h["_clean"] == lab]
                if pick:
                    hits = pick
                    break
        if len({h["amount"] for h in hits}) > 1:
            # one label on a program's own line and on the agency total's memo line ('Federal funds' under each
            # AHRQ program and under 'Total, AHRQ'): the agency total's
            pick = [h for h in hits if h.get("_after_total")]
            if pick and acct[aid].get("total_scope"):
                hits = pick
        if len({h["amount"] for h in hits}) > 1:
            words = [w for w in re.findall(r"[a-z]+", acct[aid]["canonical_name"].lower()) if len(w) > 4]
            pick = [h for h in hits if all(w in h["account_path"].lower() for w in words[-1:])]
            if pick:
                hits = pick
        return hits

    # 2. the stages
    ids = {}

    def next_id(prefix, rows, field, width):
        n = max(int(r[field][len(prefix):]) for r in rows if r[field].startswith(prefix) and r[field][len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    obs_ids = next_id("OBS-LHHS-", data["observations"], "observation_id", 4)
    new_obs, new_val, report = [], [], []
    by_stage = {}
    ext_by_src = {}
    explained = []                                       # (stage, total, items) of the short sums
    absences = []                                        # confirmed absences (absent_before)
    for stage, chamber, pkg, col, src in cfg["stages"]:
        x, sections = ext(pkg)
        ext_by_src[src] = x["observations"]
        found, origin = {}, {}
        for key in sorted(facts):
            hits = find(key, pkg, col, chamber)
            ov = cfg.get("override", {}).get((stage, key[0]))
            if ov and key[1:] == ("budget authority", ""):
                hits = [f for f in rows_for(sections, col, "TAIL") if f["_clean"] == ov[0]]
            fb = cfg.get("fallback", {}).get(stage)
            if fb and (not hits or any(h["amount"] is None for h in hits)):
                fx, _ = ext(fb[0])
                ext_by_src[fb[2]] = fx["observations"]
                fhits = find(key, fb[0], fb[1], "Senate")
                if len({h["amount"] for h in fhits}) == 1 and fhits[0]["amount"] is not None:
                    hits, origin[key] = fhits, fb
            if len({h["amount"] for h in hits}) == 1 and hits[0]["amount"] is not None:
                found[key] = hits[0]
                report.append([stage, *key, "found", hits[0]["amount"], hits[0]["source_page"], hits[0]["account_name_as_written"]])
            elif hits:
                report.append([stage, *key, "ambiguous", "", "", "; ".join(f"{h['account_name_as_written']} {h['amount']}" for h in hits)])
            else:
                report.append([stage, *key, "not printed", "", "", ""])
        # a view or contained line whose headline this table doesn't print: the headline is derived where the later
        # years define it exactly (derived_headlines); otherwise stop -- never a confirmed absence on a funded account
        derived = {}
        for aid in sorted({k[0] for k in found if k[2] and comps.get(k[2], {}).get("kind") in ("contained", "view")}):
            if (aid, "budget authority", "") in found:
                continue
            lines = {k[1:]: found[k]["amount"] or 0 for k in found if k[0] == aid}
            d = DH.derive(aid, lines, lambda k: clean_label(found[(aid, *k)]), lambda k: found[(aid, *k)]["source_page"])
            if d is None:
                raise SystemExit(f"{stage} {aid}: its headline isn't printed and can't be derived exactly "
                                 f"({sorted(lines)}); the owner decides (no printed total)")
            ka = (aid, *DH.RULES[aid][0])
            pages = sorted({str(found[(aid, *k)]["source_page"]) for k in DH.RULES[aid]})
            found[(aid, "budget authority", "")] = dict(found[ka], amount=d[0], source_page="-".join(pages),
                                                       account_name_as_written="", _records=[], _row=found[ka].get("_row", found[ka]))
            derived[(aid, "budget authority", "")] = d
            if ka in origin:
                origin[(aid, "budget authority", "")] = origin[ka]
            report.append([stage, aid, "budget authority", "", "derived", d[0], "-".join(pages), d[1]])
        # a headline printed only as its lines (derived_headlines.SUM_RULES): their sum
        for aid, (ag, heading, _) in DH.SUM_RULES.items():
            key = (aid, "budget authority", "")
            if aid not in acct or key in found or col not in sections:
                continue
            parts = DH.lines_under(rows_for(sections, col, ag), heading)
            if not parts:
                continue
            total = sum(f["amount"] or 0 for f in parts)
            pages = sorted({str(f["source_page"]) for f in parts})
            note = ("derived, not a printed line: the sum of the lines printed under '" + heading + "': "
                    + " + ".join(f"'{clean_label(f)}' {(f['amount'] or 0) // 1000:,} (p.{f['source_page']})" for f in parts)
                    + f" = {total // 1000:,} (thousands); the later tables print '{heading.split(' and ')[0]}' subtotals "
                    "as exactly this sum (a Prevention and Public Health Fund transfer is a memo, not in it)")
            arith = ("headline = the sum of the lines printed under the heading: "
                     + " + ".join(f"{(f['amount'] or 0) // 1000:,}" for f in parts) + f" = {total // 1000:,} (thousands)",
                     f"{total // 1000:,} as recorded")
            found[key] = dict(parts[0], amount=total, source_page="-".join(pages), account_name_as_written="", _records=[],
                              _parts=parts, _row=parts[0])
            derived[key] = (total, note, arith)
            report.append([stage, aid, "budget authority", "", "derived", total, "-".join(pages), note])
        # NIH Office of the Director where no OD subtotal is printed: the OD line + Gabriella Miller Kids First, the
        # later tables' 'Subtotal, Office of the Director' (owner, 2026-10-08); Kids First also a contained line
        key = (NIH_OD, "budget authority", "")
        if key in found and found[key]["_clean"] == "office of the director" and col in sections:
            kf = [f for f in rows_for(sections, col, "NIH") if re.fullmatch(r"gabriella miller kids first research act.*",
                                                                             f["_clean"]) and not f.get("is_memo")]
            if len(kf) == 1 and kf[0]["amount"]:
                od, kf = found[key], kf[0]
                total = (od["amount"] or 0) + kf["amount"]
                note = (f"derived, not a printed line: 'Office of the Director' {(od['amount'] or 0) // 1000:,} "
                        f"(p.{od['source_page']}) + '{clean_label(kf)}' {kf['amount'] // 1000:,} (p.{kf['source_page']}) = "
                        f"{total // 1000:,} (thousands); the FY2023-FY2026 tables print 'Subtotal, Office of the "
                        "Director' as the lines under the heading")
                arith = (f"headline = 'Office of the Director' + Gabriella Miller Kids First: {(od['amount'] or 0) // 1000:,} + "
                         f"{kf['amount'] // 1000:,} = {total // 1000:,} (thousands)", f"{total // 1000:,} as recorded")
                found[key] = dict(od, amount=total, account_name_as_written="", _records=[], _row=od)
                derived[key] = (total, note, arith)
                found[(NIH_OD, "budget authority", "kids_first")] = kf
                report.append([stage, *key, "derived", total, od["source_page"], note])
        for pp in cfg.get("proposal_parts", []):
            hits = [f for f in rows_for(sections, col, pp["agency"]) if re.fullmatch(pp["label"], f["_clean"])
                    and not f.get("is_memo") and f["amount"]] if col in sections else []
            if len(hits) == 1:
                found[(pp["account"], "budget authority", pp.get("component", "chamber_proposal"))] = dict(
                    hits[0], _note=pp["note"], _row=hits[0])
                report.append([stage, pp["account"], "budget authority", pp.get("component", "chamber_proposal"), "found",
                               hits[0]["amount"],
                               hits[0]["source_page"], hits[0]["account_name_as_written"]])
        # a proposal line (a chamber's or the request's one-off line) printed at zero -- another year's column of the
        # report that proposed it -- is not a figure
        for key in [k for k in found if k[2] in ("chamber_proposal", "request_proposal") and not (found[k]["amount"] or 0)]:
            found.pop(key)
        # an account a printed dash/zero shows did not exist yet (absent_before): a confirmed absence, not a figure
        for aid, first in cfg.get("absent_before", {}).items():
            key = (aid, "budget authority", "")
            if fy < first and key in found and not (found[key]["amount"] or 0):
                f = found.pop(key)
                f_src, f_col = (origin[key][2], origin[key][1]) if key in origin else (src, col)
                absences.append({"canonical_account_id": aid, "fiscal_year": fy, "stage": stage,
                                 "amount_type": "budget authority", "component": "", "source_document_id": f_src,
                                 "evidence": (f"{doc_label(f_src).rstrip(chr(39) + 's')} p.{f['source_page']} prints "
                                              f"'{clean_label(f)}' with no amount ('{(f.get('amount_as_printed') or '-').strip()}') "
                                              f"in the {f_col} column: the account did not exist in FY{fy} (first funded "
                                              f"FY{first})"),
                                 "confirmed_date": TODAY})
                report.append([stage, *key, "confirmed absence", 0, f["source_page"], f["account_name_as_written"]])
        by_key = {}
        for key in sorted(found, key=lambda k: (k[0], k[2] != "", k[1] != "budget authority", k[1], k[2])):
            aid, amount_type, component = key
            f = found[key]
            f_src, f_col = (origin[key][2], origin[key][1]) if key in origin else (src, col)
            oid = next(obs_ids)
            o = {"observation_id": oid, "canonical_account_id": aid, "fiscal_year": fy, "stage": stage, "chamber": chamber,
                 "amount": f["amount"] or 0, "amount_type": amount_type, "component": component, "headline_observation_id": "",
                 "offsetting_collections": "FALSE", "transfer_link_account_id": "", "source_document_id": f_src,
                 "source_page": str(f["source_page"]),
                 "source_table_or_section": (f"Title II, {acct[aid]['agency']} -- {derived[key][1]}" if key in derived else
                                             f"Title II, {acct[aid]['agency']} -- printed as {f['account_name_as_written'].strip()!r} [{f_col}]"
                                             + (f" -- {cfg['override'][(stage, aid)][1]}" if (stage, aid) in cfg.get("override", {}) and key[1:] == ("budget authority", "") else "")
                                             + (f" -- {src}'s column has no readable row for it" if key in origin else "")
                                             + (advance_note(fy, f["account_name_as_written"], f_col) if amount_type == "advance" else "")
                                             + (f" -- Note: {f['_note']}" if f.get("_note") else "")),
                 "extraction_method": "derived" if key in derived else (f.get("extraction_method") or "AI-extracted"),
                 "confidence": f.get("extraction_confidence") or 0.95, "verification_status": ""}
            by_key[key] = o
            new_obs.append(o)
            if key in derived:
                new_val.append({"observation_id": oid, "rule_applied": "structural", "expected_result": derived[key][2][0],
                                "observed_result": derived[key][2][1], "result": "pass"})
            for r in f["_records"]:
                if r["rule_applied"] in ("table_total", "structural", "source_text", "unit", "semantic"):
                    new_val.append({"observation_id": oid, "rule_applied": r["rule_applied"],
                                    "expected_result": r["expected_result"], "observed_result": r["observed_result"],
                                    "result": r["result"]})
        for key, o in by_key.items():
            aid, amount_type, component = key
            if component and comps.get(component, {}).get("kind") in ("contained", "view"):
                head = by_key.get((aid, "budget authority", ""))
                if head:
                    o["headline_observation_id"] = head["observation_id"]
        by_stage[stage] = (by_key, found)
        other_law_notes(stage, col, sections, by_key, found, src, new_val, cfg)
        # sum checks
        sum_checks(acct, by_key, found, new_val, sections=sections, col=col, cfg=cfg, explained=explained, stage=stage,
                   comps=comps)

    # 3. cross-document
    cross = []
    for stage, pkg, col, src in cfg["cross"]:
        by_key, _ = by_stage[stage]
        for key, o in sorted(by_key.items()):
            if o["source_document_id"] == src:
                cross.append([stage, *key, o["observation_id"], o["amount"], "", "", "same document"])
                continue
            hits = find(key, pkg, col, "Senate" if "Senate" in pkg or "SRPT" in pkg.upper() else "House", anywhere=False)
            if o["extraction_method"] == "derived" and key[0] in DH.SUM_RULES and not key[2]:
                # the other document's lines under the same heading, the same derivation
                ag_, heading, _ = DH.SUM_RULES[key[0]]
                _, osecs = ext(pkg)
                parts = DH.lines_under(rows_for(osecs, col, ag_), heading) if col in osecs else []
                hits = [dict(parts[0], amount=sum(f["amount"] or 0 for f in parts),
                             account_name_as_written=f"the lines under '{heading}'")] if parts else []
            elif o["extraction_method"] == "derived":
                if key != (NIH_OD, "budget authority", ""):
                    cross.append([stage, *key, o["observation_id"], o["amount"], "", "", "derived"])
                    continue
                # the other document's OD line + its Kids First line, the same derivation
                _, osecs = ext(pkg)
                kf = [f for f in rows_for(osecs, col, "NIH") if re.fullmatch(r"gabriella miller kids first research act.*",
                                                                             f["_clean"]) and not f.get("is_memo")]
                hits = [dict(h, amount=(h["amount"] or 0) + kf[0]["amount"],
                             account_name_as_written=f"{h['account_name_as_written'].strip()}' + '{clean_label(kf[0])}")
                        for h in hits if h["_clean"] == "office of the director"] if len(kf) == 1 else []
            vals = {h["amount"] or 0 for h in hits}
            if len(vals) != 1:
                cross.append([stage, *key, o["observation_id"], o["amount"], "", "", "not printed" if not hits else "ambiguous"])
                continue
            h = hits[0]
            ok = (h["amount"] or 0) == o["amount"]
            cross.append([stage, *key, o["observation_id"], o["amount"], h["amount"] or 0, h["source_page"], "agree" if ok else "differ"])
            rb = cfg.get("request_baseline") or {}
            base = (not ok and rb.get("stage") == stage and rb.get("other") == src
                    and o["amount"] - (h["amount"] or 0) == rb.get("difference"))
            new_val.append({"observation_id": o["observation_id"], "rule_applied": "cross_document",
                            "expected_result": f"{src} p.{h['source_page']} prints {(h['amount'] or 0) // 1000:,} "
                                               f"({h.get('extraction_method') or 'extracted'}, '{h['account_name_as_written'].strip()}' [{col}])",
                            "observed_result": f"{o['amount'] // 1000:,} as recorded", "result": "pass" if ok else "flag"}
                           | ({"_resolve": f"{rb['reason']} Note: {rb['note']}"} if base else {}))

    # rule 5 (owner, 2026-10-08), where the request columns differ: the House report's figure is kept; a difference
    # is resolved when the House figure is the budget appendix's own request for the account (its appropriation
    # language prints "[$<prior>] $<request>"), or, for a total, when its whole difference is such verified accounts'
    ra = cfg.get("request_appendix")
    if ra:
        flat = re.sub(r"\s+", " ", subprocess.run(["pdftotext", str(ROOT / ra["appendix"]), "-"],
                                                  capture_output=True, text=True).stdout)
        diffs = {}
        for v in new_val:
            if v["rule_applied"] != "cross_document" or v["result"] != "flag" or v.get("_resolve"):
                continue
            o = next(x for x in new_obs if x["observation_id"] == v["observation_id"])
            if o["stage"] != ra["stage"] or ra["other"] not in v["expected_result"]:
                continue
            other = int(re.search(r"prints ([\d,]+)", v["expected_result"]).group(1).replace(",", "")) * 1000
            diffs[(o["canonical_account_id"], o["component"] or "")] = (v, o, other)
        ok = {}
        for (aid, comp), (v, o, other) in diffs.items():
            if acct[aid].get("total_scope") or comp:
                continue
            # the account's own appropriation language: its name, then '[$<prior>] $<request>' within a few lines
            # (a bare amount elsewhere in the appendix proves nothing)
            printed = PRINTED.search(o["source_table_or_section"])
            label = printed.group(2) if printed else DH.SUM_RULES.get(aid, (None, ""))[1]
            name = re.sub(r"^(national (institute|center) (of|on|for) |office of the )", "",
                          re.sub(r"\s*\([^)]*\)", "", label).lower()).strip()
            if not name:
                continue
            amt_re = r"\[\$[\d,]+\] \$" + re.escape(f"{o['amount']:,}") + r"\b(?!,\d)"
            if any(re.search(amt_re, flat[m.end():m.end() + 400]) for m in re.finditer(re.escape(name), flat, re.I)):
                ok[aid] = o["amount"] - other
                v["_resolve"] = (f"The House report's request column prints {o['amount'] // 1000:,}, the budget appendix's "
                                 f"own request for this account (its appropriation language for '{name}': ${o['amount']:,}); the Senate "
                                 f"draft's budget estimate prints {other // 1000:,}, another baseline. Kept the House "
                                 f"report's request column (owner's rule 5, 2026-10-08). Note: The Senate draft prints this "
                                 f"request as {other // 1000:,} thousand; the budget appendix requests "
                                 f"${o['amount']:,}, as recorded.")
        for (aid, comp), (v, o, other) in diffs.items():
            scope = acct[aid].get("total_scope")
            if not scope:
                continue
            members = [(m, d) for (m, c), (_, mo, mother) in diffs.items() if not c and not acct[m].get("total_scope")
                       and (scope == "title" or acct[m]["agency"] == acct[aid]["agency"])
                       for d in [mo["amount"] - mother]]
            if members and all(m in ok for m, _ in members) and sum(d for _, d in members) == o["amount"] - other:
                v["_resolve"] = ("The difference is exactly the differences of its accounts, each the budget appendix's "
                                 "own request in the House report's column: " + ", ".join(
                                     f"{m} {d // 1000:+,}" for m, d in members) + ". Kept the House report's request "
                                 "column (owner's rule 5, 2026-10-08). Note: The Senate draft prints this request "
                                 f"{(other - o['amount']) // 1000:,} thousand higher, from accounts whose budget appendix "
                                 "requests are the House report's.")

    # 4. IDs; review status: routine notes resolved by the triage's standard reasons; the rest pending
    val_ids = next_id("VAL-LHHS-", data["validations"], "validation_id", 5)
    for v in new_val:
        v["validation_id"] = next(val_ids)
    by_obs = collections.defaultdict(list)
    for v in new_val:
        by_obs[v["observation_id"]].append(v)
    obs_by_id = {o["observation_id"]: o for o in new_obs}
    for v in new_val:
        v.update({"human_review_status": "", "reviewer": "", "resolution": ""})
        if v["result"] not in ("fail", "flag"):
            continue
        o = obs_by_id[v["observation_id"]]
        others = T.confirming_passes(by_obs[v["observation_id"]], v)
        try:
            fam, fmt = T.family(v, o, others)
        except ValueError:
            fam, fmt = None, {}
        if fam is None and (v["expected_result"] or "").startswith("memo breakdown"):
            fam, fmt = T.memo_family(v, o, ext_by_src)
        disp, reason = T.FAMILIES.get(fam, ("B", ""))
        if (v["rule_applied"] == "table_total" and "children found by the printed total" in (v["observed_result"] or "")
                and any(w is not v and w["rule_applied"] == "table_total" and w["result"] == "pass"
                        and w["expected_result"].startswith(("the ", "the NIH")) for w in by_obs[v["observation_id"]])):
            # the parser placed this total's lines by fitting the printed total; our own account sum confirms it
            v.update({"human_review_status": "resolved", "reviewer": TRIAGE_REVIEWER,
                      "resolution": "the parser placed this total's lines by the printed total; the account sum check "
                                    "on the same figure (our accounts) matches it"})
            continue
        if v["rule_applied"] == "structural" and "by model-read indent" in (v["expected_result"] or "") \
                and o["extraction_method"] != "derived" and o["component"] in ("", "kids_first", "chamber_proposal"):
            # the vision read nested a line under this one by its indent; the parent's figure is its own printed line
            # and the nested line is recorded on its own (Kids First, a proposal) or is not a figure of this account
            v.update({"human_review_status": "resolved", "reviewer": TRIAGE_REVIEWER,
                      "resolution": "parse note: a line nested by indent; this figure is the printed line itself, and "
                                    "the nested line is recorded on its own where it belongs to the account"})
            continue
        m = re.match(r"memo breakdown \[([^\]]+)\] sums to", v["expected_result"] or "")
        if v["rule_applied"] == "structural" and m and re.search(r"eval|transfer|non-add|separated families", m.group(1), re.I):
            # a memo line printed under the account -- an evaluation-tap transfer, a non-add amount -- is not a
            # breakdown of the figure; the agency sums (our accounts) confirm the figure without it
            v.update({"human_review_status": "resolved", "reviewer": TRIAGE_REVIEWER,
                      "resolution": f"parse note: '{m.group(1)}' is a memo line printed under the account (a transfer "
                                    "or non-add amount), not a breakdown of its figure"})
            continue
        if v.get("_resolve"):                            # the owner's rules 3 and 5 (2026-10-08)
            v.update({"human_review_status": "resolved", "reviewer": OWNER_RULE, "resolution": v["_resolve"]})
        elif disp == "A":
            v.update({"human_review_status": "resolved", "reviewer": TRIAGE_REVIEWER, "resolution": reason.format(**fmt)})
        else:
            v["human_review_status"] = "pending"
    # 5. statuses (the standard rule) and IDs
    checks = collections.defaultdict(list)
    for v in new_val:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                            v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    order = ["validation_id", "observation_id", "rule_applied", "expected_result", "observed_result", "result",
             "human_review_status", "reviewer", "resolution"]
    new_val = [{k: v[k] for k in order} for v in new_val]

    # 6. unmatched headings: rows of this year's tables whose label no account's labels match and no later
    #    table prints (a heading that existed then but not now, a rename, a split or a merge)
    later = later_labels({s[2] for s in cfg["stages"]})
    all_labels = {lab for f in facts.values() for lab in f["labels"]}
    unmatched = collections.OrderedDict()
    for stage, chamber, pkg, col, src in cfg["stages"]:
        x, sections = ext(pkg)
        tot_of = {AGENCY_KEY.get(x["agency"]): aid for aid, x in acct.items() if x.get("total_scope") == "agency"}
        for ag, rows in sections.get(col, []):
            for f in rows:
                if f.get("is_memo") or f["_clean"] in all_labels or f["_clean"] in later:
                    continue
                f = dict(f, _agency_total=tot_of.get(ag))
                k = (ag, f["account_name_as_written"].strip())
                if k not in unmatched:
                    unmatched[k] = {"agency_section": ag, "heading": k[1], "path": f["account_path"], "figures": [],
                                    "proposal": propose(f, facts, cfg)}
                unmatched[k]["figures"].append(f"{stage} {(f['amount'] or 0) // 1000:,} (p.{f['source_page']})")

    # a line whose money is outside our accounts (a short sum's 'outside' item) is an unmatched heading, whatever
    # later tables print
    for st, tot_id, short, items, ok in explained:
        for a_, kind, why in items:
            if kind != "outside":
                continue
            head, rest = why.split(" (p.", 1)
            k = (AGENCY_KEY.get(acct[tot_id]["agency"]), head.strip("'"))
            u = unmatched.setdefault(k, {"agency_section": k[0], "heading": k[1], "path": "", "figures": [], "proposal": ""})
            u["proposal"] = rest.split("): ", 1)[1]
            fig = f"{st} {a_ // 1000:,} (p.{rest.split(')', 1)[0]})"
            if fig not in u["figures"]:
                u["figures"].append(fig)

    # ---- report
    stage_counts = collections.Counter(o["stage"] for o in new_obs)
    print(f"FY{fy}: {len(new_obs)} observations {dict(stage_counts)}; {len(new_val)} validations "
          f"({sum(v['human_review_status'] == 'pending' for v in new_val)} pending, "
          f"{sum(v['human_review_status'] == 'resolved' for v in new_val)} resolved at creation)")
    print("statuses:", collections.Counter(o["verification_status"] for o in new_obs))
    for st in by_stage:
        accts_found = {k[0] for k in by_stage[st][0]}
        print(f"  {st}: {len(by_stage[st][0])} facts, {len(accts_found)} accounts; no figure for "
              f"{len(set(acct) - accts_found)}")
    for stage in {c[0] for c in cfg["cross"]}:
        rs = [r for r in cross if r[0] == stage]
        print(f"  cross-document {stage}: {sum(r[-1] == 'agree' for r in rs)} agree / {sum(r[-1] == 'differ' for r in rs)} "
              f"differ / {sum(r[-1] in ('not printed', 'ambiguous') for r in rs)} not compared, of {len(rs)}")
    print(f"  unmatched headings: {len(unmatched)}")
    out = HERE / f"fy{fy}_report.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["stage", "canonical_account_id", "amount_type", "component", "result", "amount", "pdf_page", "printed"])
        w.writerows(report)
    with open(HERE / f"fy{fy}_cross_document.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["stage", "canonical_account_id", "amount_type", "component", "our_observation_id", "our_amount",
                    "other_amount", "other_pdf_page", "result"])
        w.writerows(cross)
    with open(ROOT / "reference" / "review" / f"backfill_unmatched_{fy}.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["agency_section", "heading", "printed_path", "figures", "proposed_handling"])
        for u in unmatched.values():
            w.writerow([u["agency_section"], u["heading"], u["path"], "; ".join(u["figures"]), u["proposal"]])
    if a.write:
        for d in cfg["docs"]:
            assert d["document_id"] not in {x["document_id"] for x in data["source_docs"]}
            data["source_docs"].append(d)
        for did, cov in cfg.get("also_covers", {}).items():
            d = next(x for x in data["source_docs"] if x["document_id"] == did)
            if cov not in (d.get("also_covers") or ""):
                d["also_covers"] = "; ".join(x for x in (d.get("also_covers"), cov) if x)
        for r in cfg["brr"]:
            row = {"reference_id": r["reference_id"], "subcommittee": "LHHS", "fiscal_year": fy, "stage": r["stage"],
                   "bill_id": r["bill_id"], "report_id": r["report_id"], "bill_url": r["bill_url"],
                   "report_jes_url": r["report_jes_url"], "lookup_key": f"LHHS-{fy}-{r['stage']}", "notes": r.get("notes", ""),
                   "vehicle_bill_id": r.get("vehicle_bill_id", ""), "division": r.get("division", ""),
                   "enactment_date": r.get("enactment_date", ""), "funding_type": r.get("funding_type", ""),
                   "draft": r["draft"]}
            data["bill_report_refs"].append(row)
        data["components"] += [c for c in cfg.get("components", [])
                               if c["component_id"] not in {x["component_id"] for x in data["components"]}]
        hn_ids = next_id("HN-LHHS-", data["historical_names_tab"], "historical_name_id", 4)
        for h in cfg.get("historical_names", []):
            if any(x["canonical_account_id"] == h["canonical_account_id"] and x["former_name"] == h["former_name"]
                   for x in data["historical_names_tab"]):
                continue
            data["historical_names_tab"].append({"historical_name_id": next(hn_ids), "canonical_account_id": h["canonical_account_id"],
                                                 "former_name": h["former_name"], "evidence": h["evidence"],
                                                 "approved_date": TODAY, "confidence": 1.0, "human_reviewed": "TRUE"})
        ca_ids = next_id("CA-LHHS-", data["confirmed_absences"], "confirmed_absence_id", 4)
        for c in absences:
            data["confirmed_absences"].append({"confirmed_absence_id": next(ca_ids), **c})
        data["observations"].extend(new_obs)
        data["validations"].extend(new_val)
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("appended to", STAGED.relative_to(ROOT))
    return {"obs": new_obs, "val": new_val, "cross": cross, "unmatched": unmatched}


def former_names(data, cfg, fy):
    """(account, cleaned label) of every former name in use in fiscal year fy. A name valid only for some years
    says so in its evidence ('Valid for FY2022 and earlier': HRSA's 'Program Management', the account's headline
    through FY2022 and a line inside it from FY2023) -- mapped by year, not by name."""
    out = []
    for h in data.get("historical_names_tab", []) + cfg.get("historical_names", []):
        if not h.get("former_name"):
            continue
        m = re.search(r"Valid for FY(\d{4}) and earlier", h.get("evidence") or "")
        if m and fy > int(m.group(1)):
            continue
        out += [(h["canonical_account_id"], clean(lab)) for lab in [h["former_name"]] + h.get("printed", [])]
    return out


def clean_label(f):
    return " ".join(f["account_name_as_written"].split()).rstrip(" .")


def doc_label(src):
    """'SRC-CRPT-117HRPT96' -> 'H.Rept.117-96'; an explanatory statement by its chamber."""
    m = re.match(r"SRC-CRPT-(\d+)([HS])RPT(\d+)$", src)
    if m:
        return f"{m.group(2)}.Rept.{m.group(1)}-{m.group(3)}"
    return "the Senate draft explanatory statement's" if "SENATE" in src else src


def advance_note(fy, printed, col):
    """An advance line's note: the fiscal year it is for; and, where the row is labelled after a later bill (the
    next year's report's prior-year column), what the figure in this column is."""
    m = re.search(r"FY\s*(\d{4})", printed)
    if not m or int(m.group(1)) == fy + 1:
        return f"; advance for FY{fy + 1}"
    return (f"; advance for FY{fy + 1} (the row is labelled 'FY {m.group(1)}' after the report's bill column; in the "
            f"{col} column the figure is FY{fy}'s advance for the 1st quarter of FY{fy + 1})")


OTHER_LAW = re.compile(r"CR Funding|Public Law|\bP\.\s?L\.?\s?\d{3}", re.I)


def propose(f, found, cfg):
    """A first proposal for a heading no account matches; the owner decides."""
    lab = f["account_name_as_written"].strip()
    own = cfg.get("proposals", {}).get(clean(lab))
    if own:
        return own
    if OTHER_LAW.search(lab):
        return "not an account: another law's amount printed within its parent's figure (a corner note where our figure includes it)"
    if re.search(r"advance|1st quarter", lab, re.I):
        return "not an account: an advance-appropriation line of the account above it (amount_type advance)"
    bare = lambda s: re.sub(r"^(sub)?total,?\s*|,? (program level|appropriation|budget authority).*$", "", s)
    segs = [bare(clean(s)) for s in f["account_path"].split(" / ")[:-1]]
    for key, g in sorted(found.items(), key=lambda kv: kv[0]):
        if key[1:] != ("budget authority", ""):
            continue
        for lab in g["labels"]:
            if bare(lab) and bare(lab) in segs:
                return f"not an account: a program line inside {key[0]} (printed under '{segs[segs.index(bare(lab))]}')"
    if len(segs) >= 2 and f.get("_agency_total"):
        return f"not an account: a program line inside {f['_agency_total']} (no narrower account of ours prints its group)"
    return "owner: no current account prints this heading -- a new account + Historical Name, or a relationship"


def other_law_notes(stage, col, sections, by_key, found, src, new_val, cfg):
    """A figure that prints another law's amount within it (a 'CR Funding - P.L. 117-70' line under a subtotal the
    figure is): an info record whose 'Includes $...' sentence the cell shows as its corner note."""
    rows = [f for _, rs in sections.get(col, []) for f in rs]
    head_rows = {id(f): key for key, f in found.items() if key[1:] == ("budget authority", "")}
    pending, block = [], []
    for f in rows:
        if f["row_kind"] == "line" and not f.get("is_memo"):
            block.append(f)
        if OTHER_LAW.search(f["account_name_as_written"]) and f["amount"]:
            pending.append(f)
            continue
        key = head_rows.get(id(f))
        if key is None:
            continue
        # only where the figure is its printed lines with the other law's amounts among them
        included = sum((g["amount"] or 0) for g in block if g is not f) == (f["amount"] or 0)
        if pending and included and f["row_kind"] in ("subtotal", "total"):
            parts = " and ".join(f"${p['amount']:,} ('{p['account_name_as_written'].strip()}')" for p in pending)
            o = by_key[key]
            new_val.append({"observation_id": o["observation_id"], "rule_applied": "law_text",
                            "expected_result": f"{src} p.{f['source_page']} prints amounts from other laws within this figure "
                                               f"['{f['account_name_as_written'].strip()}', {col}]",
                            "observed_result": f"{o['amount']:,} as recorded. Includes {parts}, continuing-resolution "
                                               f"funding printed within this figure ({src} p.{f['source_page']}).",
                            "result": "info"})
        pending, block = [], []


def sum_checks(acct, by_key, found, new_val, sections=None, col=None, cfg=None, explained=None, stage=None, comps=None):
    """The agency totals = their member accounts; the Title II total = agency totals + department-wide lines - CURES
    (as build_rows.py). A short sum is explained, line by line, by the printed lines that make up the difference
    (explain_shortfall): resolved when they add up to it exactly and each one's money is inside an existing account
    (or is another law's line); otherwise it stays pending, naming them."""
    cfg, comps = cfg or {}, comps or {}

    def amt(aid, component=""):
        o = by_key.get((aid, "budget authority", component))
        return o["amount"] if o else None
    printed = {k[0] for k in found}
    members = collections.defaultdict(list)
    for aid, x_ in acct.items():
        if x_.get("total_scope") or x_.get("parent_account_id"):
            continue
        members[(x_["agency"], x_.get("title"))].append(aid)

    def add(tot_id, parts, not_printed, what):
        got, want = sum(v for _, v in parts), amt(tot_id)
        exp = (f"{what} = {got // 1000:,} (thousands): " + " + ".join(f"{p} {v // 1000:,}" for p, v in parts)
               + (f"; not printed in this table, so not in the sum: {', '.join(not_printed)}" if not_printed else ""))
        new_val.append({"observation_id": by_key[(tot_id, "budget authority", "")]["observation_id"], "rule_applied": "table_total",
                        "expected_result": exp,
                        "observed_result": f"{want // 1000:,} as printed ('{found[(tot_id, 'budget authority', '')]['account_name_as_written'].strip()}')",
                        "result": "pass" if got == want else "flag"})
        return want - got

    def explain(tot_id, short, items):
        """Write the explanation onto the last record: resolved (_resolve) or pending, naming the lines."""
        v = new_val[-1]
        got = sum(i[0] for i in items)
        text = "; ".join(f"{'+' if a >= 0 else '-'}{abs(a) // 1000:,} {why}" for a, kind, why in items)
        outside = [why for a, kind, why in items if kind == "outside"]
        if items and got == short and not outside:
            v["_resolve"] = (f"The shortfall {short // 1000:,} (thousands) is exactly: {text}. Every other line of the "
                             "section is inside an account: with these the sum equals the printed total.")
        else:
            v["expected_result"] += (f"; shortfall {short // 1000:,} (thousands)"
                                     + (f" = {text}" if items and got == short else
                                        f"; explained {got // 1000:,}: {text or 'none'}; unexplained {(short - got) // 1000:,}")
                                     + ("; outside our accounts -- the owner decides" if outside else ""))
        if explained is not None:
            explained.append((stage, tot_id, short, items, got == short and not outside))

    def section_items(tot_id, x_, mem):
        """The printed lines of the agency's section that make up a short sum."""
        rows = rows_for(sections, col, AGENCY_KEY.get(x_["agency"], "TAIL")) if sections and col in sections else []
        nodes = {id(f) for f in rows}
        everywhere = {id(f) for f in rows_for(sections, col, "TAIL")} if sections and col in sections else set()
        row_of = lambda f: id(f.get("_row", f))
        items = []
        for m in mem:                                     # a member printed in another agency's section
            f = found.get((m, "budget authority", ""))
            # (a member read from this table but outside this section -- unless lines of its name in this section
            # add up to it: the CURES Act lines printed under each NIH institute)
            own = clean(acct[m]["canonical_name"])
            if f is not None and row_of(f) in everywhere and row_of(f) not in nodes and amt(m) is not None \
                    and sum(r["amount"] or 0 for r in rows if r["_clean"].startswith(own) and not r.get("is_memo")) != amt(m):
                items.append((-amt(m), "inside", f"{m} (an existing account printed in another agency's section of this "
                                                  f"table, p.{f['source_page']}, and counted in that agency's total)"))
        for key, f in found.items():                      # another agency's account printed in this section
            a_ = acct.get(key[0], {})
            if key[1] == "budget authority" and key[2] in ("", "chamber_proposal") and row_of(f) in nodes \
                    and (not a_.get("total_scope") or key[2] == "chamber_proposal") \
                    and a_.get("agency") != x_["agency"]:
                items.append((by_key[key]["amount"], "inside", f"{key[0]}{' ' + key[2] + ' line' if key[2] else ''} (an existing account of {a_['agency']} "
                                                              f"printed in this section, p.{f['source_page']})"))
        for key, f in found.items():                      # a line recorded inside the total itself (a request proposal)
            if key[0] == tot_id and key[2] and comps.get(key[2], {}).get("kind") == "contained" and row_of(f) in nodes \
                    and key[2] not in [c for c in comps if comps[c]["kind"] == "view"]:
                items.append((f["amount"] or 0, "inside", f"'{clean_label(f)}' (p.{f['source_page']}): recorded as a "
                                                       f"{key[2]} line inside {tot_id}"))
        for it in cfg.get("sum_items", []):
            if it["total"] != tot_id:
                continue
            for f in rows:
                if f.get("is_memo") or not re.fullmatch(it["label"], f["_clean"]) or it.get("path", "") not in f["account_path"] \
                        or not f["amount"]:
                    continue
                why = it.get("why") or it["proposal"]
                items.append((f["amount"] or 0, it["kind"], f"'{clean_label(f)}' (p.{f['source_page']}): {why}"))
        return items

    for tot_id, x_ in acct.items():
        if x_.get("total_scope") != "agency" or (tot_id, "budget authority", "") not in by_key:
            continue
        mem = members[(x_["agency"], x_.get("title"))]
        if not mem:
            continue
        vals, lack = [], []
        for m in mem:
            v = amt(m, "appropriated_in_this_bill") if amt(m, "appropriated_in_this_bill") is not None else amt(m)
            if v is None:
                lack.append(m)
            else:
                vals.append((m, v))
        what = (f"the NIH institutes and other NIH accounts ({len(vals)})" if x_["agency"] == "National Institutes of Health"
                else f"the {len(vals)} {x_['agency']} accounts")
        short = add(tot_id, vals, [m for m in lack if m not in printed], what)
        if any(m in printed for m in lack):
            new_val[-1]["result"] = "flag"
        if new_val[-1]["result"] == "flag":
            explain(tot_id, short, section_items(tot_id, x_, mem))
    title_id = next((aid for aid, x_ in acct.items() if x_.get("total_scope") == "title"), None)
    if title_id and (title_id, "budget authority", "") in by_key:
        parts = [(aid, amt(aid)) for aid, x_ in acct.items() if x_.get("total_scope") == "agency" and amt(aid) is not None]
        for aid, x_ in acct.items():
            if x_["agency"] == "Department of Health and Human Services" and not x_.get("total_scope"):
                for (k_aid, k_type, k_comp), o in by_key.items():
                    if k_aid == aid and not k_comp:
                        parts.append((aid, o["amount"]))
        cures = amt("ACC-HHS-NIH-CURES")
        if cures:
            parts.append(("minus ACC-HHS-NIH-CURES", -cures))
        short = add(title_id, parts, [], "the agency totals + the department-wide lines - the CURES Act line")
        if new_val[-1]["result"] == "flag":
            items = ([(cures, "inside", "ACC-HHS-NIH-CURES: this year's Title II total counts the CURES Act lines inside "
                                       "the NIH total (the table prints no line taking them out)")]
                     if cures and cfg.get("title_counts_cures") else [])
            explain(title_id, short, items)

if __name__ == "__main__":
    main()
