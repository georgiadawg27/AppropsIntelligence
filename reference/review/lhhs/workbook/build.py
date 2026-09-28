"""
Workbook-ready rows for the Labor-HHS Title II proof, for merging into v29:
one CSV per workbook tab, the tab's own columns (the Appropriations
Observation tab also carries the proposed headline_observation_id, and the
proposed Component tab is included), plus the FY2025 "Estimate" rows held
back for a decision.

    python reference/review/lhhs/workbook/build.py        # writes the CSVs next to this file

Scope (as agreed): the ten agency totals, the Title II total and nine
accounts, plus the proposed Administration for a Healthy America; every
fiscal year x stage the six committee reports and the FY2026 JES print.

One observation per account x fiscal year x stage x amount_type x component
(the store's fact key). A cell's headline and its components come from one
document, so a component's headline_observation_id is in the same document.
Which document, when several print the cell:
  1. the cell's own document (a Senate report for Senate Reported; the JES
     for FY2026 Enacted);
  2. else the text layer over vision (deterministic reading);
  3. else the later publication.
Every other document printing the same fact becomes a cross_document
validation record on the chosen observation. The FY2026 JES figures were read
by hand from the Congressional Record's page images (hand_checks.csv):
extraction_method human_entered, as asked -- read by Claude, not a person.

Reads extractions/*.title-ii.json (gitignored: rerun extract_approps.py
--title "TITLE II" --offline) and ../hand_checks.csv, ../law_text.csv.
"""

import csv
import glob
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

import accounts as A  # noqa: E402

SUB = "Labor-HHS-Education"
TODAY = "2026-09-28"
JES = "MANUAL-LHHS-FY2026-Enacted-jes-e44f7662"

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

DOCS = {  # package -> Source Document fields (published dates: govinfo summaries)
    "CRPT-118srpt84": dict(id="SRC-CRPT-118SRPT84", agency="Senate Committee on Appropriations", type="committee_report",
                           cs="118-1", fy=2024, date="2023-07-27", stage="Senate Reported", pages="366-392",
                           also="FY2024 President's Budget; FY2023 Enacted", url="https://www.govinfo.gov/app/details/CRPT-118srpt84",
                           bill="S.2624", report="S.Rept.118-84", bill_url="https://www.govinfo.gov/app/details/BILLS-118s2624rs"),
    "CRPT-118srpt207": dict(id="SRC-CRPT-118SRPT207", agency="Senate Committee on Appropriations", type="committee_report",
                            cs="118-2", fy=2025, date="2024-08-01", stage="Senate Reported", pages="430-452",
                            also="FY2025 President's Budget; FY2024 Enacted", url="https://www.govinfo.gov/app/details/CRPT-118srpt207",
                            bill="S.4942", report="S.Rept.118-207", bill_url="https://www.govinfo.gov/app/details/BILLS-118s4942rs"),
    "CRPT-119srpt55": dict(id="SRC-CRPT-119SRPT55", agency="Senate Committee on Appropriations", type="committee_report",
                           cs="119-1", fy=2026, date="2025-08-01", stage="Senate Reported", pages="426-442",
                           also="", url="https://www.govinfo.gov/app/details/CRPT-119srpt55",
                           bill="S.2587", report="S.Rept.119-55", bill_url="https://www.govinfo.gov/app/details/BILLS-119s2587rs"),
    "CRPT-118hrpt585": dict(id="SRC-CRPT-118HRPT585", agency="House Committee on Appropriations", type="committee_report",
                            cs="118-2", fy=2025, date="2024-07-12", stage="House Reported", pages="308-340",
                            also="FY2025 President's Budget; FY2024 Enacted", url="https://www.govinfo.gov/app/details/CRPT-118hrpt585",
                            bill="H.R.9029", report="H.Rept.118-585", bill_url="https://www.govinfo.gov/app/details/BILLS-118hr9029rh"),
    "CRPT-119hrpt271": dict(id="SRC-CRPT-119HRPT271", agency="House Committee on Appropriations", type="committee_report",
                            cs="119-1", fy=2026, date="2025-09-11", stage="House Reported", pages="334-366",
                            also="FY2026 President's Budget", url="https://www.govinfo.gov/app/details/CRPT-119hrpt271",
                            bill="H.R.5304", report="H.Rept.119-271", bill_url="https://www.govinfo.gov/app/details/BILLS-119hr5304rh"),
    "CRPT-119hrpt696": dict(id="SRC-CRPT-119HRPT696", agency="House Committee on Appropriations", type="committee_report",
                            cs="119-2", fy=2027, date="2026-06-11", stage="House Reported", pages="365-397",
                            also="FY2027 President's Budget; FY2026 Enacted", url="https://www.govinfo.gov/app/details/CRPT-119hrpt696",
                            bill="H.R.9260", report="H.Rept.119-696", bill_url="https://www.govinfo.gov/app/details/BILLS-119hr9260rh"),
    JES: dict(id="SRC-EXPL-LHHS-FY2026-ENACTED", agency="House Committee on Appropriations (Congressional Record)",
              type="explanatory_statement", cs="119-2", fy=2026, date="2026-01-22", stage="Enacted", pages="281-296",
              also="", url="https://www.govinfo.gov/app/details/CREC-2026-01-22/CREC-2026-01-22-pt2-PgH1353-2",
              bill="P.L.119-75", report="JES_LHHS_P.L.119-75", bill_url="https://www.govinfo.gov/app/details/PLAW-119publ75"),
}
RETRIEVED = "2026-09-27 00:00:00"
OWN_CELL = {pkg: (d["fy"], d["stage"]) for pkg, d in DOCS.items()}

