import json
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

with open("data/staged.json") as f:
    data = json.load(f)

# ---------------------------------------------------------------------------
# Allowed values. ONE definition, used by both the dropdowns below and the
# pre-build check, so the two can never drift apart (v29 had "human-entered"
# in the dropdown but "human_entered" in all 906 rows).
# ---------------------------------------------------------------------------
STAGE_ALL = ["President's Budget", "House Reported", "Senate Reported", "Enacted", "House Passed", "Senate Passed"]
AMOUNT_TYPE = ["budget authority", "obligation", "outlay", "rescission", "transfer", "offsetting_collection", "supplemental", "advance", "prior_year_advance", "other"]
EXTRACTION = ["AI-extracted", "human-entered", "hybrid", "text-extracted", "derived"]
VERIFICATION = ["unverified", "auto-validated", "human-verified", "flagged", "provisional", "superseded"]
ACCOUNT_STATUS = ["active", "inactive", "superseded", "proposed"]
FUND_TYPE = ["general", "trust", "special", "revolving", "working_capital", "no_year"]
REL_TYPE = ["same", "renamed", "split_from", "merged_into", "moved", "proposed_move", "uncertain"]
DOC_TYPE = ["bill", "committee_report", "explanatory_statement", "public_law", "presidents_budget", "budget_appendix", "congressional_budget_justification", "cbo_cost_estimate", "crs_report", "omb_public_budget_database", "other"]
RULE = ["source_text", "structural", "table_total", "cross_document", "historical", "account_identity", "unit", "semantic", "law_text"]
RESULT = ["pass", "fail", "flag", "info"]
REVIEW = ["pending", "resolved"]
CHAMBER = ["House", "Senate", "N/A"]
BOOL = ["TRUE", "FALSE"]
COMP_KIND = ["part", "contained", "view"]
STAGE_BR = ["Enacted", "Senate Reported", "House Reported", "President's Budget"]
SUBCOMMITTEES = ["CJS", "LHHS"]
TOTAL_SCOPE = ["agency", "title", "bill"]
FUNDING_TYPE = ["standalone", "minibus", "omnibus", "full_year_cr"]

def _check_enums():
    comp_ids = [c["component_id"] for c in data.get("components", [])]
    checks = [
        ("accounts", "status", ACCOUNT_STATUS), ("accounts", "fund_type", FUND_TYPE),
        ("accounts", "subcommittee", SUBCOMMITTEES), ("accounts", "total_scope", TOTAL_SCOPE),
        ("observations", "stage", STAGE_ALL), ("observations", "chamber", CHAMBER),
        ("observations", "amount_type", AMOUNT_TYPE), ("observations", "offsetting_collections", BOOL),
        ("observations", "extraction_method", EXTRACTION), ("observations", "verification_status", VERIFICATION),
        ("observations", "component", comp_ids),
        ("confirmed_absences", "stage", STAGE_ALL), ("confirmed_absences", "amount_type", AMOUNT_TYPE),
        ("confirmed_absences", "component", comp_ids),
        ("relationships", "relationship_type", REL_TYPE), ("relationships", "human_reviewed", BOOL),
        ("source_docs", "document_type", DOC_TYPE), ("source_docs", "stage", STAGE_ALL),
        ("bill_report_refs", "stage", STAGE_BR), ("bill_report_refs", "subcommittee", SUBCOMMITTEES),
        ("bill_report_refs", "funding_type", FUNDING_TYPE), ("bill_report_refs", "draft", BOOL),
        ("bill_report_refs", "not_reported", BOOL),
        ("validations", "rule_applied", RULE), ("validations", "result", RESULT),
        ("validations", "human_review_status", REVIEW),
        ("components", "kind", COMP_KIND),
    ]
    errors = []
    for table, field, allowed in checks:
        bad = {}
        for row in data.get(table, []):
            v = row.get(field, "")
            if v in ("", None):
                continue
            if v not in allowed:
                bad[v] = bad.get(v, 0) + 1
        for v, n in bad.items():
            errors.append(f"{table}.{field}: {v!r} x{n} (allowed: {allowed})")
    if errors:
        raise SystemExit("BUILD STOPPED: values outside the allowed lists\n  " + "\n  ".join(errors))

_check_enums()

