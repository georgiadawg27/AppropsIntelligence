"""
appendix_attributes: each account's Budget Appendix attributes (accounting views, phase 2; accounting.py has the
schema, reference/ACCOUNTING_RULES.md the rules).

    python scripts/appendix_attributes.py --extract   # read the Appendix chapter PDFs (document_store/appendix/,
                                                      # downloaded from govinfo when missing) into the committed
                                                      # schedule extract (reference/review/accounting/appendix_schedules.json)
    python scripts/appendix_attributes.py             # what it would record; coverage
    python scripts/appendix_attributes.py --write     # write data/staged.json's account_attributes and
                                                      # account_attribute_notes, the Appendix source documents, and
                                                      # the review CSVs (reference/review/accounting/appendix_*.csv)
    python scripts/appendix_attributes.py --ci        # fail when data/staged.json is not up to date with this

Text only: every value is read from the PDF's text layer (no page image). The extract keeps, for each account we
map (appendix_map.csv), its Program and Financing schedule: the identification code, the printed pages, each
program-activity line and the budget authority lines by BEA category, column by column.

For each account and fiscal year we hold a figure for (FY2017-FY2027):

  enacted   (Enacted, House and Senate stages) FY N from the FY N+2 Appendix, whose first column is FY N's actuals,
            where that volume exists (FY2017-FY2025); otherwise FY N's own Appendix (FY2026, FY2027).
  request   (the President's Budget stage) FY N from FY N's own Appendix, its budget-year column: what the request
            proposed. Recorded only where it differs from the enacted value, or where there is no enacted value (a
            proposed-only account). Where the request moves the activity to another account (the FY2026 and FY2027
            Administration for a Healthy America), the moved row of appendix_map.csv names the new account and the
            words that show it.

  account_id_code     the schedule's identification code, e.g. 075-0350-0-1-550
  budget_subfunction  its last three digits ('999': the account spans more than one subfunction)
  fund_type           its fund digit: 1 general, 2 special, 3/4 revolving, 7/8 trust
  budget_class        the schedule's budget authority, gross, in the year's column: line 4000 (discretionary) and
                      line 4090 (mandatory). Both non-zero: 'mixed' (the mandatory part of the headline is a
                      mandatory_portion component); neither: no class (no budget authority that year; noted).

An account that is one program activity of a larger Appendix account (a HRSA program, a CDC program, an NIH
institute) takes that account's attributes, and its activity line must be printed in the schedule; otherwise the
fields stay empty with a note. Agency and title totals get no attributes of their own. Nothing is guessed: an
account with no schedule we can find is noted, not filled.
"""

import argparse
import collections
import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import accounting as AC  # noqa: E402
import legislative_text as L  # noqa: E402

CACHE = ROOT / "document_store" / "appendix"
REVIEW = ROOT / "reference" / "review" / "accounting"
EXTRACT = REVIEW / "appendix_schedules.json"
MAP = REVIEW / "appendix_map.csv"
COVERAGE = REVIEW / "appendix_coverage.csv"
GOVINFO = "https://www.govinfo.gov/content/pkg/"

# The chapters (govinfo part files) and their printed page ranges (accounting views phase 1 report, "Phase 2 prep")
CHAPTER_NAMES = {"HHS": "Department of Health and Human Services", "COM": "Department of Commerce",
                 "DOJ": "Department of Justice", "NASA": "National Aeronautics and Space Administration",
                 "NSF": "National Science Foundation", "EOP": "Executive Office of the President"}