# (fiscal_year, stage) -> (bill_id, report_id, bill_url, report_jes_url)
BRR = {
    (2023, "Enacted"): ("P.L.117-328", "N/A", "https://www.govinfo.gov/app/details/PLAW-117publ328", "N/A"),
    (2024, "President's Budget"): ("PREX 2.8:2024/APP", "N/A", "https://www.govinfo.gov/content/pkg/BUDGET-2024-APP/pdf/BUDGET-2024-APP.pdf", "N/A"),
    (2024, "Senate Reported"): ("S.2624", "S.Rept.118-84", DOCS["CRPT-118srpt84"]["bill_url"], DOCS["CRPT-118srpt84"]["url"]),
    (2024, "Enacted"): ("P.L.118-47", "N/A", "https://www.govinfo.gov/app/details/PLAW-118publ47", "N/A"),
    (2025, "President's Budget"): ("PREX 2.8:2025/APP", "N/A", "https://www.govinfo.gov/content/pkg/BUDGET-2025-APP/pdf/BUDGET-2025-APP.pdf", "N/A"),
    (2025, "Senate Reported"): ("S.4942", "S.Rept.118-207", DOCS["CRPT-118srpt207"]["bill_url"], DOCS["CRPT-118srpt207"]["url"]),
    (2025, "House Reported"): ("H.R.9029", "H.Rept.118-585", DOCS["CRPT-118hrpt585"]["bill_url"], DOCS["CRPT-118hrpt585"]["url"]),
    (2026, "President's Budget"): ("PREX 2.8:2026/APP", "N/A", "https://www.govinfo.gov/content/pkg/BUDGET-2026-APP/pdf/BUDGET-2026-APP.pdf", "N/A"),
    (2026, "Senate Reported"): ("S.2587", "S.Rept.119-55", DOCS["CRPT-119srpt55"]["bill_url"], DOCS["CRPT-119srpt55"]["url"]),
    (2026, "House Reported"): ("H.R.5304", "H.Rept.119-271", DOCS["CRPT-119hrpt271"]["bill_url"], DOCS["CRPT-119hrpt271"]["url"]),
    (2026, "Enacted"): ("P.L.119-75", "JES_LHHS_P.L.119-75", DOCS[JES]["bill_url"], DOCS[JES]["url"]),
    (2027, "President's Budget"): ("PREX 2.8:2027/APP", "N/A", "https://www.govinfo.gov/content/pkg/BUDGET-2027-APP/pdf/BUDGET-2027-APP.pdf", "N/A"),
    (2027, "House Reported"): ("H.R.9260", "H.Rept.119-696", DOCS["CRPT-119hrpt696"]["bill_url"], DOCS["CRPT-119hrpt696"]["url"]),
}
BRR_ID = {"Enacted": "ENACTED", "President's Budget": "PB", "Senate Reported": "SENATE", "House Reported": "HOUSE"}

# ---------------------------------------------------------------------------
# Accounts: (id, name, agency, bureau, fund_type, status, notes, headline patterns, view patterns, extra)
# Headline patterns are tried in order; the first that prints in a document is
# the account's figure there. View patterns name parallel totals (their
# component is the extractor's printed scope).
# ---------------------------------------------------------------------------

AGENCY_TOTAL_NOTE = ("Derived rollup -- {a}'s agency total as the Title II table prints it; kept as a reference row, "
                     "not an independent funding decision.")