def _check_integrity():
    """Referential checks the build must pass. Stops the build like _check_enums."""
    errs = []
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    for a in data["accounts"]:
        for gone in ("effective_start", "effective_end"):
            if a.get(gone) not in (None, ""):
                errs.append(f"account {a['canonical_account_id']}: {gone} is no longer a field")
        p = a.get("parent_account_id") or ""
        if p:
            if p == a["canonical_account_id"]: errs.append(f"account {p}: parent is itself")
            elif p not in acc: errs.append(f"account {a['canonical_account_id']}: parent {p} does not exist")
            elif acc[p]["subcommittee"] != a["subcommittee"]: errs.append(f"account {a['canonical_account_id']}: parent in another subcommittee")
            elif acc[p].get("total_scope"): errs.append(f"account {a['canonical_account_id']}: parent {p} is a rollup total")
    docs = {d["document_id"] for d in data["source_docs"]}
    obs = {o["observation_id"]: o for o in data["observations"]}
    comps = {c["component_id"] for c in data.get("components", [])}
    for o in data["observations"]:
        if o["canonical_account_id"] not in acc: errs.append(f"{o['observation_id']}: unknown account")
        if o["source_document_id"] not in docs: errs.append(f"{o['observation_id']}: unknown source document")
        if o.get("component") and o["component"] not in comps: errs.append(f"{o['observation_id']}: unknown component")
        h = o.get("headline_observation_id") or ""
        if h and (h not in obs or obs[h]["canonical_account_id"] != o["canonical_account_id"]):
            errs.append(f"{o['observation_id']}: headline {h} missing or on another account")
    for s in data["source_docs"]:
        if "/app/details/" in (s.get("url_or_identifier") or ""):
            errs.append(f"{s['document_id']}: govinfo landing page; link the PDF (content/pkg/...pdf or congress.gov)")
    for r in data["bill_report_refs"]:
        for f in ("report_jes_url", "bill_url"):
            if "/app/details/" in (r.get(f) or ""):
                errs.append(f"{r['reference_id']}: {f} is a govinfo landing page")
        enacted_fields = [r.get(k) or "" for k in ("vehicle_bill_id", "division", "enactment_date", "funding_type")]
        if r["stage"] != "Enacted" and any(enacted_fields):
            errs.append(f"{r['reference_id']}: enactment fields are only for Enacted rows")
        if r["stage"] == "Enacted" and r.get("funding_type") and not r.get("enactment_date"):
            errs.append(f"{r['reference_id']}: funding_type without enactment_date")
    for v in data["validations"]:
        if v["observation_id"] not in obs: errs.append(f"{v['validation_id']}: unknown observation")
    for c in data["confirmed_absences"]:
        if c["canonical_account_id"] not in acc or c["source_document_id"] not in docs:
            errs.append(f"{c['confirmed_absence_id']}: unknown account or document")
    for r in data["relationships"]:
        if r["from_account_id"] not in acc or r["to_account_id"] not in acc: errs.append(f"{r['relationship_id']}: unknown account")
    for kind in ("observations", "validations", "confirmed_absences", "accounts", "source_docs"):
        key = {"observations": "observation_id", "validations": "validation_id",
               "confirmed_absences": "confirmed_absence_id", "accounts": "canonical_account_id",
               "source_docs": "document_id"}[kind]
        seen = set()
        for row in data[kind]:
            if row[key] in seen: errs.append(f"duplicate {key} {row[key]}")
            seen.add(row[key])
    if errs:
        raise SystemExit("BUILD STOPPED: integrity\n  " + "\n  ".join(errs[:50]) + (f"\n  ... {len(errs)} total" if len(errs) > 50 else ""))

_check_integrity()