CHAPTERS = [
    (2017, "HHS", 443, 516, "1-11"), (2017, "COM", 185, 222, "1-6"), (2017, "DOJ", 741, 786, "1-15"),
    (2017, "NASA", 1183, 1194, "1-26"), (2017, "NSF", 1195, 1200, "1-27"), (2017, "EOP", 1157, 1168, "1-24"),
    (2018, "HHS", 415, 482, "1-11"), (2018, "COM", 179, 212, "1-6"), (2018, "DOJ", 683, 726, "1-15"),
    (2018, "NASA", 1073, 1082, "1-26"), (2018, "NSF", 1083, 1088, "1-27"), (2018, "EOP", 1047, 1058, "1-24"),
    (2019, "HHS", 415, 486, "1-11"), (2019, "COM", 179, 212, "1-6"), (2019, "DOJ", 687, 732, "1-15"),
    (2019, "NASA", 1081, 1090, "1-26"), (2019, "NSF", 1091, 1096, "1-27"), (2019, "EOP", 1053, 1064, "1-24"),
    (2020, "HHS", 417, 488, "1-11"), (2020, "COM", 175, 210, "1-6"), (2020, "DOJ", 685, 728, "1-15"),
    (2020, "NASA", 1097, 1106, "1-26"), (2020, "NSF", 1107, 1112, "1-27"), (2020, "EOP", 1057, 1070, "1-24"),
    (2021, "HHS", 431, 504, "1-11"), (2021, "COM", 187, 224, "1-6"), (2021, "DOJ", 717, 762, "1-15"),
    (2021, "NASA", 1177, 1186, "1-26"), (2021, "NSF", 1187, 1192, "1-27"), (2021, "EOP", 1137, 1150, "1-24"),
    (2022, "HHS", 433, 506, "1-10"), (2022, "COM", 193, 232, "1-6"), (2022, "DOJ", 727, 770, "1-14"),
    (2022, "NASA", 1195, 1204, "1-25"), (2022, "NSF", 1205, 1210, "1-26"), (2022, "EOP", 1163, 1176, "1-23"),
    (2023, "HHS", 423, 496, "1-10"), (2023, "COM", 191, 230, "1-6"), (2023, "DOJ", 715, 760, "1-14"),
    (2023, "NASA", 1163, 1172, "1-25"), (2023, "NSF", 1173, 1178, "1-26"), (2023, "EOP", 1131, 1144, "1-23"),
    (2024, "HHS", 409, 486, "1-10"), (2024, "COM", 183, 224, "1-6"), (2024, "DOJ", 695, 736, "1-14"),
    (2024, "NASA", 1131, 1140, "1-25"), (2024, "NSF", 1141, 1146, "1-26"), (2024, "EOP", 1101, 1114, "1-23"),
    (2025, "HHS", 391, 466, "1-10"), (2025, "COM", 175, 212, "1-6"), (2025, "DOJ", 667, 710, "1-14"),
    (2025, "NASA", 1073, 1082, "1-25"), (2025, "NSF", 1083, 1088, "1-26"), (2025, "EOP", 1043, 1056, "1-23"),
    (2026, "HHS", 325, 400, "2-10"), (2026, "COM", 187, 224, "2-6"), (2026, "DOJ", 595, 636, "2-14"),
    (2026, "NASA", 997, 1006, "2-25"), (2026, "NSF", 1007, 1012, "2-26"), (2026, "EOP", 965, 980, "2-23"),
    (2027, "HHS", 427, 502, "2-9"), (2027, "COM", 195, 234, "2-6"), (2027, "DOJ", 703, 744, "2-13"),
    (2027, "NASA", 1113, 1122, "2-25"), (2027, "NSF", 1123, 1128, "2-26"), (2027, "EOP", 1081, 1096, "2-23"),
]
# govinfo's dateIssued for each volume (the FY2026 volume is the Technical Supplement's Appendix)
PUBLISHED = {2017: "2016-02-09", 2018: "2017-05-23", 2019: "2018-02-12", 2020: "2019-03-18", 2021: "2020-02-10",
             2022: "2021-05-28", 2023: "2022-03-28", 2024: "2023-03-13", 2025: "2024-03-11", 2026: "2025-05-30",
             2027: "2026-04-03"}