ACCOUNTS = [
    dict(id="ACC-HHS-AHA-TOTAL", name="Administration for a Healthy America (agency total)",
         agency="Administration for a Healthy America", bureau="(Agency Total)", fund="general", status="proposed",
         start="2025-10-01",
         notes=("Proposed in the FY2026 and FY2027 budget requests only; never reported by a committee or enacted "
                "(P.L. 119-75 has no such heading). The committee tables print it only in the request column, as the "
                "request's funding 'not displayed under other HHS accounts below' (H.Rept. 119-271 p.334 footnote 1/): "
                "the tables show most of the proposed AHA under HRSA, SAMHSA and the other legacy lines. Derived rollup "
                "(agency). H.Rept. 119-696 prints no plain total, only 'Administration for a Healthy America, "
                "Discretionary 1/', recorded as the headline there."),
         head=[r"^Total, Administration for a Healthy America$", r"^Administration for a Healthy America, Discretionary$"],
         views=[r"^Total, Administration for a Healthy America, program level$"]),
    dict(id="ACC-HHS-HRSA-HEALTH-CENTERS", name="Health Centers", agency="Health Resources and Services Administration",
         bureau="Primary Health Care", fund="general",
         notes="A program line under Primary Health Care (with Free Clinics Medical Malpractice).",
         head=[r"^Health Centers$"]),
    dict(id="ACC-HHS-HRSA-PRIMARY-CARE", name="Primary Health Care", agency="Health Resources and Services Administration",
         bureau="Primary Health Care", fund="general",
         notes="The appropriation heading 'Primary Health Care' (the law's dollar figure); printed as 'Total, Primary Health Care'.",
         head=[r"^Total, Primary Health Care$"]),
    dict(id="ACC-HHS-HRSA-VICTF", name="Vaccine Injury Compensation Program Trust Fund",
         agency="Health Resources and Services Administration", bureau="Vaccine Injury Compensation Program", fund="trust",
         notes=("Trust fund, mandatory. The law appropriates 'such sums as may be necessary' for claims plus a fixed HRSA "
                "administrative-expenses amount (15,200); the tables' claims figure is an estimate, not a law figure."),
         head=[r"^Total, Vaccine Injury Compensation (Program )?Trust Fund$"]),
    dict(id="ACC-HHS-HRSA-TOTAL", name="Health Resources and Services Administration (agency total)",
         agency="Health Resources and Services Administration", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="HRSA") + " Includes the Vaccine Injury Compensation trust fund and the Covered "
               "Countermeasures Process Fund; 'Total, Health Resources and Services' (no 'Administration') is a smaller subtotal.",
         head=[r"^Total, Health Resources and Services Administration$"]),
    dict(id="ACC-HHS-CDC-TOTAL", name="Centers for Disease Control and Prevention (agency total)",
         agency="Centers for Disease Control and Prevention", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="CDC"),
         head=[r"^Total, Centers for Disease Control and Prevention$"],
         views=[r"^Total, Centers for Disease Control,? program level$"]),
    dict(id="ACC-HHS-NIH-NCI", name="National Cancer Institute", agency="National Institutes of Health",
         bureau="National Cancer Institute", fund="general", notes=None,
         head=[r"^National Cancer Institute( \(NCI\))?$"]),
    dict(id="ACC-HHS-NIH-TOTAL", name="National Institutes of Health (agency total)", agency="National Institutes of Health",
         bureau="(Agency Total)", fund="general", rollup=True,
         notes=(AGENCY_TOTAL_NOTE.format(a="NIH") + " Headline: 'Total, National Institutes of Health (with CURES Act "
                "funding)' (decision 2026-09-27). The CURES Act line is its own observation, component CURES (kind "
                "'contained': inside this headline, never added to it); the Title II total subtracts it."),
         head=[r"^Total, National Institutes of Health (\(NIH\) with CURES Act funding|\(with CURES Act funding\))$"],
         views=[r"^Total, National Institutes of Health,? program level", r"^Total, NIH,? program level \(excluding ARPA-H\)$"],
         # the CURES Act figure the table prints under the Title II total: NIH's whole CURES amount (FY2023-24
         # columns split it across four institutes' "NIH Innovation Account, CURES Act" lines, checked to sum to it)
         contained=[r"^\(?CURES Act\)?( \(under the title total\))?$"], contained_parts=r"^\(?NIH Innovation Account, CURES Act\)?$"),
    dict(id="ACC-HHS-SAMHSA-TOTAL", name="Substance Abuse and Mental Health Services Administration (agency total)",
         agency="Substance Abuse and Mental Health Services Administration", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="SAMHSA"),
         head=[r"^Total, (SAMHSA|Substance Abuse and Mental Health Services Administration)$"],
         views=[r"^Total, SAMHSA, program level$"]),
    dict(id="ACC-HHS-AHRQ-TOTAL", name="Agency for Healthcare Research and Quality (agency total)",
         agency="Agency for Healthcare Research and Quality", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="AHRQ"),
         head=[r"^Total, (AHRQ|Agency for Healthcare Research and Quality)$"],
         views=[r"^Total, AHRQ, program level$"]),
    dict(id="ACC-HHS-CMS-MEDICAID", name="Grants to States for Medicaid", agency="Centers for Medicare & Medicaid Services",
         bureau="Grants to States for Medicaid", fund="general",
         notes=("Mandatory. Three printed totals, each its own observation (decision 2026-09-27): the current-year "
                "'Total, Grants to States for Medicaid' (headline); 'Total, Medicaid[,] program level, available this fiscal "
                "year' (component program_level_available_this_fiscal_year); '..., appropriated in this bill' (component "
                "appropriated_in_this_bill). The new first-quarter advance is amount_type 'advance' and 'Less appropriations "
                "provided in prior years' amount_type 'prior_year_advance' (negative, as printed). S.Rept. 118-84 prints no "
                "current-year total."),
         head=[r"^Total, Grants to States for Medicaid$"],
         views=[r"^Total, Medicaid,? program level, available this fiscal year$",
                r"^Total, Grants to States for Medicaid, appropriated in this bill$"],
         advances=True),
    dict(id="ACC-HHS-CMS-TRUST-FUND-PAYMENTS", name="Payments to the Health Care Trust Funds",
         agency="Centers for Medicare & Medicaid Services", bureau="Payments to the Health Care Trust Funds", fund="general",
         notes="Mandatory general-fund payments into the Medicare trust funds.",
         head=[r"^Total, Payments to (the )?(Health Care )?Trust Funds$"]),
    dict(id="ACC-HHS-CMS-PROGRAM-MANAGEMENT", name="Program Management", agency="Centers for Medicare & Medicaid Services",
         bureau="Program Management", fund="trust",
         notes="Funded by transfer from the Medicare trust funds (it sits in CMS's 'Trust Funds' memo line).",
         head=[r"^Total, Program Management$"]),
    dict(id="ACC-HHS-CMS-TOTAL", name="Centers for Medicare & Medicaid Services (agency total)",
         agency="Centers for Medicare & Medicaid Services", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="CMS") + " Sums Medicaid 'appropriated in this bill' (advance included).",
         head=[r"^Total, Centers for Medicare (and|&) Medicaid Services$"]),
    dict(id="ACC-HHS-ACF-LIHEAP", name="Low Income Home Energy Assistance", agency="Administration for Children and Families",
         bureau="Low Income Home Energy Assistance", fund="general",
         notes=("No stable total row: 'Total, LIHEAP' where printed; S.Rept. 118-207 and H.Rept. 118-585 print only "
                "'Total, LIHEAP, program level' (recorded as the headline there); the FY2026 JES prints only 'Formula Grants'."),
         head=[r"^Total, LIHEAP$", r"^Total, Low Income Home Energy Assistance$", r"^Total, LIHEAP, program level$",
               r"^Formula Grants$"], head_page_hint="LIHEAP"),
    dict(id="ACC-HHS-ACF-HEAD-START", name="Head Start", agency="Administration for Children and Families",
         bureau="Children and Families Services Programs", fund="general",
         notes="The line item; an 'Additional funding (emergency)' line under it is a separate supplemental line, not in scope.",
         head=[r"^Head Start$"]),
    dict(id="ACC-HHS-ACF-TOTAL", name="Administration for Children and Families (agency total)",
         agency="Administration for Children and Families", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="ACF"),
         head=[r"^Total, Administration for Children and Families$"],
         views=[r"^Total, (ACF|Administration for Children and Families) \(excluding emergencies\)$",
                r"^Total, Administration for Children and Families,? discretionary$"]),
    dict(id="ACC-HHS-ACL-TOTAL", name="Administration for Community Living (agency total)",
         agency="Administration for Community Living", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="ACL"),
         head=[r"^Total, Administration for Community Living$"],
         views=[r"^Total, Administration for Community Living, program level$"]),
    dict(id="ACC-HHS-ASPR-TOTAL", name="Administration for Strategic Preparedness and Response (agency total)",
         agency="Administration for Strategic Preparedness and Response", bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="ASPR") + " A separate operating division in these tables, outside the Office of the Secretary total.",
         head=[r"^Total, (Administration|Office of the Assistant Secretary) for (Strategic )?Preparedness and Response$"]),
    dict(id="ACC-HHS-OS-TOTAL", name="Office of the Secretary (agency total)", agency="Office of the Secretary",
         bureau="(Agency Total)", fund="general", rollup=True,
         notes=AGENCY_TOTAL_NOTE.format(a="the Office of the Secretary"),
         head=[r"^Total, Office of the Secretary$"],
         views=[r"^Total, Office of the Secretary, program level$"]),
    dict(id="ACC-HHS-TITLE-II-TOTAL", name="Title II, Department of Health and Human Services (title total)",
         agency="Department of Health and Human Services", bureau="(Title Total)", fund="general", rollup=True,
         notes=("Derived rollup -- the Title II title total as printed. Reconciles as the ten agency totals (eleven in the "
                "FY2026-27 requests, with AHA) + General Provisions lines (signed) - the CURES Act line, in all 16 printed "
                "columns (reference/review/lhhs/title_ii_totals.py). The House and Senate reports scope FY2024's general "
                "provisions differently, so their FY2024 figures differ by 1,320,000 as printed."),
         head=[r"^Total, Title II, Department of Health and Human Services$"],
         views=[r"^Total, Title II, Department of Health and Human Services,? discretionary$"]),
]
BY_ID = {a["id"]: a for a in ACCOUNTS}
DASHES = re.compile(r"[‐-―−]")