def _check_status_rule():
    """Every verification_status the rule sets is the one validate_approps.verification_status gives for
    the observation's checks (a check a person resolved, with its resolution written, no longer counts).
    Human-verified, provisional and superseded are set by their own steps. Stops the build."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import validate_approps as V
    from collections import defaultdict
    checks = defaultdict(list)
    for v in data["validations"]:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v.get("expected_result"),
                                           v.get("human_review_status"), v.get("resolution")))
    errs = []
    for v in data["validations"]:
        if v.get("human_review_status") == "resolved" and not (v.get("resolution") or "").strip():
            errs.append(f"{v['validation_id']}: resolved without a resolution")
    for o in data["observations"]:
        if o["verification_status"] in ("human-verified", "provisional", "superseded"):
            continue
        want = V.verification_status(o["confidence"], checks[o["observation_id"]])
        if o["verification_status"] != want:
            errs.append(f"{o['observation_id']}: verification_status {o['verification_status']!r}, the rule gives {want!r}")
    if errs:
        raise SystemExit("BUILD STOPPED: verification_status rule\n  " + "\n  ".join(errs[:50])
                         + (f"\n  ... {len(errs)} total" if len(errs) > 50 else ""))

_check_status_rule()

FONT_NAME = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF", size=10)
FLAG_FILL = PatternFill("solid", fgColor="FCE4E4")
BODY_FONT = Font(name=FONT_NAME, size=10)
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14)
SUBTITLE_FONT = Font(name=FONT_NAME, italic=True, size=10, color="595959")
NOTE_FONT = Font(name=FONT_NAME, size=10, color="595959")
BOLD_NOTE = Font(name=FONT_NAME, bold=True, size=11)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

wb = openpyxl.Workbook()
wb.remove(wb.active)

def style_header(ws, ncols, row=1):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 32
    ws.freeze_panes = ws.cell(row=row + 1, column=1)

def write_rows(ws, headers, rows, widths, flag_col=None, flag_value=None):
    ws.append(headers)
    style_header(ws, len(headers))
    for r_i, row in enumerate(rows, start=2):
        for c_i, h in enumerate(headers, start=1):
            v = row.get(h, "")
            cell = ws.cell(row=r_i, column=c_i, value=v)
            if isinstance(v, str) and v.startswith("="):
                cell.data_type = "s"   # data text that begins with '=' must stay text, never become a formula
            cell.font = BODY_FONT
            cell.border = BORDER
            if flag_col and row.get(flag_col) == flag_value:
                cell.fill = FLAG_FILL
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

def add_dropdown(ws, col_letter, options, first_row, last_row):
    dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True, showDropDown=False)
    ws.add_data_validation(dv)
    dv.add(f"{col_letter}{first_row}:{col_letter}{last_row}")

# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
# Account.historical_names is always DERIVED from the Historical Name tab, never
# hand-set -- this is the single source of truth; drift between the two is what
# caused v11's load failure, and this makes that category of bug impossible.
from collections import defaultdict as _defaultdict
_names_by_account = _defaultdict(list)
for _h in data["historical_names_tab"]:
    _names_by_account[_h["canonical_account_id"]].append(_h["former_name"])
for _a in data["accounts"]:
    _a["historical_names"] = "; ".join(_names_by_account.get(_a["canonical_account_id"], []))

ws = wb.create_sheet("Account")
headers = ["canonical_account_id", "canonical_name", "agency", "bureau", "treasury_account_symbol",
           "status", "historical_names", "historical_identifiers",
           "fund_type", "subcommittee", "notes", "title", "display_order", "total_scope", "parent_account_id"]
# v33: effective_start / effective_end REMOVED (every value had been derived from our first year of data,
# not a real start date, and no product feature used them; coverage is computed from observations instead).
# parent_account_id (last column): set only when an account is a program line inside another account's
# heading (e.g. Health Centers inside Primary Health Care). Accounts with a parent are excluded from
# agency-total reconciliation because their money is already inside the parent.
# total_scope (column P, appended so no existing column moves): what a rollup account's printed total
# covers -- agency / title / bill. Blank for every real funding account. Code reads THIS field for
# scope, never the free-text notes (a note mentioning "grand total" once mislabeled Title II as a bill total).
write_rows(ws, headers, data["accounts"], [22, 40, 35, 30, 18, 12, 40, 20, 16, 14, 55, 14, 14, 14, 24])
n = len(data["accounts"]) + 1
_L = lambda h: get_column_letter(headers.index(h) + 1)
add_dropdown(ws, _L("status"), ACCOUNT_STATUS, 2, n)
add_dropdown(ws, _L("fund_type"), FUND_TYPE, 2, n)
add_dropdown(ws, _L("total_scope"), TOTAL_SCOPE, 2, n)
n_acc = n
ACC_SUB_COL = _L("subcommittee")

# ---------------------------------------------------------------------------
# Historical Name (former display names, with evidence -- distinct from
# Account Relationship, which links two separate canonical accounts, not a
# name history on one account)
# ---------------------------------------------------------------------------
ws = wb.create_sheet("Historical Name")
headers = ["historical_name_id", "canonical_account_id", "former_name", "evidence",
           "approved_date", "confidence", "human_reviewed"]
write_rows(ws, headers, data["historical_names_tab"], [16, 22, 32, 70, 14, 12, 14])
n_hn = max(len(data["historical_names_tab"]) + 1, 2)
add_dropdown(ws, "G", BOOL, 2, n_hn)
for row in ws.iter_rows(min_row=2, max_row=n_hn, min_col=4, max_col=4):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")

# ---------------------------------------------------------------------------
# Confirmed Absence -- distinguishes "confirmed not applicable" from both a
# real observation and genuine "haven't checked yet" (missing)
# ---------------------------------------------------------------------------
ws = wb.create_sheet("Confirmed Absence")
headers = ["confirmed_absence_id", "canonical_account_id", "fiscal_year", "stage", "amount_type",
           "component", "source_document_id", "evidence", "confirmed_date"]
ca_rows = data.get("confirmed_absences", [])
write_rows(ws, headers, ca_rows, [20, 22, 10, 16, 18, 22, 22, 55, 14])
n_ca = max(len(ca_rows) + 1, 2)
add_dropdown(ws, "D", STAGE_ALL, 2, max(n_ca, 200))
add_dropdown(ws, "E", AMOUNT_TYPE, 2, max(n_ca, 200))
for row in ws.iter_rows(min_row=2, max_row=n_ca, min_col=8, max_col=8):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")

# ---------------------------------------------------------------------------
# Appropriations Observation
# ---------------------------------------------------------------------------
ws_obs = wb.create_sheet("Appropriations Observation")
headers = ["observation_id", "canonical_account_id", "fiscal_year", "stage", "chamber", "bill_id", "report_id",
           "amount", "amount_type", "component", "offsetting_collections", "transfer_link_account_id",
           "source_document_id", "source_page", "source_table_or_section", "extraction_method", "confidence",
           "verification_status", "bill_url", "report_jes_url", "headline_observation_id", "superseded_by_observation_id"]
write_rows(ws_obs, headers, data["observations"],
           [14, 22, 10, 16, 10, 14, 14, 16, 18, 16, 12, 20, 18, 12, 42, 16, 10, 16, 40, 40, 20, 20],
           flag_col="verification_status", flag_value="flagged")
n_obs = len(data["observations"]) + 1
add_dropdown(ws_obs, "D", STAGE_ALL, 2, n_obs)
add_dropdown(ws_obs, "E", CHAMBER, 2, n_obs)
add_dropdown(ws_obs, "I", AMOUNT_TYPE, 2, n_obs)
add_dropdown(ws_obs, "K", BOOL, 2, n_obs)
add_dropdown(ws_obs, "P", EXTRACTION, 2, n_obs)
add_dropdown(ws_obs, "R", VERIFICATION, 2, n_obs)
for row in ws_obs.iter_rows(min_row=2, max_row=n_obs, min_col=8, max_col=8):
    for c in row:
        c.number_format = "#,##0"

# ---------------------------------------------------------------------------
# Account Relationship  (was missing from the original template)
# ---------------------------------------------------------------------------
ws = wb.create_sheet("Account Relationship")
headers = ["relationship_id", "from_account_id", "to_account_id", "relationship_type", "effective_fiscal_year",
           "evidence", "confidence", "human_reviewed"]
write_rows(ws, headers, data["relationships"], [16, 20, 20, 16, 16, 70, 12, 14], flag_col="human_reviewed", flag_value="FALSE")
n = max(len(data["relationships"]) + 1, 2)
add_dropdown(ws, "D", REL_TYPE, 2, n)
add_dropdown(ws, "H", BOOL, 2, n)
for row in ws.iter_rows(min_row=2, max_row=n, min_col=6, max_col=6):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")

# ---------------------------------------------------------------------------
# Source Document (deduped: one row per real document, not per year/stage cell)
# ---------------------------------------------------------------------------
ws = wb.create_sheet("Source Document")
headers = ["document_id", "source_agency", "url_or_identifier", "document_type", "congress_session",
           "fiscal_year", "publication_date", "stage", "retrieval_timestamp", "source_page", "also_covers", "notes"]
write_rows(ws, headers, data["source_docs"], [24, 32, 55, 22, 16, 12, 14, 16, 16, 12, 45, 60])
n = len(data["source_docs"]) + 1
add_dropdown(ws, "D", DOC_TYPE, 2, n)
add_dropdown(ws, "H", STAGE_ALL, 2, n)
for row in ws.iter_rows(min_row=2, max_row=n, min_col=11, max_col=12):
    for c in row:
        c.font = NOTE_FONT
        c.alignment = Alignment(wrap_text=True, vertical="top")

# ---------------------------------------------------------------------------
# Bill Report Reference (one row per fiscal_year x stage, not per observation)
# ---------------------------------------------------------------------------
YEARS = list(range(2026, 2016, -1))
STAGES = ["Enacted", "Senate Reported", "House Reported", "President's Budget"]
STAGE_CODE = {"Enacted": "ENACTED", "Senate Reported": "SENATE", "House Reported": "HOUSE", "President's Budget": "PB"}
ws = wb.create_sheet("Bill Report Reference")
headers = ["reference_id", "subcommittee", "fiscal_year", "stage", "bill_id", "report_id", "bill_url",
           "report_jes_url", "lookup_key", "notes", "vehicle_bill_id", "division", "enactment_date", "funding_type",
           "draft", "not_reported"]
# not_reported (after draft): TRUE when the committee never reported a bill or released a draft for the stage
# (Labor-HHS FY2020 Senate) -- the grid's 'not reported' state
# draft (last column, so no existing column moves): TRUE when the stage's document is a committee draft --
# the full committee never reported the bill (e.g. CJS FY2024 House, H.R. 5893); FALSE otherwise
write_rows(ws, headers, data["bill_report_refs"], [22, 14, 12, 16, 16, 16, 40, 40, 30, 60, 16, 10, 14, 14, 10, 12])
n_br = len(data["bill_report_refs"]) + 1
add_dropdown(ws, "D", STAGE_BR, 2, n_br)
add_dropdown(ws, "N", FUNDING_TYPE, 2, n_br)
add_dropdown(ws, "O", BOOL, 2, n_br)
add_dropdown(ws, "P", BOOL, 2, n_br)
for r in range(2, n_br + 1):
    for col_letter in ("E", "F", "G", "H"):
        ws[f"{col_letter}{r}"].fill = PatternFill("solid", fgColor="FFF2CC")
for row in ws.iter_rows(min_row=2, max_row=n_br, min_col=9, max_col=9):
    for c in row:
        c.font = NOTE_FONT

# One row per subcommittee x fiscal_year x stage. All 40 current rows are CJS -- add rows for
# a new subcommittee (Labor-HHS, Defense, etc.) the same way: reference_id "BR-<SUB>-FY####-<STAGE>",
# lookup_key "<SUB>-####-<stage>". Every account on the Account tab must have its own subcommittee
# value filled in, since that's what the lookup below uses to pick the right subcommittee's row here.

# Live lookups on Appropriations Observation: bill_id, report_id, bill_url, report_jes_url. Each
# observation resolves its own account's subcommittee from the Account tab first, then matches
# subcommittee + fiscal_year + stage against Bill Report Reference's lookup_key. Two chained
# INDEX/MATCH calls, no arrays -- self-updates as both tabs are filled in, no rebuild needed.
SUB_LOOKUP = f"INDEX(Account!${ACC_SUB_COL}$2:${ACC_SUB_COL}${n_acc},MATCH($B{{r}},Account!$A$2:$A${n_acc},0))"
BR_RANGE = f"'Bill Report Reference'!$I$2:$I${n_br}"
for r in range(2, n_obs + 1):
    key = f'{SUB_LOOKUP.format(r=r)}&"-"&$C{r}&"-"&$D{r}'
    ws_obs.cell(row=r, column=6,
                value=f'=IFERROR(""&INDEX(\'Bill Report Reference\'!$E$2:$E${n_br},MATCH({key},{BR_RANGE},0)),"")')
    ws_obs.cell(row=r, column=7,
                value=f'=IFERROR(""&INDEX(\'Bill Report Reference\'!$F$2:$F${n_br},MATCH({key},{BR_RANGE},0)),"")')
    ws_obs.cell(row=r, column=19,
                value=f'=IFERROR(""&INDEX(\'Bill Report Reference\'!$G$2:$G${n_br},MATCH({key},{BR_RANGE},0)),"")')
    ws_obs.cell(row=r, column=20,
                value=f'=IFERROR(""&INDEX(\'Bill Report Reference\'!$H$2:$H${n_br},MATCH({key},{BR_RANGE},0)),"")')
    for col in (6, 7, 19, 20):
        ws_obs.cell(row=r, column=col).font = BODY_FONT
        ws_obs.cell(row=r, column=col).border = BORDER

# ---------------------------------------------------------------------------
# Validation Record
# ---------------------------------------------------------------------------
ws = wb.create_sheet("Validation Record")
headers = ["validation_id", "observation_id", "rule_applied", "expected_result", "observed_result", "result",
           "human_review_status", "reviewer", "resolution"]
write_rows(ws, headers, data["validations"], [12, 16, 16, 45, 45, 10, 16, 14, 30],
           flag_col="result", flag_value="flag")
n = len(data["validations"]) + 1
add_dropdown(ws, "C", RULE, 2, n)
add_dropdown(ws, "F", RESULT, 2, n)
add_dropdown(ws, "G", REVIEW, 2, n)
FAIL_FILL = PatternFill("solid", fgColor="F4B6B6")
for r_i, v in enumerate(data["validations"], start=2):
    if v.get("result") == "fail":
        for c in range(1, len(headers) + 1):
            ws.cell(row=r_i, column=c).fill = FAIL_FILL

# ---------------------------------------------------------------------------
# Component -- vocabulary for Appropriations Observation.component.
#   part      = adds to the account's line (NSF defense, CHIMP, supplemental acts)
#   contained = already inside the headline figure; never add it again (CURES in NIH)
#   view      = the same money scoped another way (parallel totals)
# contained/view observations point at their headline via headline_observation_id.
# ---------------------------------------------------------------------------
ws = wb.create_sheet("Component")
headers = ["component_id", "label", "kind", "description"]
comp_rows = data.get("components", [])
write_rows(ws, headers, comp_rows, [52, 24, 12, 80])
n = max(len(comp_rows) + 1, 2)
add_dropdown(ws, "C", COMP_KIND, 2, n)
for row in ws.iter_rows(min_row=2, max_row=n, min_col=4, max_col=4):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")

# ---------------------------------------------------------------------------
# Read Me
# ---------------------------------------------------------------------------
legend = wb.create_sheet("Read Me", 0)
legend.sheet_view.showGridLines = False
legend.column_dimensions["A"].width = 105
rows = [
    ("Appropriations Pilot \u2014 CJS Title III (Science) + Labor-HHS Title II (HHS), Loaded Against the Schema", TITLE_FONT),
    ("Built from your filled-in CJS_Title_III_Science_Pilot_Schema_Loaded workbook. Five tabs, matching the Data "
     "Dictionary in the scoping doc.", SUBTITLE_FONT),
    ("", BODY_FONT),
    ("v38: two source notes", BOLD_NOTE),
    ("\u2022 SRC-CJ-AHA-FY2026: public link checked by hand (printed p. 11 = PDF p. 11). SRC-CBO-HR8845-FY2027: its 22 "
     "CJS figures are all outside Title III and owner-verified; it is an interim source, to be replaced by the FY2027 House "
     "committee report when the rest of CJS is ingested. Notes only; no figure, link, page or ID changed.", NOTE_FONT),
    ("", BODY_FONT),
    ("v37: verified links; how each year was enacted", BOLD_NOTE),
    ("\u2022 Links: 45 links (source documents and Bill Report Reference bill_url / report_jes_url) now point at the file "
     "whose sha256 matches the document we ingested (Claude Code link check, link_results_v36.csv). Older reports (FY2017\u2013"
     "FY2023) use govinfo, since Congress.gov serves a re-stamped copy; 118th\u2013119th reports use Congress.gov. The 12 "
     "Labor-HHS bill_url landing pages are now PDFs. The FY2023 Senate Labor-HHS statement uses its direct PDF. Links "
     "that could not be checked (no stored file) are unchanged. The build now also stops on a landing-page bill_url.", NOTE_FONT),
    ("\u2022 Bill Report Reference: four new columns on Enacted rows: vehicle_bill_id (the bill the law was enacted as), "
     "division, enactment_date and funding_type (standalone / minibus / omnibus / full_year_cr). Values for the 14 "
     "enacted rows are from the National Taxpayers Union Foundation's Definitive Congressional Appropriations History "
     "Database; every public law matched our bill_id. FY2025 (CJS and Labor-HHS) is full_year_cr (P.L. 119-4).", NOTE_FONT),
    ("\u2022 Page check: 140 of the cited figures were not found by text search on their cited page; nearly all are on "
     "image pages (OCR), and no document shows a consistent page offset, so no page was changed. They are listed in the "
     "repo (page_check_misses_v36.csv) for review. No figure, page or ID changed.", NOTE_FONT),
    ("", BODY_FONT),
    ("v36: link corrections", BOLD_NOTE),
    ("\u2022 H.Rpt. 118-585: govinfo files this report as CRPT-118hrpt585-pt1, so the v35 package URL returned a web "
     "page, not the PDF. It now links the Congress.gov PDF, whose sha256 matches the ingested file. The eight Bill "
     "Report Reference report_jes_url values that were still govinfo landing pages now carry the same PDF links as "
     "their Source Documents. The build now stops on any govinfo landing-page link in either place. No figure, page "
     "or ID changed.", NOTE_FONT),
    ("", BODY_FONT),
    ("v35: source links open the PDF", BOLD_NOTE),
    ("\u2022 Nine govinfo sources were stored as their landing pages (govinfo.gov/app/details/...), which cannot open "
     "at a page. They now point to the PDF itself (govinfo.gov/content/pkg/<package>/pdf/<file>.pdf): S.Rpt. 118-84, "
     "118-207, 119-55; H.Rpt. 118-585, 119-271, 119-696, 117-403; the FY2026 Labor-HHS explanatory statement "
     "(Congressional Record, 2026-01-22); P.L. 119-4. No figure, page or ID changed.", NOTE_FONT),
    ("", BODY_FONT),
    ("v34: verification rule enforced, AHA relationships", BOLD_NOTE),
    ("\u2022 One rule now sets verification_status everywhere: 'auto-validated' only with confidence >= 0.90, every "
     "check passing, and at least one check that confirms the figure (a sum or cross-document match); 'flagged' when "
     "a check fails or a person deliberately flagged it; otherwise 'unverified'. 382 Labor-HHS rows moved from "
     "auto-validated to unverified, 2 the other way. No auto-validated row is below 0.90. A check a person resolved "
     "(human_review_status 'resolved', its resolution written) no longer counts; the rule applies to the rest. "
     "law_text (an Enacted figure equal to the enrolled law's first dollar amount under its heading) confirms like a "
     "printed sum; a law_text 'info' record (program level, advances, transfers, trust-fund limitations) counts neither way.", NOTE_FONT),
    ("\u2022 FY2026 AHA Congressional Justification added (SRC-CJ-AHA-FY2026, excerpt pp. 11-19). Five proposed moves "
     "into AHA, each matched to the dollar against H.Rept. 119-271's FY2026 request: NIEHS, CDC Injury Prevention, "
     "Birth Defects, NIOSH and EEOICPA (REL-LHHS-0011..0015). REL-LHHS-0003/0004 (agency-total links) removed; their "
     "IDs are not reused. Partial moves (CDC Environmental Health, CDC HIV, OASH offices, WTC) get no relationship.", NOTE_FONT),
    ("", BODY_FONT),
    ("v33: full HHS account list, FY2023 Labor-HHS, schema clean-up", BOLD_NOTE),
    ("\u2022 Labor-HHS Title II now has one account per appropriation heading in the enacted law (100 accounts, up "
     "from 25): every NIH institute and center, CDC's and SAMHSA's headings, and the rest. Agency totals can now be "
     "checked against the sum of their accounts.", NOTE_FONT),
    ("\u2022 FY2023 Labor-HHS added: President's Budget and House Reported (H.Rept. 117-403) and Senate Reported from the "
     "Senate committee's draft explanatory statement (released 2022-07-28; S. 4659 introduced, never reported). "
     "AHRQ FY2023 is recorded from the table's 'Federal funds' line; the bill-wide NEF rescissions are recorded but "
     "never enter a Title II reconciliation.", NOTE_FONT),
    ("\u2022 CURES is now its own account (ACC-HHS-NIH-CURES, the law's 'NIH Innovation Account, CURES Act' heading). "
     "Its 14 earlier observations moved to it with the same IDs; the CURES component is retired.", NOTE_FONT),
    ("\u2022 Account: effective_start and effective_end removed (they recorded our data coverage, not real dates). "
     "New last column parent_account_id: Health Centers sits inside Primary Health Care, Head Start inside Children "
     "and Families Services Programs. Source Document and Bill Report Reference gain a notes column.", NOTE_FONT),
    ("\u2022 The build now also stops on broken references (unknown account, document, component, headline or parent), "
     "duplicate IDs, or any leftover effective date.", NOTE_FONT),
    ("", BODY_FONT),
    ("v32: Labor-HHS FY2025 Enacted (full-year CR year)", BOLD_NOTE),
    ("\u2022 35 FY2025 Enacted observations (OBS-LHHS-0455..0489) from H.Rept. 119-271's 'FY 2025 Estimate' column, "
     "which the report defines (pp.319-320) as operating plans and other available information. Checked against the "
     "law: wherever P.L. 119-4 sets a dollar level, the column equals it exactly (23 rows, each with a cross_document "
     "PASS citing the provision). The 12 mandatory rows have no dollar level in law (P.L. 119-4 sec. 1109(a): amounts "
     "necessary), so the figure is the report's estimate; they carry a semantic FLAG saying so.", NOTE_FONT),
    ("\u2022 Medicaid's FY2025 advance (for the 1st quarter of FY2026, sec. 1109(b)(2)) is printed under a 'FY 2027' "
     "heading in the report; stored as fiscal_year 2025, amount_type advance. CA-LHHS-0025: the NEF emergency "
     "rescission line is not printed for FY2025. Title II FY2025 figures are House scope (HHS rescissions inside "
     "Title II), unlike FY2024's Senate-scope figures.", NOTE_FONT),
    ("", BODY_FONT),
    ("v31: total_scope field + Adoption Incentives confirmed absences", BOLD_NOTE),
    ("\u2022 New Account column P, total_scope: agency / title / bill for rollup accounts (13 agency totals, 1 title "
     "total), blank for real funding accounts. This is the only place a total's scope is recorded; notes are free text "
     "for people and are never parsed for it.", NOTE_FONT),
    ("\u2022 Six new Confirmed Absences for Adoption Incentives (rescission): FY2025 Enacted (P.L. 119-4 sec. 1101(a)(8) "
     "continues FY2024 Labor-HHS but expressly excludes sec. 241, the FY2024 rescission), FY2026 President's Budget, "
     "House Reported and Enacted, FY2027 President's Budget and House Reported. New Source Document SRC-PLAW-119PUBL4 "
     "and Bill Report Reference BR-LHHS-FY2025-ENACTED.", NOTE_FONT),
    ("", BODY_FONT),
    ("v30: consistency pass (file renamed from CJS_Title_III_Science_Pilot_Schema_Loaded)", BOLD_NOTE),
    ("\u2022 Subcommittee codes are now short and uniform: CJS and LHHS. Bill Report Reference lookup_key follows "
     "the same pattern for both (e.g. LHHS-2023-Enacted, matching reference_id BR-LHHS-FY2023-ENACTED). The UI "
     "shows the full name \"Labor-HHS-Education\" as a display label.", NOTE_FONT),
    ("\u2022 extraction_method is spelled human-entered everywhere, matching the Data Dictionary (906 rows had "
     "human_entered). The build now stops if any field holds a value outside its allowed list.", NOTE_FONT),
    ("\u2022 Component.label is filled in: the literal printed name where one exists (e.g. CURES Act, program level "
     "(excluding ARPA-H)), otherwise the heading or line text the component was read from.", NOTE_FONT),
    ("", BODY_FONT),
    ("v29: second subcommittee (Labor-HHS Title II) merged", BOLD_NOTE),
    (f"\u2022 Workbook now holds {len(data['accounts'])} accounts and {len(data['observations'])} observations across two "
     "subcommittees (Account.subcommittee = CJS or LHHS). Labor-HHS IDs are namespaced (ACC-HHS-*, "
     "OBS-LHHS-*, VAL-LHHS-*, CA-LHHS-*, BR-LHHS-*, REL-LHHS-*) so they never collide with CJS IDs.", NOTE_FONT),
    ("\u2022 New Component tab: the vocabulary for Appropriations Observation.component. kind = part (adds to the "
     "account's line), contained (already inside the headline \u2014 never add it again, e.g. CURES inside NIH), or "
     "view (same money scoped another way, e.g. a parallel 'program level' total).", NOTE_FONT),
    ("\u2022 New column U on Appropriations Observation: headline_observation_id. Every contained/view row points at "
     "the headline observation (same account, year, stage and document) it must not be double-counted against. "
     "Appended at the end so no existing column moved.", NOTE_FONT),
    ("\u2022 Labor-HHS rows keep their pipeline provenance (extraction_method text-extracted / AI-extracted / "
     "human_entered; verification_status auto-validated / unverified / flagged). Flagged observations are shaded pink; "
     "failed validations are shaded darker red \u2014 all current fails are table_total checks on rollup rows.", NOTE_FONT),
    ("\u2022 New column V on Appropriations Observation: superseded_by_observation_id. A superseded observation "
     "(verification_status superseded) names the observation that replaced it; nothing is deleted (NIH Office of "
     "the Director FY2022, re-derived as the Office of the Director + Gabriella Miller Kids First).", NOTE_FONT),
    ("\u2022 extraction_method 'derived': a headline the table doesn't print, computed from two printed lines where the "
     "later years' tables define it exactly (Grants to States for Medicaid FY2022-FY2023: 'appropriated in this bill' "
     "minus the new advance); the note names both lines and pages, and a structural record checks the arithmetic. A "
     "confirmed absence is never recorded on an account the document funds.", NOTE_FONT),
    ("", BODY_FONT),
    ("Earlier passes (CJS)", BOLD_NOTE),
    (f"\u2022 Your 40 filled-in Source Document rows collapsed to {len(data['source_docs'])} truly distinct documents \u2014 "
     "many stage/year cells share one committee report's comparison table, exactly like your FY17 Enacted / FY18 PB / "
     "FY18 House example. Each surviving row's also_covers column lists the other fiscal-year/stage cells that document "
     "also supplied, so nothing about the sharing is hidden.", NOTE_FONT),
    (f"\u2022 All {len(data['observations'])} Appropriations Observations were repointed from the old per-year/stage stub "
     "IDs to the correct deduped document \u2014 e.g. every observation that used to point at SRC-FY2025-ENACTED or "
     "SRC-FY2026-HOUSE now points at SRC-CRPT-119HRPT272, the actual H.Rpt. 119-272.", NOTE_FONT),
    ("\u2022 Every observation's source_page is now auto-filled from its document's page range \u2014 the per-observation "
     "page-number task is done, since the page range is a fact about the 2-page table in the document, not about the "
     "individual account. source_table_or_section still carries the account's location in the bill for extra context.", NOTE_FONT),
    ("", BODY_FONT),
    ("Still standing from before", BOLD_NOTE),
    (f"\u2022 {len(data['accounts'])} accounts, 2 kept as agency-total rollups (NASA, NSF). Exploration Technology "
     "stays a standalone active account (its FY2019/2020 values don't match either candidate parent, linked via "
     "two low-confidence uncertain Account Relationships pending confirmation). LEO and Spaceflight Operations "
     "was retired -- confirmed as a real rename of Space Operations, its values folded in, tracked via Historical "
     "Name instead of a separate account row.", NOTE_FONT),
    ("\u2022 Emergency-supplemental lines are split into separate budget authority / supplemental observations per your "
     "earlier answer; the Space Operation rescission line is loaded as a rescission-type observation (just the one real "
     "nonzero cell).", NOTE_FONT),
    (f"\u2022 {len([v for v in data['validations'] if v['rule_applied']=='table_total'])} table_total Validation Records "
     "confirming the NASA/NSF rollups exactly equal their children \u2014 all passed.", NOTE_FONT),
    ("\u2022 REL-0005 and REL-0006 (both Account Relationship rows, human_reviewed = FALSE, confidence 0.2) are the "
     "open judgment call now: two candidate parents for Exploration Technology's FY2019 origin (Space Technology, "
     "Exploration), unverified from memory, neither confirmed against a primary document describing the change.", NOTE_FONT),
    ("\u2022 bill_id and report_id are still blank on every observation \u2014 optional, since source_document_id already "
     "identifies the exact document unambiguously.", NOTE_FONT),
    ("", BODY_FONT),
    ("New: Bill Report Reference tab", BOLD_NOTE),
    ("\u2022 bill_id and report_id moved off individual observations. Fill them in once per fiscal year \u00d7 stage on the "
     "new Bill Report Reference tab (40 rows, yellow-filled columns) instead of 963 times \u2014 every observation's "
     "bill_id and report_id columns are now live formulas that look themselves up from there, keyed on the observation's "
     "own fiscal_year and stage.", NOTE_FONT),
    ("\u2022 A new bill_url and report_jes_url column on Appropriations Observation work the same way \u2014 fill in bill_url "
     "and report_jes_url on Bill Report Reference (separate links, since a bill's own text and its committee report or "
     "joint explanatory statement are different documents) and every matching observation shows both automatically. "
     "Both are separate from source_document_id, which points to whichever document you actually pulled the number from.", NOTE_FONT),
    ("\u2022 These are live formulas, not a one-time copy \u2014 edit Bill Report Reference at any point and every observation "
     "updates itself. No need to ask me to re-run a backfill pass.", NOTE_FONT),
    ("", BODY_FONT),
    ("New: subcommittee dimension", BOLD_NOTE),
    ("\u2022 Account now has a subcommittee column (all 21 rows currently \"CJS\"). Bill Report Reference now has one too, "
     "and its reference_id and lookup_key are keyed on subcommittee + fiscal_year + stage instead of just fiscal_year + "
     "stage \u2014 so a future Labor-HHS or Defense FY2024 House Reported row won't collide with CJS's.", NOTE_FONT),
    ("\u2022 Every observation's bill_id, report_id, bill_url, and report_jes_url formulas now resolve their own account's "
     "subcommittee first (via canonical_account_id against the Account tab), then use that alongside fiscal_year and "
     "stage to find the right Bill Report Reference row. When you add accounts from another subcommittee, just set their "
     "subcommittee on Account and add that subcommittee's rows to Bill Report Reference \u2014 nothing else needs to change.", NOTE_FONT),
    ("\u2022 Your 9 already-filled Bill Report Reference rows carried forward untouched, just with subcommittee = CJS added "
     "and their reference_id/lookup_key updated to match.", NOTE_FONT),
]
r = 1
for text, font in rows:
    c = legend.cell(row=r, column=1, value=text)
    c.font = font
    c.alignment = Alignment(wrap_text=True, vertical="top")
    legend.row_dimensions[r].height = 34 if font in (TITLE_FONT, SUBTITLE_FONT) else (18 if text else 8)
    r += 1

wb.save("build/Approps_Pilot_Schema_Loaded.xlsx")
print("saved")