SUBCOMMITTEE_CHAPTERS = {"LHHS": ("HHS",), "CJS": ("COM", "DOJ", "NASA", "NSF", "EOP")}
ENACTED_STAGES = ("Enacted", "House Reported", "Senate Reported")
REQUEST = "President's Budget"
CODE = re.compile(r"Identification code (\d{3})[–-](\d{4})[–-](\d)[–-](\d)[–-](\d{3})")
YEAR = re.compile(r"^(\d{4})$")
MANDATORY_ACTIVITY = r"mandatory|\bACA\b"
NUMBER = re.compile(r"^–?\d[\d,]*$|^\.{3,}$")


def url(fy, part):
    return f"{GOVINFO}BUDGET-{fy}-APP/pdf/BUDGET-{fy}-APP-{part}.pdf"


def document_id(fy, chapter):
    return f"SRC-BUDGET-APP-FY{fy}-{chapter}"


def fetch(fy, part):
    dest = CACHE / f"BUDGET-{fy}-APP-{part}.pdf"
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url(fy, part), headers={"User-Agent": "approps-intelligence appendix"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    return dest


# ------------------------------------------------------------------------------------------------- extract

def column_lines(page):
    """A two-column Appendix page -> [(column, [words of one printed line, left to right])], column by column. A
    word is (text, x0, x1), x measured from the column's left margin (a schedule runs on from one column into the
    next without repeating its header, so its year columns are placed the same way in both)."""
    import pymupdf  # noqa: F401  (imported here: only --extract needs it)
    width = page.rect.width
    out = []
    words = page.get_text("words")
    for col, (lo, hi) in enumerate([(0, width / 2), (width / 2, width)]):
        rows = collections.defaultdict(list)
        mine = [w for w in words if lo <= w[0] < hi]
        margin = min([w[0] for w in mine] or [0])
        for w in mine:
            rows[round(w[1] / 2.5)].append((w[4], w[0] - margin, w[2] - margin))
        for k in sorted(rows):
            out.append((col, sorted(rows[k], key=lambda w: w[1])))
    return out


def value(t):
    if t.startswith("."):
        return 0
    return int(t.replace(",", "").replace("–", "-"))


def read_values(words, cols):
    """The numbers at the end of a printed line, each placed in the year column its right edge sits under.
    -> (label words, {column index: value}) or None when a number sits under no column."""
    vals = {}
    words = list(words)
    while words and NUMBER.match(words[-1][0]) and len(vals) < len(cols):
        t, x0, x1 = words.pop()
        i = min(range(len(cols)), key=lambda j: abs(cols[j] - x1))
        if abs(cols[i] - x1) > 40 or i in vals:
            return None
        vals[i] = value(t)
    return words, vals


def extract_volume(pdf, first_page, wanted):
    """-> {code: schedule} for the Program and Financing schedules of the agency-account prefixes in `wanted`."""
    import pymupdf
    doc = pymupdf.open(pdf)
    if len(doc) != 0 and first_page + len(doc) - 1 < first_page:
        raise SystemExit(f"{pdf}: no pages")
    out = {}
    cur, cols, pending = None, None, None
    recent = []
    for pno, page in enumerate(doc):
        printed = first_page + pno
        for col, words in column_lines(page):
            text = " ".join(w[0] for w in words)
            m = CODE.search(text)
            if m:
                code = "-".join(m.groups())
                ctx = " ".join(recent[-3:])
                cur = None
                if "Program and Financing" in ctx and "-".join(m.groups()[:2]) in wanted:
                    cur = code
                    after = [w for w in words if w[1] > 0 and YEAR.match(w[0])]
                    years = [w[0] for w in after]
                    # a year column's right edge: the right edge of the word after the year ('actual' / 'est.')
                    edges = []
                    for i, w in enumerate(words):
                        if YEAR.match(w[0]) and i + 1 < len(words) and words[i + 1][0] in ("actual", "est."):
                            edges.append(words[i + 1][2])
                    cols = edges
                    s = out.setdefault(code, {"code": code, "pages": [], "years": years, "lines": {},
                                              "activities": []})
                    if printed not in s["pages"]:
                        s["pages"].append(printed)
                pending = None
                recent.append(text)
                continue
            recent.append(text)
            if not cur:
                continue
            n = re.match(r"(\d{4})$", words[0][0]) if words else None
            if n:
                got = read_values(words[1:], cols)
                if got is None:
                    pending = None
                    continue
                label, vals = got
                if not vals:
                    pending = (n.group(1), " ".join(w[0] for w in label))     # a label wrapped onto the next line
                    continue
                record(out[cur], n.group(1), " ".join(w[0] for w in label), vals, printed)
                pending = None
            elif pending:
                got = read_values(words, cols)
                if got and got[1]:
                    record(out[cur], pending[0], pending[1] + " " + " ".join(w[0] for w in got[0]), got[1], printed)
                    pending = None
    return out


def record(s, line, label, vals, page):
    label = re.sub(r"\s*\.{2,}.*$", "", label).strip(" .")
    vals = [vals.get(i) for i in range(len(s["years"]))]
    if line < "0900":
        s["activities"].append({"line": line, "label": label, "values": vals, "page": page})
    s["lines"].setdefault(line, {"label": label, "values": vals, "page": page})


def extract():
    wanted = collections.defaultdict(set)
    for r in read_map():
        for code in (r["appendix_account"],):
            wanted[r["chapter"]].add(code)
    out = {}
    for fy, ch, first, last, part in CHAPTERS:
        pdf = fetch(fy, part)
        vol = extract_volume(pdf, first, wanted[ch])
        out[f"{fy}-{ch}"] = {"fiscal_year": fy, "chapter": ch, "pages": f"{first}-{last}", "url": url(fy, part),
                             "schedules": vol}
    EXTRACT.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"{EXTRACT.relative_to(ROOT)}: {sum(len(v['schedules']) for v in out.values())} schedules from "
          f"{len(out)} chapters (text layer; no page image)")