def fold(label):
    label = re.sub(r"\s+", " ", DASHES.sub("-", label)).strip().rstrip(".")
    return re.sub(r"(\s+\d+/)+$", "", label)


# ---------------------------------------------------------------------------
# Figures, per document: pkg -> [fig]
# ---------------------------------------------------------------------------

def extracted():
    out = {}
    for path in sorted(glob.glob(str(ROOT / "extractions" / "*.title-ii.json"))):
        d = json.load(open(path))
        pkg = d["source_document"]["package_id"]
        if pkg not in DOCS:
            continue
        recs = defaultdict(list)
        for r in d["validation_records"]:
            recs[r["observation_id"]].append(r)
        out[pkg] = [dict(o, _records=recs[o["observation_id"]], _pkg=pkg) for o in d["observations"]]
    return out


def jes_figures():
    """hand_checks.csv's JES rows as observation-like dicts."""
    figs = []
    with open(HERE.parent / "hand_checks.csv", newline="") as f:
        for i, h in enumerate(csv.DictReader(f)):
            if h["document"] != JES or not re.fullmatch(r"\(?-?\d+\)?", h["hand_read_thousands"] or ""):
                continue
            label = re.sub(r"\s*\(LIHEAP; no LIHEAP total is printed\)", "", h["label_as_printed"])
            v = int(h["hand_read_thousands"].strip("()"))
            memo = h["hand_read_thousands"].startswith("(")
            t = "advance" if re.match(r"^New advance", label) else \
                "prior_year_advance" if re.match(r"^Less appropriations provided in prior years", label) else "budget authority"
            comp = None
            if re.search(r"CURES Act", label) and not re.search(r"with CURES", label):
                comp = "CURES"
            figs.append(dict(observation_id=f"JES-{i}", account_name_as_written=label, amount=v * 1000, amount_as_printed=
                             h["hand_read_thousands"], fiscal_year=2026, stage="Enacted", column_header="FINAL BILL",
                             source_page=f"{h['pdf_page']} ({h['printed_page']})", is_memo=memo, row_kind="line",
                             amount_type=t, account_component=comp, extraction_method="human_entered",
                             extraction_confidence=0.95, node_id=int(h["pdf_page"]) * 100, _records=[], _pkg=JES,
                             advance_for_fiscal_year=2027 if t == "advance" else None))
    return figs