# --------------------------------------------------------------------------------------------- attributes

def read_map():
    with open(MAP) as f:
        return [r for r in csv.DictReader(f) if not r["canonical_account_id"].startswith("#")]


def map_rows(rows, account, fy, basis):
    """The map rows for an account in a fiscal year and basis (a 'moved' row is the request's own account)."""
    out = []
    for r in rows:
        if r["canonical_account_id"] != account:
            continue
        if r["first_fy"] and fy < int(r["first_fy"]) or r["last_fy"] and fy > int(r["last_fy"]):
            continue
        if r["basis"] not in ("both", basis):
            continue
        out.append(r)
    return out


def schedule(vol, prefix, transmittals=("0",)):
    for t in transmittals:
        for code, s in vol["schedules"].items():
            if code.startswith(prefix + "-") and code.split("-")[2] == t:
                return s
    return None


def year_value(s, line, fy):
    ln = s["lines"].get(line)
    if not ln or str(fy) not in s["years"]:
        return None, None
    v = ln["values"][s["years"].index(str(fy))]
    return v, ln


def read_attributes(extract_, row, fy, volume_fy, transmittals, basis):
    """-> (dict of attribute -> (value, location, evidence), note). The note says why a field stays empty."""
    vol = extract_.get(f"{volume_fy}-{row['chapter']}")
    if vol is None:
        return {}, f"no FY{volume_fy} Appendix chapter"
    s = schedule(vol, row["appendix_account"], transmittals)
    if s is None:
        return {}, (f"no Program and Financing schedule for {row['appendix_account']} ({row['appendix_title']}) "
                    f"in the FY{volume_fy} Appendix")
    if str(fy) not in s["years"]:
        return {}, f"the FY{volume_fy} schedule {s['code']} prints no FY{fy} column"
    col = s["years"].index(str(fy))
    kind = "actual" if volume_fy >= fy + 2 else "est." if volume_fy == fy + 1 else "est. (request)"
    act_txt, zero_activity, hits = "", "", []
    if row["activity"]:
        hits = [a for a in s["activities"] if re.search(row["activity"], a["label"], re.I)]
        if not hits:
            return {}, (f"the FY{volume_fy} schedule {s['code']} ({row['appendix_title']}) prints no program "
                        f"activity line matching '{row['activity']}'")
        a = hits[0]
        act_txt = f"; program activity line {a['line']} '{a['label']}' (p.{a['page']})"
        if basis == "request" and not any(h["values"][col] for h in hits):
            zero_activity = (f"the FY{fy} request prints no obligations for '{a['label']}' in {s['code']} "
                             f"(line {a['line']}, p.{a['page']})")
    code = s["code"]
    m = AC.ACCOUNT_ID_CODE.fullmatch(code)
    head = (f"FY{volume_fy} Budget Appendix, {row['appendix_title']}, Program and Financing schedule, "
            f"Identification code {code}{act_txt}")
    loc = f"p.{s['pages'][0]}"
    out = {"account_id_code": (code, loc, head),
           "budget_subfunction": (m.group(5), loc, head + f": the code's last three digits, {m.group(5)}"
                                  + (" (the account spans more than one subfunction)" if m.group(5) == "999" else "")),
           "fund_type": (AC.FUND_DIGIT[m.group(4)], loc, head + f": fund digit {m.group(4)}")}
    if zero_activity:
        # The account is unchanged; only this activity is unfunded in the request (e.g. CDC programs shown under
        # CDC-Wide Activities). The class of an activity with no request is not read; it follows enacted.
        return out, zero_activity + "; account code read, budget_class follows the enacted value"
    note = ""
    if basis == "enacted" and volume_fy == fy and str(fy - 1) in s["years"]:
        # FY2027 has no actuals yet and its own column is the request, which is not what the House or Senate bill
        # funds (the FY2027 request moves whole agencies). The class is read from the volume's FY2026 est. column,
        # the latest enacted level; the code, fund and subfunction are the schedule's own.
        got = class_in_column(s, fy - 1, "est.", hits)
        if got:
            note = (f"{code}: budget_class read from the FY{volume_fy} Appendix's {fy - 1} est. column (the latest "
                    f"enacted level), as the {fy} column is the request")
            got = (got[0], got[1], got[2] + f" -- read from the {fy - 1} column: the {fy} column is the request")
        else:
            got = class_in_column(s, fy, kind, hits)
    else:
        got = class_in_column(s, fy, kind, hits)
    if got is None:
        return out, f"{code}: no budget authority in the {fy} {kind} column (lines 4000 and 4090 blank or absent)"
    cls, pages, ba = got
    out["budget_class"] = (cls, ", ".join(f"p.{p}" for p in pages) or loc, head + "; " + ba)
    return out, note


def class_in_column(s, fy, kind, hits):
    """budget_class from one year column of a schedule -> (class, [pages], evidence), or None with no budget
    authority in it."""
    col = s["years"].index(str(fy))
    d, dl = year_value(s, "4000", fy)
    mnd, ml = year_value(s, "4090", fy)
    d, mnd = d or 0, mnd or 0
    pages = sorted({x["page"] for x in (dl, ml) if x})
    ba = (f"{fy} {kind} column: line 4000 discretionary budget authority, gross {d:,}; line 4090 mandatory budget "
          f"authority, gross {mnd:,} ($ millions)" + ("" if dl or ml else " (neither line printed)"))
    if not d and not mnd:
        # Rescission-only accounts (e.g. the Nonrecurring Expenses Fund) may print no 4000/4090 line; read the
        # budget authority lines themselves: 11xx/13xx/15xx/17xx discretionary, 12xx/14xx/16xx/18xx mandatory.
        parts = {"discretionary": [], "mandatory": []}
        for ln, rec in sorted(s["lines"].items()):
            if ln.isdigit() and 1100 <= int(ln) <= 1899 and ln[2:] not in ("50", "60", "70", "99") and ln[2] != "9":
                v = rec["values"][col] if col < len(rec["values"]) else None
                if v:
                    parts["discretionary" if int(ln[1]) % 2 else "mandatory"].append((ln, v, rec))
        d = sum(v for _, v, _ in parts["discretionary"])
        mnd = sum(v for _, v, _ in parts["mandatory"])
        used = parts["discretionary"] + parts["mandatory"]
        if used:
            pages = sorted({rec["page"] for _, _, rec in used})
            ba = (f"{fy} {kind} column: no line 4000/4090 printed; budget authority lines "
                  + "; ".join(f"{ln} '{rec['label']}' {v:,}" for ln, v, rec in used) + " ($ millions)")
    if d and mnd:
        cls = "mixed"
    elif d:
        cls = "discretionary"
    elif mnd:
        cls = "mandatory"
    else:
        return None
    if cls == "mixed" and hits:
        # The schedule is mixed; where it labels its mandatory program activities, the class of the canonical
        # account is the class of the activity lines it maps to.
        mand = lambda h: re.search(MANDATORY_ACTIVITY, h["label"], re.I)  # noqa: E731
        if any(mand(x) for x in s["activities"]):
            live = [h for h in hits if h["values"][col]]
            kinds = {"mandatory" if mand(h) else "discretionary" for h in live}
            if len(kinds) == 1:
                cls = kinds.pop()
                ba += (f"; the schedule labels its mandatory program activities, and the mapped activity line(s) "
                       + ", ".join(f"{h['line']} '{h['label']}'" for h in live) + f" are {cls}")
        else:
            ba += "; the schedule does not split budget authority by program activity"
    return cls, pages, ba