def matches(rx_list, label):
    return any(re.match(DASHES.sub("-", rx), fold(label), re.I) for rx in rx_list)


def account_facts(figs_by_doc):
    """-> {(account, fy, stage): {pkg: [(fig, amount_type, component, role)]}}"""
    out = defaultdict(lambda: defaultdict(list))
    for pkg, figs in figs_by_doc.items():
        cols = defaultdict(list)
        for f in figs:
            if f["amount"] is None or f["fiscal_year"] is None or f["stage"] is None:
                continue          # a column with no stage (H.Rept. 119-271's "FY 2025 Estimate"): held back, see main()
            cols[(f["fiscal_year"], f["stage"], f["column_header"])].append(f)
        for (fy, stage, _), rows in cols.items():
            for a in ACCOUNTS:
                head = None
                for rx in a["head"]:
                    hits = [f for f in rows if matches([rx], f["account_name_as_written"])
                            and not (f["is_memo"] and not rx.startswith("^Administration for a Healthy"))]
                    if hits:
                        head = hits[0]
                        break
                if head is None:
                    continue
                out[(a["id"], fy, stage)][pkg].append((head, head["amount_type"], None, "headline"))
                for rx in a.get("views", []):
                    for f in rows:
                        if matches([rx], f["account_name_as_written"]) and f is not head:
                            out[(a["id"], fy, stage)][pkg].append((f, "budget authority", view_component(f), "view"))
                for rx in a.get("contained", []):
                    for f in rows:
                        if matches([rx], f["account_name_as_written"]):
                            parts = [x for x in rows if matches([a["contained_parts"]], x["account_name_as_written"])]
                            f = dict(f, _parts=parts)
                            out[(a["id"], fy, stage)][pkg].append((f, "budget authority", "CURES", "contained"))
                if a.get("advances"):
                    # the advance and prior-year lines printed with Medicaid's totals
                    near = [f for f in rows if f["amount_type"] in ("advance", "prior_year_advance") and not f["is_memo"]
                            and abs(f["node_id"] - head["node_id"]) <= 3]
                    for f in near:
                        out[(a["id"], fy, stage)][pkg].append((f, f["amount_type"], None, "line"))
    return out


def view_component(f):
    c = f.get("account_component")
    if c:
        return c
    low = fold(f["account_name_as_written"]).lower()          # hand-read JES rows
    for pat, comp in ((r"program level \(with cures", "program_level_with_cures_and_phs_evaluation_act_funding"),
                      (r"program level \(excluding arpa", "program_level_excluding_arpa_h"),
                      (r"program level, available this fiscal year", "program_level_available_this_fiscal_year"),
                      (r"appropriated in this bill", "appropriated_in_this_bill"),
                      (r"excluding emergencies", "excluding_emergencies"),
                      (r"discretionary$", "discretionary"), (r"program level$", "program_level")):
        if re.search(pat, low):
            return comp
    raise ValueError(f"no component for view {f['account_name_as_written']!r}")


def choose(docs, cell):
    """Which document supplies a cell (see the module notes)."""
    def rank(pkg):
        own = 0 if OWN_CELL[pkg] == cell else 1
        method = 0 if docs[pkg][0][0].get("extraction_method") in ("text-extracted", "human_entered") else 1
        return (own, method, -int(DOCS[pkg]["date"].replace("-", "")))
    return sorted(docs, key=rank)[0]


# ---------------------------------------------------------------------------