def enacted_volume(fy):
    """Actuals from FY N+2 where printed. FY2026 has no FY2028 volume: its enacted level is the FY2027 volume's
    2026 est. column (FY2026's own volume would print the request). FY2027 falls back to its own volume."""
    return fy + 2 if fy + 2 <= AC.LAST_FY else fy + 1 if fy + 1 <= AC.LAST_FY else fy


def held(data):
    """-> {(account, fy): {stages}} for every account (not totals) we hold a figure for."""
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    out = collections.defaultdict(set)
    for o in data["observations"]:
        a = acc[o["canonical_account_id"]]
        if a.get("total_scope") or o.get("component") or o["verification_status"] == "superseded":
            continue
        fy = int(o["fiscal_year"])
        if AC.FIRST_FY <= fy <= AC.LAST_FY:
            out[(a["canonical_account_id"], fy)].add(o["stage"])
    return out


def build(data, extract_, rows):
    """-> (attribute records without IDs, notes, coverage rows)"""
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    recs, notes, cover = [], [], []
    for (account, fy), stages in sorted(held(data).items()):
        a = acc[account]
        values = {}
        for basis in ("enacted", "request"):
            if basis == "enacted" and not stages & set(ENACTED_STAGES):
                continue
            if basis == "request" and REQUEST not in stages:
                continue
            mrows = map_rows(rows, account, fy, basis)
            got, note = {}, ""
            if not mrows:
                note = "no Appendix account mapped (appendix_map.csv): " + (
                    next((r["note"] for r in rows if r["canonical_account_id"] == account and r["note"]), "") or
                    "none found")
            if basis == "request":
                # the request's own account (a proposed or moved one) before the enacted structure
                mrows = sorted(mrows, key=lambda r: r["status"] not in ("proposed", "moved", "none"))
            fallback = None
            for r in mrows:
                if r["status"] == "none":
                    note = r["note"]
                    if basis == "request":
                        break
                    continue
                if basis == "enacted":
                    vfy = enacted_volume(fy)
                    transmittals = ("0",) if r["status"] != "proposed" else ("0", "2", "4")
                else:
                    vfy = fy
                    transmittals = ("0",) if r["status"] not in ("proposed", "moved") else ("0", "2", "4")
                got, note = read_attributes(extract_, r, fy, vfy, transmittals, basis)
                if got:
                    got = {k: v + (r, vfy) for k, v in got.items()}
                    if basis == "request" and "budget_class" not in got and fallback is None:
                        fallback, got = (got, note), {}   # an unfunded activity: try the request's other accounts
                        continue
                    break
            if not got and fallback:
                got, note = fallback
            values[basis] = (got, note)
        enacted = values.get("enacted", ({}, ""))[0]
        for basis, (got, note) in values.items():
            if basis == "request" and enacted:
                diff = {k: v for k, v in got.items() if k not in enacted or enacted[k][0] != v[0]}
                if not got and note:
                    cover.append(row(account, a, fy, basis, {}, "empty", note))
                    notes.append({"canonical_account_id": account, "fiscal_year": fy, "stage_basis": basis,
                                  "note": note + " -- the President's Budget stage uses the enacted values"})
                    continue
                got = diff
                if not diff:
                    cover.append(row(account, a, fy, basis, enacted, "same as enacted", note))
                    if note:
                        notes.append({"canonical_account_id": account, "fiscal_year": fy, "stage_basis": basis,
                                      "note": note})
                    continue
            for attr, (v, loc, ev, r, vfy) in got.items():
                recs.append({"canonical_account_id": account, "fiscal_year": fy, "attribute": attr, "value": v,
                             "source_document_id": document_id(vfy, r["chapter"]), "source_location": loc,
                             "evidence": ev, "stage_basis": basis})
            if basis == "request" and enacted:
                status = "differs from enacted (" + ", ".join(sorted(got)) + ")"
            else:
                status = ("mixed" if got.get("budget_class", ("",))[0] == "mixed" else
                          "filled" if len(got) == 4 else "partly filled" if got else "empty")
            cover.append(row(account, a, fy, basis, got, status, note))
            if note:
                notes.append({"canonical_account_id": account, "fiscal_year": fy, "stage_basis": basis, "note": note})
    return recs, notes, cover


def row(account, a, fy, basis, got, status, note):
    g = lambda k: (got.get(k) or ("",))[0]  # noqa: E731
    return {"canonical_account_id": account, "subcommittee": a["subcommittee"], "fiscal_year": fy,
            "stage_basis": basis, "status": status, "account_id_code": g("account_id_code"),
            "budget_class": g("budget_class"), "fund_type": g("fund_type"), "budget_subfunction": g("budget_subfunction"),
            "source": (document_id(got["account_id_code"][4], got["account_id_code"][3]["chapter"]) + " "
                       + got["account_id_code"][1]) if got.get("account_id_code") else "",
            "note": note}


def key(r):
    return (r["canonical_account_id"], int(r["fiscal_year"]), r["attribute"], r["stage_basis"])


def number(i):
    return int(i.rsplit("-", 1)[1])


def assign_ids(data, recs):
    """Keep the ID of every record whose identity is unchanged; new ones take the next number per subcommittee. A
    record no longer produced is retired (account_attributes_retired), never reused."""
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    old = {key(r): r["account_attribute_id"] for r in data.get("account_attributes", [])}
    used = set(old.values()) | set(data.get("account_attributes_retired", []))
    nxt = {"CJS": 1 + max([number(i) for i in used if not i.startswith("AAT-LHHS-")] or [0]),
           "LHHS": 1 + max([number(i) for i in used if i.startswith("AAT-LHHS-")] or [0])}
    out = []
    for r in recs:
        k = key(r)
        if k in old:
            rid = old[k]
        else:
            sc = acc[r["canonical_account_id"]]["subcommittee"]
            rid = f"AAT-LHHS-{nxt['LHHS']:04d}" if sc == "LHHS" else f"AAT-{nxt['CJS']:04d}"
            nxt["LHHS" if sc == "LHHS" else "CJS"] += 1
        out.append({c: (rid if c == "account_attribute_id" else r[c]) for c in AC.ATTRIBUTE_COLUMNS})
    keep = {r["account_attribute_id"] for r in out}
    retired = sorted(set(data.get("account_attributes_retired", [])) | (set(old.values()) - keep))
    out.sort(key=lambda r: (r["account_attribute_id"].startswith("AAT-LHHS-") is False, number(r["account_attribute_id"])))
    return out, retired