def main():
    figs = extracted()
    figs[JES] = jes_figures()
    facts = account_facts(figs)
    law = defaultdict(list)
    with open(HERE.parent / "law_text.csv", newline="") as f:
        for r in csv.DictReader(f):
            law[(r["concept"], int(r["fiscal_year"]), r["figure"])].append(r)
    LAW_CONCEPT = {"ACC-HHS-HRSA-PRIMARY-CARE": "Primary Health Care", "ACC-HHS-NIH-NCI": "National Cancer Institute",
                   "ACC-HHS-ACF-LIHEAP": "LIHEAP", "ACC-HHS-CMS-MEDICAID": "Grants to States for Medicaid",
                   "ACC-HHS-CMS-TRUST-FUND-PAYMENTS": "Payments to the Health Care Trust Funds",
                   "ACC-HHS-CMS-PROGRAM-MANAGEMENT": "CMS Program Management", "ACC-HHS-AHRQ-TOTAL": "AHRQ",
                   "ACC-HHS-ACF-HEAD-START": "Head Start"}

    obs_rows, val_rows, used_cells = [], [], set()
    n_obs = n_val = 0
    first_fy = defaultdict(lambda: 9999)
    for (acct, fy, stage), docs in sorted(facts.items(), key=lambda kv: (BY_ID[kv[0][0]]["id"], kv[0][1], kv[0][2])):
        a = BY_ID[acct]
        if a.get("status") == "proposed" and stage != "President's Budget":
            continue                                   # request-stage only (its other cells: Confirmed Absences)
        cell = (fy, stage)
        pkg = choose(docs, cell)
        d = DOCS[pkg]
        bill, report, bill_url, report_url = BRR[cell]
        used_cells.add(cell)
        first_fy[acct] = min(first_fy[acct], fy)
        head_id = None
        for f, amount_type, component, role in docs[pkg]:
            n_obs += 1
            oid = f"OBS-LHHS-{n_obs:04d}"
            if role == "headline":
                head_id = oid
            recs = [r for r in f["_records"] if r["rule_applied"] in ("table_total", "structural", "source_text", "unit", "semantic")]
            others = {p: [x for x in docs[p] if x[1] == amount_type and x[2] == component] for p in docs if p != pkg}
            agree, disagree = [], []
            for p, xs in others.items():
                for x, *_ in xs:
                    (agree if x["amount"] == f["amount"] else disagree).append((p, x))
            method = f.get("extraction_method")
            passed = any(r["result"] == "pass" and r["rule_applied"] in ("table_total", "structural") for r in recs) or bool(agree)
            failed = any(r["result"] == "fail" for r in recs) or bool(disagree)
            status = "flagged" if failed else ("auto-validated" if passed else "unverified")
            label = f["account_name_as_written"]
            obs_rows.append({
                "observation_id": oid, "canonical_account_id": acct, "fiscal_year": fy, "stage": stage,
                "chamber": {"House Reported": "House", "Senate Reported": "Senate"}.get(stage, "N/A"),
                "bill_id": bill, "report_id": report, "amount": f["amount"], "amount_type": amount_type,
                "component": component, "headline_observation_id": head_id if role in ("view", "contained") else None,
                "offsetting_collections": "FALSE", "transfer_link_account_id": None,
                "source_document_id": d["id"], "source_page": str(f["source_page"]).split(" ")[0],
                "source_table_or_section": f"Title II, {a['agency']} -- printed as {label!r} [{f['column_header']}]"
                                           + (f"; advance for FY{fy + 1}" if amount_type == "advance" else ""),
                "extraction_method": method, "confidence": f.get("extraction_confidence") or 0.95,
                "verification_status": status, "bill_url": bill_url, "report_jes_url": report_url,
            })
            for r in recs:
                n_val += 1
                val_rows.append({"validation_id": f"VAL-LHHS-{n_val:05d}", "observation_id": oid,
                                 "rule_applied": r["rule_applied"], "expected_result": r["expected_result"],
                                 "observed_result": r["observed_result"], "result": r["result"],
                                 "human_review_status": "pending" if r["result"] in ("fail", "flag") else None,
                                 "reviewer": None, "resolution": None})
            for p, x in agree + disagree:
                n_val += 1
                ok = x["amount"] == f["amount"]
                why = ""
                if not ok and acct == "ACC-HHS-TITLE-II-TOTAL":
                    why = (" -- as printed: the two reports scope this year's Title II general provisions differently "
                           "(title_ii_totals.csv)")
                val_rows.append({"validation_id": f"VAL-LHHS-{n_val:05d}", "observation_id": oid, "rule_applied": "cross_document",
                                 "expected_result": f"{DOCS[p]['report']} p.{x['source_page']} prints {x['amount'] // 1000:,} "
                                                    f"({x.get('extraction_method')}, {x['account_name_as_written']!r})",
                                 "observed_result": f"{f['amount'] // 1000:,} as recorded" + why,
                                 "result": "pass" if ok else "flag", "human_review_status": None if ok else "pending",
                                 "reviewer": None, "resolution": None})
            if f.get("_parts"):
                n_val += 1
                total = sum(x["amount"] for x in f["_parts"])
                val_rows.append({"validation_id": f"VAL-LHHS-{n_val:05d}", "observation_id": oid, "rule_applied": "structural",
                                 "expected_result": f"sum of the NIH Innovation Account, CURES Act lines "
                                                    f"({', '.join('p.%s %s' % (x['source_page'], x['amount_as_printed']) for x in f['_parts'])})"
                                                    f" = {total // 1000:,}",
                                 "observed_result": f"{f['amount'] // 1000:,} printed under the Title II total",
                                 "result": "pass" if total == f["amount"] else "fail",
                                 "human_review_status": None if total == f["amount"] else "pending", "reviewer": None, "resolution": None})
            if role == "headline" and stage == "Enacted" and acct in LAW_CONCEPT:
                figure = "appropriation" if acct != "ACC-HHS-HRSA-VICTF" else "administrative expenses"
                for lr in law.get((LAW_CONCEPT[acct], fy, figure), []):
                    n_val += 1
                    ok = int(lr["amount_thousands"]) * 1000 == f["amount"]
                    val_rows.append({"validation_id": f"VAL-LHHS-{n_val:05d}", "observation_id": oid,
                                     "rule_applied": "cross_document",
                                     "expected_result": f"{lr['law']} Title II (pp. {lr['title_ii_pdf_pages']}) appropriates "
                                                        f"${int(lr['amount_thousands']) * 1000:,}",
                                     "observed_result": f"{f['amount'] // 1000:,} as recorded", "result": "pass" if ok else "fail",
                                     "human_review_status": None if ok else "pending", "reviewer": None, "resolution": None})
    # Title II total: the 16-column reconciliation, one record per recorded title total
    tt = {(r["document"], int(r["fiscal_year"]), r["stage"]): r for r in csv.DictReader(open(HERE.parent / "title_ii_totals.csv"))}
    by_doc_id = {d["id"]: pkg for pkg, d in DOCS.items()}
    for o in obs_rows:
        if o["canonical_account_id"] == "ACC-HHS-TITLE-II-TOTAL" and o["component"] is None:
            r = tt.get((by_doc_id[o["source_document_id"]], o["fiscal_year"], o["stage"]))
            if r:
                n_val += 1
                val_rows.append({"validation_id": f"VAL-LHHS-{n_val:05d}", "observation_id": o["observation_id"],
                                 "rule_applied": "table_total",
                                 "expected_result": "ten agency totals (+ AHA in the requests) + General Provisions lines "
                                                    f"({r['general_provisions_thousands'] or 'none'}) - CURES Act "
                                                    f"({r['cures_act_subtracted_thousands'] or 0}) = {int(r['computed_thousands']):,}",
                                 "observed_result": f"{int(r['printed_title_ii_thousands']):,} as printed",
                                 "result": "pass" if r["reconciles"] == "yes" else "fail",
                                 "human_review_status": None, "reviewer": None, "resolution": None})

    # --- Accounts, in printed order (H.Rept. 119-696, which prints all of them)
    order = {a["id"]: i + 1 for i, a in enumerate(ACCOUNTS)}
    acct_rows = [{"canonical_account_id": a["id"], "canonical_name": a["name"], "agency": a["agency"], "bureau": a["bureau"],
                  "treasury_account_symbol": None, "status": a.get("status", "active"),
                  "effective_start": a.get("start") or f"{first_fy[a['id']] - 1}-10-01", "effective_end": None,
                  "historical_names": None, "historical_identifiers": None, "fund_type": a["fund"], "subcommittee": SUB,
                  "notes": a["notes"], "title": "Title II", "display_order": order[a["id"]]} for a in ACCOUNTS]

    # --- Confirmed Absences: AHA (proposed) at its non-request cells; any headline no document prints
    ca = []

    def add_ca(acct, fy, stage, amount_type, doc, evidence):
        ca.append({"confirmed_absence_id": f"CA-LHHS-{len(ca) + 1:04d}", "canonical_account_id": acct, "fiscal_year": fy,
                   "stage": stage, "amount_type": amount_type, "component": None, "source_document_id": DOCS[doc]["id"],
                   "evidence": evidence, "confirmed_date": TODAY})
    aha = "ACC-HHS-AHA-TOTAL"
    add_ca(aha, 2026, "House Reported", "budget authority", "CRPT-119hrpt271",
           "H.Rept. 119-271 p.334 prints the Administration for a Healthy America rows (a proposal of the FY2026 request, "
           "footnote 1/: funding 'not displayed under other HHS accounts below') with '---' in the Bill column for "
           "'Administration for a Healthy America, Discretionary 1/', 'Total, Administration for a Healthy America' and its "
           "program-level total; the Bill vs. Request column prints -574,803. Read by vision and by hand from the page image.")
    add_ca(aha, 2026, "Senate Reported", "budget authority", "CRPT-119srpt55",
           "S.Rept. 119-55's Title II table (pp. 426-442, text layer) prints no Administration for a Healthy America row: "
           "Title II opens with HRSA's Health Centers; no 'Healthy America' text anywhere in the table.")
    add_ca(aha, 2026, "Enacted", "budget authority", JES,
           "The FY2026 JES Division B table (Congressional Record H1633, p.281, read by hand from the page image) opens Title II "
           "with HRSA's Health Centers and prints no Administration for a Healthy America row; H.Rept. 119-696 p.365 prints "
           "'---' for AHA in its FY 2026 Enacted column; P.L. 119-75 contains no 'Healthy America' heading or text.")
    add_ca(aha, 2027, "House Reported", "budget authority", "CRPT-119hrpt696",
           "H.Rept. 119-696 p.365 prints 'Administration for a Healthy America, Discretionary 1/' and its program-level total "
           "with '---' in the Committee Recommendation column (FY 2027 Request 427,803). Read by vision; the row verified by "
           "hand against the page image.")
    covered = defaultdict(set)
    for pkg, d in DOCS.items():
        cells = {(d["fy"], d["stage"])}
        for e in (d["also"] or "").split(";"):
            m = re.fullmatch(r"FY(\d{4}) (.+)", e.strip())
            if m:
                cells.add((int(m.group(1)), m.group(2)))
        for c in cells:
            covered[c].add(pkg)
    have = {(o["canonical_account_id"], o["fiscal_year"], o["stage"]) for o in obs_rows if o["component"] is None
            and o["amount_type"] == "budget authority"}
    for a in ACCOUNTS:
        if a.get("status") == "proposed":
            continue
        for (fy, stage), pkgs in sorted(covered.items()):
            if (a["id"], fy, stage) in have:
                continue
            docs_ = sorted(pkgs, key=lambda p: DOCS[p]["date"])
            printed = sorted({f"{f['account_name_as_written']!r} {f['amount_as_printed']} (p.{f['source_page']})"
                              for p in docs_ for f in figs.get(p, []) if f["fiscal_year"] == fy and f["stage"] == stage
                              and matches(a.get("views", []), f["account_name_as_written"])})
            add_ca(a["id"], fy, stage, "budget authority", docs_[0],
                   f"No row for this account's headline figure ({' or '.join(repr(fold(x.strip('^$'))) for x in a['head'])}) in "
                   + "; ".join(f"{DOCS[p]['report']} Title II table pp. {DOCS[p]['pages']}" for p in docs_)
                   + f" for FY{fy} {stage}."
                   + (" The document prints only " + "; ".join(printed) + " -- not recorded: each is another scope "
                      "of the headline (component kind 'view'), which needs the headline it is a view of." if printed else ""))

    # --- Account Relationships (decision 3: consolidated only with explicit primary-document language)
    rel = [
        ("ACC-HHS-HRSA-TOTAL", "consolidated", 2026, 0.9,
         "Proposed, not enacted. FY2026 Budget Appendix (BUDGET-2026-APP) pp. 332, 338, 339, 346: 'In 2026 HRSA will be "
         "reorganized into the Agency [p.339: Administration] for a Healthy America to improve coordination of health resources "
         "for Americans.' Repeated for 2027 in the FY2027 Appendix (BUDGET-2027-APP) pp. 434, 439, 449. P.L. 119-75 keeps HRSA."),
        ("ACC-HHS-SAMHSA-TOTAL", "consolidated", 2026, 0.9,
         "Proposed, not enacted. FY2026 Budget Appendix p.360: 'In 2026 SAMHSA will be reorganized into the Agency for a "
         "Healthy America to improve coordination of health resources for Americans.' FY2027 Appendix p.464 repeats it for 2027. "
         "P.L. 119-75 keeps SAMHSA."),
        ("ACC-HHS-NIH-TOTAL", "uncertain", 2026, 0.5,
         "Part of NIH only, and only in the FY2026 request: FY2026 Budget Appendix p.357: 'relocate the National Institute of "
         "Environmental Health Sciences into the new Administration for a Healthy America'. The FY2027 Appendix p.461 instead "
         "proposes relocating NIEHS into CDC. Not a consolidation of the NIH total."),
        ("ACC-HHS-CDC-TOTAL", "uncertain", 2026, 0.4,
         "Part of CDC only: FY2026 Budget Appendix p.337, World Trade Center Health Program: 'This account is moving to the "
         "Administration for a Healthy America, in alignment with the HHS reorganization.' Not a consolidation of the CDC total."),
    ]
    rel_rows = [{"relationship_id": f"REL-LHHS-{i + 1:04d}", "from_account_id": f, "to_account_id": aha,
                 "relationship_type": t, "effective_fiscal_year": y, "evidence": ev, "confidence": c, "human_reviewed": "FALSE"}
                for i, (f, t, y, c, ev) in enumerate(rel)]

    # --- Source Documents, Bill Report Reference, Component
    sd_rows = [{"document_id": d["id"], "source_agency": d["agency"], "url_or_identifier": d["url"],
                "document_type": d["type"], "congress_session": d["cs"], "fiscal_year": d["fy"],
                "publication_date": f"{d['date']} 00:00:00", "stage": d["stage"], "retrieval_timestamp": RETRIEVED,
                "source_page": d["pages"], "also_covers": d["also"] or None} for d in DOCS.values()]
    brr_rows = []
    for (fy, stage), (bill, report, bu, ru) in sorted(BRR.items()):
        if (fy, stage) not in used_cells and not any(c["fiscal_year"] == fy and c["stage"] == stage for c in ca):
            continue
        brr_rows.append({"reference_id": f"BR-LHHS-FY{fy}-{BRR_ID[stage]}", "subcommittee": SUB, "fiscal_year": fy,
                         "stage": stage, "bill_id": bill, "report_id": report, "bill_url": bu, "report_jes_url": ru,
                         "lookup_key": f"{SUB}-{fy}-{stage}"})
    comp_rows = [{"component": c, "kind": k, "description": d} for c, k, d in A.COMPONENT_KINDS]

    # --- Held back: H.Rept. 119-271's "FY 2025 Estimate" column (decision 1's condition not met)
    held = []
    for f in figs.get("CRPT-119hrpt271", []):
        if f["column_header"] != "FY 2025 Estimate" or f["amount"] is None:
            continue
        for a in ACCOUNTS:
            if matches(a["head"], f["account_name_as_written"]) and not f["is_memo"]:
                held.append({"canonical_account_id": a["id"], "fiscal_year": 2025, "proposed_stage": "Enacted",
                             "amount": f["amount"], "amount_as_printed": f["amount_as_printed"],
                             "source_document_id": DOCS["CRPT-119hrpt271"]["id"], "source_page": f["source_page"],
                             "label_as_printed": f["account_name_as_written"], "extraction_method": f["extraction_method"],
                             "why_held": "H.Rept. 119-271 defines this column as 'FY 2025 Operating Plans and other available "
                                         "information' (p.319 text; p.320 table footnote *), not the CR level; the column prints "
                                         "'---' for many lines it regroups as 'Other ...'. The vehicle is confirmed: P.L. 119-4 "
                                         "sec. 1101(a)(8) continues the FY2024 LHHS Act (division D of P.L. 118-47) at FY2024 levels."})
                break

    for name, rows in (("account", acct_rows), ("observation", obs_rows), ("source_document", sd_rows),
                       ("bill_report_reference", brr_rows), ("historical_name", []), ("confirmed_absence", ca),
                       ("account_relationship", rel_rows), ("validation_record", val_rows), ("component", comp_rows),
                       ("held_fy2025_estimate", held)):
        write(HERE / f"lhhs_{name}.csv", rows, COLUMNS.get(name))
    print(f"{len(acct_rows)} accounts, {len(obs_rows)} observations, {len(ca)} confirmed absences, {len(rel_rows)} "
          f"relationships, {len(val_rows)} validation records, {len(sd_rows)} source documents, {len(brr_rows)} bill/report "
          f"references, {len(held)} held back", file=sys.stderr)


COLUMNS = {"historical_name": ["historical_name_id", "canonical_account_id", "former_name", "evidence", "approved_date",
                               "confidence", "human_reviewed"]}


def write(path, rows, columns=None):
    cols = columns or (list(rows[0]) if rows else [])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