def source_docs(data, recs):
    """The Appendix chapters cited, as source documents (added once; never changed after)."""
    have = {d["document_id"] for d in data["source_docs"]}
    cited = {r["source_document_id"] for r in recs}
    out = []
    for fy, ch, first, last, part in CHAPTERS:
        did = document_id(fy, ch)
        if did in have or did not in cited:
            continue
        out.append({"document_id": did, "source_agency": "Office of Management and Budget",
                    "url_or_identifier": url(fy, part), "document_type": "budget_appendix", "congress_session": "",
                    "fiscal_year": fy, "publication_date": PUBLISHED[fy], "stage": REQUEST,
                    "retrieval_timestamp": "2026-10-12", "source_page": f"{first}-{last}", "also_covers": "",
                    "notes": f"Budget of the U.S. Government, FY{fy}, Appendix: the {CHAPTER_NAMES[ch]} chapter "
                             f"(govinfo part file {part}). Account attributes (accounting views, phase 2) cite its "
                             "printed page numbers; the chapter prints actuals for FY{} and estimates for FY{} and "
                             "FY{}.".format(fy - 2, fy - 1, fy)})
    return out


def summary(recs, cover):
    lines = ["## appendix_attributes", ""]
    status = lambda r: "differs from enacted" if r["status"].startswith("differs") else r["status"]  # noqa: E731
    c = collections.Counter((r["subcommittee"], r["stage_basis"], status(r)) for r in cover)
    cols = ("filled", "mixed", "partly filled", "same as enacted", "differs from enacted", "empty")
    lines += ["| subcommittee | basis | " + " | ".join(cols) + " |", "|---" * (len(cols) + 2) + "|"]
    for sc in ("LHHS", "CJS"):
        for b in ("enacted", "request"):
            lines.append(f"| {sc} | {b} | " + " | ".join(str(c[(sc, b, s)]) for s in cols) + " |")
    lines += ["", f"{len(recs)} attribute records"]
    return "\n".join(lines)


def write_csv(path, rows, cols):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ci", action="store_true")
    a = ap.parse_args(argv)
    if a.extract:
        extract()
    data = json.loads(L.STAGED.read_text())
    extract_ = json.loads(EXTRACT.read_text())
    recs, notes, cover = build(data, extract_, read_map())
    recs, retired = assign_ids(data, recs)
    docs = source_docs(data, recs)
    print(summary(recs, cover))
    errs = AC.attribute_errors(recs, {x["canonical_account_id"] for x in data["accounts"]},
                               {d["document_id"] for d in data["source_docs"] + docs})
    if errs:
        print("\n".join(errs[:40]))
        return 1
    stale = (recs != data.get("account_attributes", []) or notes != data.get("account_attribute_notes", [])
             or retired != data.get("account_attributes_retired", []) or bool(docs))
    if a.write:
        write_csv(COVERAGE, cover, ["canonical_account_id", "subcommittee", "fiscal_year", "stage_basis", "status",
                                    "account_id_code", "budget_class", "fund_type", "budget_subfunction", "source",
                                    "note"])
        if stale:
            data["account_attributes"] = recs
            data["account_attribute_notes"] = notes
            data["account_attributes_retired"] = retired
            data["source_docs"] += docs
            L.STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
            print("data/staged.json updated")
    if a.ci and stale:
        print("data/staged.json is not up to date with appendix_attributes -- run python scripts/appendix_attributes.py "
              "--write and commit data/staged.json")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
