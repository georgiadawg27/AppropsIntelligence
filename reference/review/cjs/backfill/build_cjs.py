"""
CJS full load: every account of Titles I, II, IV and V (and the cells of Title III not yet held), FY2017 to FY2027,
each stage from the comparative table the held Title III figures already cite. Appends to data/staged/; never
changes an existing figure, ID or status. A cell already held (same account, year, stage, amount type, component)
is left alone and listed (with the table's figure where it differs).

    python reference/review/cjs/backfill/build_cjs.py            # report only
    python reference/review/cjs/backfill/build_cjs.py --write    # append

Reads extractions/<package>.json (extract_approps.py, whole table; House reports' image-only fold-outs read by
vision). Each stage reads one column of one table (STAGES): House Reported and President's Budget from the year's
House report, Senate Reported from the Senate report, Enacted from the next year's House report's enacted column
(FY2023 from S.Rept. 118-62, FY2026 from the enacted explanatory statement).

How a line is found (assign): the tables print a title's accounts in the same order every year
(accounts_cjs.ACCOUNTS); each printed line's label, reduced to letters, is matched in full against the account
patterns, the next account in order first (so 'Salaries and expenses' after 'Minority Business Development' is
Economic and Statistical Analysis). A heading the extractor read over the line, or a printed 'Total, <agency>'
just passed, narrows the candidates to that agency's. The lines printed under an account's line are its parts:
offsetting fee collections (offsetting_collection), '(by transfer)' and '... (transfer out)' (transfer, on the
account the money moves into or out of, as the FY2027 House records do), a '(emergency)' line (supplemental), a
defense line (component defense: BIS's 'CWC enforcement (Defense)' / 'Defense function', the FBI's
'Counterintelligence and national security' -- the defense half of its Salaries and Expenses, as the Title V
lines print it). Direct-appropriation and subtotal lines are sums: checked, not recorded. Title V: each
rescission or transfer line goes to the account it names (accounts_cjs.TITLE_V_LINES). Nothing outside the
titles is read (the 'Other Appropriations' supplemental acts are listed, not recorded).

Checks per new figure: the extractor's own records on the row (table_total, structural, source_text, unit,
semantic); the agency sum (the printed 'Total, <agency>' against our accounts' lines in it); the title sum
(the printed title total against all our lines in the title); cross_document (an Enacted figure against the
Senate report's prior-year column, a request against the other chamber's request column). Statuses by
validate_approps.verification_status; routine extractor notes resolved with the triage's standard reasons.
Writes reference/review/cjs/backfill/report.csv (every account x cell: found / held / not printed),
unmatched.csv (every printed line no account took) and cross_document.csv.
"""

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "reference" / "review"))
sys.path.insert(0, str(HERE))

import accounts as A  # noqa: E402
import ids as IDS  # noqa: E402
import staged_data  # noqa: E402
import triage as T  # noqa: E402
import validate_approps as V  # noqa: E402
from accounts_cjs import (ACCOUNTS, AGENCY_HEADINGS, TITLE_III_FY, TITLE_III_PATTERNS, TITLE_V_LINES,  # noqa: E402
                          TITLE_V_ONLY, FUND_TYPES)

RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
TRIAGE_REVIEWER = "triage rules"

# fiscal year -> stage -> (source document id, extraction package)
H, S_ = "House Reported", "Senate Reported"
PB, EN = "President's Budget", "Enacted"
PKG = {
    "SRC-CRPT-114HRPT605": "CRPT-114hrpt605", "SRC-CRPT-114SRPT239": "CRPT-114srpt239",
    "SRC-CRPT-115HRPT231": "CRPT-115hrpt231", "SRC-CRPT-115SRPT139": "CRPT-115srpt139",
    "SRC-CRPT-115HRPT704": "CRPT-115hrpt704", "SRC-CRPT-115SRPT275": "CRPT-115srpt275",
    "SRC-CRPT-116HRPT101": "CRPT-116hrpt101", "SRC-CRPT-116SRPT127": "CRPT-116srpt127",
    "SRC-CRPT-116HRPT455": "CRPT-116hrpt455", "SRC-EXPL-FY2021-SENATE": "SRC-EXPL-FY2021-SENATE",
    "SRC-CRPT-117HRPT97": "CRPT-117hrpt97", "SRC-EXPL-FY2022-SENATE": "SRC-EXPL-FY2022-SENATE",
    "SRC-CRPT-117HRPT395": "CRPT-117hrpt395", "SRC-EXPL-FY2023-SENATE": "SRC-EXPL-FY2023-SENATE",
    "SRC-CRPT-118SRPT62": "CRPT-118srpt62", "SRC-EXPL-FY2024-HOUSE": "SRC-EXPL-FY2024-HOUSE",
    "SRC-CRPT-118HRPT582": "CRPT-118hrpt582", "SRC-CRPT-118SRPT198": "CRPT-118srpt198",
    "SRC-CRPT-119HRPT272": "CRPT-119hrpt272", "SRC-CRPT-119SRPT44": "CRPT-119srpt44",
    "SRC-EXPL-FY2026-PB": "SRC-EXPL-FY2026-PB", "SRC-CRPT-119HRPT652": "CRPT-119hrpt652",
}
# (fy, stage) -> source document: the documents the held Title III figures cite for the same cell
STAGES = {
    (2017, PB): "SRC-CRPT-114HRPT605", (2017, H): "SRC-CRPT-114HRPT605", (2017, S_): "SRC-CRPT-114SRPT239",
    (2017, EN): "SRC-CRPT-115HRPT231",
    (2018, PB): "SRC-CRPT-115HRPT231", (2018, H): "SRC-CRPT-115HRPT231", (2018, S_): "SRC-CRPT-115SRPT139",
    (2018, EN): "SRC-CRPT-115HRPT704",
    (2019, PB): "SRC-CRPT-115HRPT704", (2019, H): "SRC-CRPT-115HRPT704", (2019, S_): "SRC-CRPT-115SRPT275",
    (2019, EN): "SRC-CRPT-116HRPT101",
    (2020, PB): "SRC-CRPT-116HRPT101", (2020, H): "SRC-CRPT-116HRPT101", (2020, S_): "SRC-CRPT-116SRPT127",
    (2020, EN): "SRC-CRPT-116HRPT455",
    (2021, PB): "SRC-CRPT-116HRPT455", (2021, H): "SRC-CRPT-116HRPT455", (2021, S_): "SRC-EXPL-FY2021-SENATE",
    (2021, EN): "SRC-CRPT-117HRPT97",
    (2022, PB): "SRC-CRPT-117HRPT97", (2022, H): "SRC-CRPT-117HRPT97", (2022, S_): "SRC-EXPL-FY2022-SENATE",
    (2022, EN): "SRC-CRPT-117HRPT395",
    (2023, PB): "SRC-CRPT-117HRPT395", (2023, H): "SRC-CRPT-117HRPT395", (2023, S_): "SRC-EXPL-FY2023-SENATE",
    (2023, EN): "SRC-CRPT-118SRPT62",
    (2024, PB): "SRC-CRPT-118SRPT62", (2024, H): "SRC-EXPL-FY2024-HOUSE", (2024, S_): "SRC-CRPT-118SRPT62",
    (2024, EN): "SRC-CRPT-118HRPT582",
    (2025, PB): "SRC-CRPT-118HRPT582", (2025, H): "SRC-CRPT-118HRPT582", (2025, S_): "SRC-CRPT-118SRPT198",
    (2025, EN): "SRC-CRPT-119HRPT272",
    (2026, PB): "SRC-EXPL-FY2026-PB", (2026, H): "SRC-CRPT-119HRPT272", (2026, S_): "SRC-CRPT-119SRPT44",
    (2026, EN): "SRC-EXPL-FY2026-PB",
    (2027, H): "SRC-CRPT-119HRPT652",   # its table prints no request column; FY2027 PB and Senate not on file
}
CHAMBER = {H: "House", S_: "Senate", PB: "N/A", EN: "N/A"}
# the cross-document check: (fy, stage) -> the other table that prints the same cell
CROSS = {}
for (fy, st), src in STAGES.items():
    if st == EN and (fy + 1, S_) in STAGES and STAGES[(fy + 1, S_)] != src:
        CROSS[(fy, st)] = STAGES[(fy + 1, S_)]
    elif st == PB and (fy, S_) in STAGES and STAGES[(fy, S_)] != src:
        CROSS[(fy, st)] = STAGES[(fy, S_)]
    elif st == PB and (fy, H) in STAGES and STAGES[(fy, H)] != src:
        CROSS[(fy, st)] = STAGES[(fy, H)]
NEW_BRR = []    # every cell loaded has its Bill Report Reference row already

TITLE_OF = {"I": "Title I", "II": "Title II", "III": "Title III", "IV": "Title IV", "V": "Title V"}


def letters(s):
    return re.sub(r"[^a-z]", "", (s or "").lower().replace("&", "and"))


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fuzzy(key, pat):
    """OCR's misread letters: a literal pattern within 15% edit distance (at least 7 letters, as accounts.py)."""
    if re.escape(pat) != pat or len(pat) < 7:
        return False
    return lev(key, pat) <= max(1, int(0.15 * len(pat)))


EMERG = re.compile(r"\(\s*emerg\w*\s*\)?|\{\s*emergency\s*\)", re.I)
TITLE_RE = re.compile(r"TITLE ([IVX]+)")


def title_key(t):
    m = TITLE_RE.match(t or "")
    return m.group(1) if m else None


def load(pkg):
    """The table's rows in printed order: label, path, page, kind and the cells by (stage, fiscal year)."""
    x = json.loads((ROOT / "extractions" / f"{pkg}.json").read_text())
    recs = collections.defaultdict(list)
    for r in x["validation_records"]:
        recs[r["observation_id"]].append(r)
    rows = collections.OrderedDict()
    for o in sorted(x["observations"], key=lambda o: o["node_id"]):
        if not o["stage"] and letters(o.get("column_header") or "") == "finalbill":
            # the FY2026 explanatory statement's 'Final Bill' column: the enacted act (the extractor names no stage)
            o = dict(o, stage=EN)
        r = rows.setdefault(o["node_id"], {"node": o["node_id"], "label": o["account_name_as_written"].strip(),
                                           "path": o["account_path"], "segs": o["account_path"].split(" / "),
                                           "page": o["source_page"], "kind": o["row_kind"], "memo": o["is_memo"],
                                           "title": title_key(o["title"]), "cells": {}})
        if o["stage"] and o["fiscal_year"]:
            r["cells"].setdefault((o["stage"], int(o["fiscal_year"])), dict(o, _records=recs[o["observation_id"]]))
    out = list(rows.values())
    for r in out:
        r["key"] = letters(EMERG.sub("", r["label"]))
        r["emergency"] = bool(EMERG.search(r["label"])) or "emergency" in letters(r["label"])
    return x, out


TOTAL_RE = re.compile(r"^total")
TITLE_TOTAL_RE = re.compile(r"^total(appropriations)?,?title(i|ii|iii|iv|v)\b|^totaltitle(i|ii|iii|iv|v)(department|science|related|general)")


def title_total_of(key):
    m = re.match(r"^total(?:appropriations)?title(v|iv|iii|ii|i)(?=department|science|related|general|$)", key)
    return m.group(1).upper() if m else None


def bureau_of_heading(r):
    """The agency whose heading the extractor read over the line, if any."""
    for seg in reversed(r["segs"][:-1]):        # the nearest heading
        k = letters(seg)
        for b, rx in AGENCY_HEADINGS.items():
            if re.search(rx, k):
                return b
    return None


def bureau_of_total(key):
    if not key.startswith("total"):
        return None
    for b, rx in AGENCY_HEADINGS.items():
        if re.search(rx, key):
            return b
    return None


# lines printed under an account's line: (regex on the letters, what it is)
PART_LINES = [
    (r"offsetting(fee)?collections?(currentyear|prioryear)?", "offset"),
    (r"diversioncontrolfund", "offset"),
    (r"defensefunction|cwcenforcement(defense)?|ewcenforcement(defense)?|ewe?enforcement(defense)?", "defense"),
    (r"counterintelligenceandnationalsecurity", "defense"),
    (r"transferfromimmigrationexaminationsfeeaccount", "transfer_line"),
    (r"transferfromavailablebalancesofworkingcapitalfund", "transfer_line"),
    (r"directappropriation", "sum"), (r"subtotal.*", "sum"), (r"oigtotal", "sum"),
    # the account's line and its emergency line again, as one figure (FY2023): a view, not a part
    (r".*reclassification", "sum"),
    # fee lines under the U.S. Trustee System Fund: a proposed or enacted new fee, deducted like the collections
    (r"newfees.*", "offset"),
    # the spending of the collections the line above deducts (VAWA, FY2017 request)
    (r"spendingofoffsettingcollections", "offset"),
    # an amount a continuing resolution appropriated, printed under the account (P.L. 114-254, P.L. 117-180)
    (r"edwardbyrnememorialgrantspl.*|c?r?fundingp(ublic)?l(aw)?.*sec", "cr_addition"),
    # one-off lines under an account's line, each its own part component (accounts.PROPOSAL_COMPONENTS)
    (r"censushreshr.*", "census_2020"),
    (r"legislativeproposal", "request_proposal_nist_construction"),
]


# offset lines that can print positive: collections of a prior year spent this year, the spending of collections
POSITIVE_OFFSET = re.compile(r"prioryear|spendingof")


# lines that exist only where printed with a figure (a continuing resolution's amount, a one-off component)
ONE_OFF = ("cr_addition", "census_2020", "request_proposal_nist_construction")


def part_kind(key):
    for rx, k in PART_LINES:
        if re.fullmatch(rx, key):
            return k
    if "bytransfer" in key:
        return "transfer_in"
    if "transferout" in key:
        return "transfer_out"
    return None


def assign(rows, title, accts):
    """(row, account id, role) for the rows of one title, in order; role is 'head' or a part kind. Unassigned rows
    come back with account None."""
    order = [a for a in accts if a[1] == TITLE_OF[title]]
    pos = 0
    cur = None
    out = []
    for r in rows:
        k = r["key"]
        if r["kind"] in ("total", "grand_total") or title_total_of(k):
            b = bureau_of_total(k)
            if b:
                idx = [i for i, a in enumerate(order) if a[2] == b]
                if idx:
                    pos = max(pos, idx[-1] + 1)
            cur = None if b else cur
            out.append((r, None, "total"))
            continue
        pk = part_kind(k)
        if pk == "sum":
            out.append((r, None, "sum"))
            continue
        if pk and cur:
            out.append((r, cur, pk))
            continue
        if pk in ("transfer_in", "transfer_out") and title == "II" and (
                "preventionandprosecution" in k or "crimevictimsfundtransferout" in k):
            # VAWA's transfer lines printed above its own line (FY2022 Enacted column, H.Rept. 117-395)
            out.append((r, "ACC-DOJ-VAWA", pk))
            continue
        head_b = bureau_of_heading(r)
        cands = [i for i, a in enumerate(order) if any(re.fullmatch(p, k) for p in a[4])]
        if not cands:
            cands = [i for i, a in enumerate(order) if any(fuzzy(k, p) for p in a[4])]
        by_heading = False
        if head_b:
            narrowed = [i for i in cands if order[i][2] == head_b]
            if narrowed:
                cands = narrowed
                by_heading = len(narrowed) == 1
        hit = next((i for i in cands if i >= pos), None)
        if hit is None and by_heading:
            hit = cands[0]      # the agency heading printed over the line names its one account (printed out of order)
        if hit is None and cands:
            # an account earlier in the order: only when the label is that account's alone
            uniq = [i for i in cands if not any(re.fullmatch(p, k) for j, a in enumerate(order) if j != i for p in a[4])]
            hit = uniq[0] if len(uniq) == 1 else None
        if hit is not None:
            aid = order[hit][0]
            if r["emergency"] and cur == aid:
                out.append((r, aid, "emergency"))
                continue
            if r["emergency"]:
                out.append((r, aid, "emergency"))
                pos = max(pos, hit)
                cur = aid
                continue
            if r["memo"]:
                out.append((r, None, "memo"))
                continue
            out.append((r, aid, "head"))
            pos = hit if not r["emergency"] else pos
            cur = aid
            continue
        if r["memo"]:
            out.append((r, None, "memo"))
            continue
        out.append((r, None, None))
    return out


RESCISSION = re.compile(r"rescission|recission|resc?is+ion|resetss?t?i?on")


def title_v(rows):
    out = []
    last = None
    for r in rows:
        k = r["key"]
        part = re.fullmatch(r"(fees)?(non)?defense" + RESCISSION.pattern.join("()"), k)
        if r["kind"] in ("total", "grand_total") or r["memo"] and not (
                "bytransfer" in k or "transferout" in k or part):
            out.append((r, None, "total" if r["kind"] in ("total", "grand_total") else "memo"))
            continue
        name = k
        if part and len(r["segs"]) > 1:
            name = letters(r["segs"][-2]) + k
        aid = next((a for rx, a in TITLE_V_LINES if re.search(rx, name)), None)
        if part and k.startswith("fees"):
            aid = "ACC-DOJ-FBI-SE"  # '(Fees) nondefense / defense (rescission)': the FBI's fee rescission lines
        if aid is None and part:
            aid = last          # '(Fees) defense (rescission)' under the account line above it
        last = aid or last
        if not aid:
            out.append((r, None, None))
            continue
        defense = bool(re.search(r"(?<!non)defense", k))
        if RESCISSION.search(k):
            out.append((r, aid, "rescission_defense" if defense else "rescission"))
        elif "transferout" in k:
            out.append((r, aid, "transfer_out"))
        elif "bytransfer" in k:
            out.append((r, aid, "transfer_in"))
        elif r["emergency"]:
            # '<account> Sec. 5xx (emergency)': an emergency appropriation to the account the line names
            out.append((r, aid, "emergency_defense" if defense else "emergency"))
        elif all((c["amount"] or 0) <= 0 for c in r["cells"].values()) and any(
                (c["amount"] or 0) < 0 for c in r["cells"].values()):
            # a negative line naming an account, with no 'rescission' in its label: the title's rescission of it
            out.append((r, aid, "rescission_unlabelled"))
        else:
            out.append((r, None, None))
    return out


def sections(rows):
    """Split the table into its titles: a title's rows run from its first row to its printed title total. Rows
    after Title V's total (Other Appropriations, the grand total) are not in any title."""
    by = collections.defaultdict(list)
    done = set()
    for r in rows:
        t = r["title"]
        tt = title_total_of(r["key"])
        if tt:
            by[tt].append(r)
            done.add(tt)
            continue
        if not t or t in done:
            by["OTHER"].append(r)
            continue
        by[t].append(r)
    return by


ROLE = {  # role -> (amount_type, component, sign rule)
    "head": ("budget authority", ""), "defense": ("budget authority", "defense"),
    "emergency": ("supplemental", ""), "offset": ("offsetting_collection", ""), "cr_addition": ("supplemental", "supplemental_act"),
    "census_2020": ("budget authority", "census_2020"),
    "request_proposal_nist_construction": ("budget authority", "request_proposal_nist_construction"),
    "transfer_in": ("transfer", ""), "transfer_out": ("transfer", ""), "transfer_line": ("transfer", ""),
    "rescission": ("rescission", ""), "rescission_defense": ("rescission", "defense"),
    "rescission_unlabelled": ("rescission", ""), "emergency_defense": ("supplemental", "defense"),
}



def widen_table_pages(docs, new_obs):
    """A document's source_page lists its table's pages (the main range first, then single pages: other tables,
    D-8's 302(b) page). The Title III load recorded only the pages it cited; the full table's lines cite the pages
    around them, so the main range takes in every page a new line cites, and the single pages it now reaches."""
    cited = collections.defaultdict(set)
    for o in new_obs:
        cited[o["source_document_id"]].add(int(o["source_page"]))
    for d in docs:
        pages = cited.get(d["document_id"])
        if not pages or not d["source_page"]:
            continue
        parts = [x.strip() for x in d["source_page"].split(";")]
        lo, _, hi = parts[0].partition("-")
        lo, hi = int(lo), int(hi or lo)
        singles = [int(x) for x in parts[1:]]
        if all(lo <= pg <= hi or pg in singles for pg in pages):
            continue
        lo, hi = min(lo, *pages), max(hi, *pages)
        grew = True
        while grew:
            grew = False
            for x in singles:
                if lo - 1 <= x <= hi + 1:
                    lo, hi, grew = min(lo, x), max(hi, x), True
            singles = [x for x in singles if not lo <= x <= hi]
        d["source_page"] = "; ".join([f"{lo}-{hi}"] + [str(x) for x in singles])

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = staged_data.load()
    retired = json.loads(RETIRED.read_text())
    acc = {x["canonical_account_id"]: x for x in data["accounts"]}
    live = {}
    for o in data["observations"]:
        if o["verification_status"] != "superseded":
            live.setdefault((o["canonical_account_id"], int(o["fiscal_year"]), o["stage"], o["amount_type"],
                             o.get("component") or ""), []).append(o)
    accts = ACCOUNTS
    title3 = [(aid, "Title III", acc[aid]["bureau"] if acc[aid]["agency"] not in (
        "National Aeronautics and Space Administration", "National Science Foundation") else acc[aid]["agency"],
        acc[aid]["canonical_name"], pats) for aid, pats in TITLE_III_PATTERNS.items()]

    ext = {}

    def table(src):
        if src not in ext:
            x, rows = load(PKG[src])
            secs = sections(rows)
            assigned = []
            for t in ("I", "II", "IV"):
                assigned += [(t,) + z for z in assign(secs.get(t, []), t, accts)]
            assigned += [("III",) + z for z in assign(secs.get("III", []), "III", title3)]
            assigned += [("V",) + z for z in title_v(secs.get("V", []))]
            ext[src] = (x, rows, secs, assigned)
        return ext[src]

    # 1. facts per cell
    found = collections.defaultdict(list)       # (aid, fy, stage) -> [(role, row, cell)]
    unmatched = []
    for (fy, st), src in sorted(STAGES.items()):
        if not (ROOT / "extractions" / f"{PKG[src]}.json").exists():
            print(f"missing extraction for {src} (FY{fy} {st}): stopped there, nothing recorded for the cell")
            continue
        x, rows, secs, assigned = table(src)
        for t, r, aid, role in assigned:
            if t == "III" and fy not in TITLE_III_FY:
                continue                        # held by the earlier Title III loads
            cell = r["cells"].get((st, fy))
            if aid is None:
                if role is None and cell is not None:
                    unmatched.append([fy, st, src, t, r["page"], r["path"], cell["amount_as_printed"]])
                continue
            if cell is None:
                continue
            if role in ONE_OFF and (cell.get("amount_is_dash_zero") or (cell["amount_as_printed"] or "").strip()
                                    in ("---", "--", "-")):
                continue        # a one-off line's '---' in a column: the line isn't in that cell (nothing recorded)
            target = aid
            if role == "transfer_out" and t != "V":
                # '<fund> (transfer out)' printed under the receiving account: the fund's own figure
                m = letters(r["label"])
                if "promoteanddevelop" in m:
                    target = "ACC-NOAA-PDF"
                elif "crimevictimsfund" in m:
                    target = "ACC-DOJ-CVF"
            found[(target, fy, st)].append((role, r, cell, src, aid))

    # 2. observations
    absent = {(x["canonical_account_id"], x["fiscal_year"], x["stage"], x["amount_type"], x.get("component") or "")
              for x in data["confirmed_absences"]}
    alloc = IDS.Allocator([o["observation_id"] for o in data["observations"]] + list(retired.get("observations", {}))
                          + [v["validation_id"] for v in data["validations"]] + list(retired.get("validations", {})))
    new_acc = []
    order_no = collections.Counter()
    known = set(acc)
    for spec in accts:
        aid, title, bureau, name, _ = spec
        order_no[title] += 1
        if aid in known:
            continue
        dept = {"Title I": "Department of Commerce", "Title II": "Department of Justice"}.get(title, bureau)
        new_acc.append({"canonical_account_id": aid, "canonical_name": name, "agency": dept,
                        "bureau": bureau, "treasury_account_symbol": "", "status": "active", "historical_names": "",
                        "historical_identifiers": "", "fund_type": FUND_TYPES.get(aid, "general"), "subcommittee": "CJS",
                        "notes": "CJS full load (reference/review/cjs/backfill/build_cjs.py).", "title": title,
                        "display_order": 0, "total_scope": "", "parent_account_id": ""})
        known.add(aid)
    for aid, title, bureau, name, note in TITLE_V_ONLY:
        if aid not in known:
            new_acc.append({"canonical_account_id": aid, "canonical_name": name,
                            "agency": "Department of Commerce", "bureau": bureau, "treasury_account_symbol": "",
                            "status": "active", "historical_names": "", "historical_identifiers": "",
                            "fund_type": FUND_TYPES.get(aid, "general"), "subcommittee": "CJS", "notes": note,
                            "title": title,
                            "display_order": 0, "total_scope": "", "parent_account_id": ""})
            known.add(aid)
    acc_all = dict(acc, **{x["canonical_account_id"]: x for x in new_acc})

    new_obs, new_val, report, held_diff = [], [], [], []
    by_cell = {}
    for (aid, fy, st), items in sorted(found.items()):
        src = STAGES[(fy, st)]
        groups = collections.defaultdict(list)
        for role, r, cell, src_, owner in items:
            groups[ROLE[role]].append((role, r, cell, owner))
        for (atype, comp), its in sorted(groups.items()):
            if len(its) > 1 and atype == "budget authority" and comp == "":
                # an account printed under its old and its new name, one of them '---' in this column (EOIR
                # FY2017, VAWA FY2020-FY2021): the figure is the line that prints one
                printed = [z for z in its if not z[2].get("amount_is_dash_zero")
                           and (z[2]["amount_as_printed"] or "").strip() not in ("---", "--", "-")]
                if len(printed) <= 1:
                    its = printed or its[:1]
            if len(its) > 1 and atype == "budget authority" and comp == "":
                report.append([fy, st, aid, atype, comp, "ambiguous", "", "",
                               "; ".join(f"{r['label']} {c['amount_as_printed']} p.{r['page']}" for _, r, c in its)])
                continue
            if len(its) > 1:
                # several lines of one kind (two Title V rescissions of one account): each its own figure
                pass
            k = (aid, fy, st, atype, comp)
            if k in live:
                held = live[k]
                tab = sum(z[2]["amount"] or 0 for z in its)
                same = any(h["amount"] == tab for h in held) or sum(h["amount"] for h in held) == tab
                report.append([fy, st, aid, atype, comp, "held", tab, its[0][1]["page"],
                               "" if same else "table prints " + ", ".join(f"{z[2]['amount_as_printed']}" for z in its)
                               + "; held " + ", ".join(f"{h['observation_id']} {h['amount']}" for h in held)])
                if not same:
                    held_diff.append(k)
                continue
            # each line: its amount, link and notes
            lines = []
            cvf_out_under = {z[3] for z in its if z[0] == "transfer_out" and aid == "ACC-DOJ-CVF"}
            for role, r, cell, owner in its:
                amt = cell["amount"] if cell["amount"] is not None else 0
                if role == "transfer_out" and amt > 0:
                    amt = -amt
                note = ""
                if role == "offset" and amt > 0 and not POSITIVE_OFFSET.search(letters(r["label"])):
                    # a deduction the page prints with a minus sign, read without it (an OCR text layer: H.Rept.
                    # 115-231 p.112 'Offsetting fee collections ... 13,000', its Bill vs Enacted column -1,000)
                    amt = -amt
                    note = (f" -- Note: read as '{cell['amount_as_printed']}' without its minus sign; recorded "
                            f"negative (a deduction from the account's line; the title total confirms)")
                link = ""
                if role == "transfer_in" and aid == "ACC-NOAA-ORF":
                    link = "ACC-NOAA-PDF"
                if role == "transfer_out" and aid == "ACC-NOAA-PDF":
                    link = "ACC-NOAA-ORF"
                if role == "transfer_in" and any(
                        z[0] == "transfer_out" and "crimevictimsfund" in letters(z[1]["label"]) and z[4] == aid
                        for z in found.get(("ACC-DOJ-CVF", fy, st), [])):
                    link = "ACC-DOJ-CVF"      # '(by transfer)' from the fund printed under this account
                sec510 = lambda z: re.search(r"sec\.?\s*510", (z["label"] or "").lower())
                if role == "transfer_out" and aid == "ACC-DOJ-CVF":
                    link = owner if r["title"] != "V" and owner != aid else ""   # the account it's printed under
                    if r["title"] == "V" and sec510(r):
                        link = "ACC-DOJ-OIG"      # sec. 510: the fund's transfer to the Inspector General (as FY2027)
                if role == "transfer_in" and aid == "ACC-DOJ-OIG" and r["title"] == "V" and any(
                        z[0] == "transfer_out" and sec510(z[1]) for z in found.get(("ACC-DOJ-CVF", fy, st), [])):
                    link = "ACC-DOJ-CVF"
                if role == "defense" and "counterintelligence" in letters(r["label"]):
                    note += (" -- Note: the defense line of the account: the tables' Title V and Other Appropriations "
                             "lines print the FBI's Salaries and Expenses as defense and nondefense (inferred from them)")
                if role == "rescission_unlabelled":
                    note += (" -- Note: printed in Title V as a negative line naming the account, without the word "
                             "'rescission': read as the title's rescission of it")
                if role == "cr_addition":
                    note += (" -- Note: appropriated by the continuing resolution the label names, printed under "
                             "the account")
                if role == "offset" and "newfees" in letters(r["label"]):
                    note += " -- Note: a fee line printed under the fund, deducted like its offsetting collections"
                if role == "offset" and "spendingof" in letters(r["label"]):
                    note += " -- Note: the spending of the collections the line above deducts (printed as a positive)"
                if role == "offset" and "diversioncontrol" in letters(r["label"]):
                    note += " -- Note: the Diversion Control Fund fees the table deducts from the account's line"
                dash = bool(cell.get("amount_is_dash_zero")) or (cell["amount_as_printed"] or "").strip() in (
                    "---", "--", "-", "\u2014")
                lines.append({"role": role, "r": r, "cell": cell, "amt": amt, "link": link, "note": note, "dash": dash})
            by_link = collections.defaultdict(list)
            for ln in lines:
                by_link[ln["link"]].append(ln)
            for link, lns in sorted(by_link.items()):
                # one fact per account, year, stage, amount type, component and counterpart (the store's
                # observation_fact): a '---' beside a printed figure adds nothing; several printed lines of one
                # fact (FBI's fee and other rescissions, a fund's fee lines) are recorded as their sum
                use = [ln for ln in lns if not ln["dash"]] or lns[:1]
                first = use[0]
                r, cell = first["r"], first["cell"]
                amt = sum(ln["amt"] for ln in use)
                agency = acc_all[aid]["bureau"] if acc_all[aid]["bureau"] not in ("", None) else acc_all[aid]["agency"]
                where = (f"Title V, General Provisions" if first["role"].startswith("rescission") or r["title"] == "V"
                         else f"{acc_all[aid]['title']}, {agency}")
                if len(use) == 1:
                    text = f"{where} -- printed as {r['label']!r} [{cell['column_header']}]" + first["note"]
                    if first["dash"]:
                        text += " -- printed '---'"
                else:
                    text = (f"{where} -- the sum of {len(use)} lines printed in the cell: "
                            + "; ".join(f"{ln['r']['label']!r} {ln['cell']['amount_as_printed']} (p.{ln['r']['page']})"
                                        for ln in use) + f" [{cell['column_header']}]"
                            + "".join(dict.fromkeys(ln["note"] for ln in use)))
                if first["dash"] and (aid, fy, st, atype, comp) in absent:
                    # the bill or law text already records nothing of this fact (a confirmed absence); the
                    # table's '---' agrees and adds nothing (the store holds one or the other)
                    report.append([fy, st, aid, atype, comp, "confirmed absence (bill text); '---' printed", "",
                                   r["page"], r["label"]])
                    continue
                oid = alloc.next("OBS", "CJS")
                o = {"observation_id": oid, "canonical_account_id": aid, "fiscal_year": fy, "stage": st,
                     "chamber": CHAMBER[st], "bill_id": "", "report_id": "", "amount": amt, "amount_type": atype,
                     "offsetting_collections": "TRUE" if atype == "offsetting_collection" else "FALSE",
                     "transfer_link_account_id": link, "source_document_id": src, "source_page": str(r["page"]),
                     "source_table_or_section": text,
                     "extraction_method": "AI-extracted" if cell.get("extraction_method") == "vision" else
                     (cell.get("extraction_method") or "text-extracted"),
                     "confidence": cell.get("extraction_confidence") or 0.95, "verification_status": "",
                     "component": comp, "headline_observation_id": "", "added_date": ""}
                new_obs.append(o)
                for ln in use:
                    by_cell.setdefault((aid, fy, st), []).append((o, ln["role"], ln["r"], ln["cell"]))
                    for rec in ln["cell"]["_records"]:
                        if rec["rule_applied"] in ("table_total", "structural", "source_text", "unit", "semantic"):
                            new_val.append({"observation_id": oid, "rule_applied": rec["rule_applied"],
                                            "expected_result": rec["expected_result"],
                                            "observed_result": rec["observed_result"], "result": rec["result"]})
                report.append([fy, st, aid, atype, comp, "found", amt, r["page"], " + ".join(ln["r"]["label"] for ln in use)])

    # 3. sum checks: each printed agency total and title total against our lines in it
    BUREAU = {x[0]: x[2] for x in accts + title3}
    ADD = ("head", "defense", "emergency", "offset", "transfer_line", "cr_addition", "census_2020",
           "request_proposal_nist_construction")
    sums = []
    held_vs_read = []       # a held title total this read of the page doesn't print
    for (fy, st), src in sorted(STAGES.items()):
        if not (ROOT / "extractions" / f"{PKG[src]}.json").exists():
            continue
        x, rows, secs, assigned = table(src)
        for t in ("I", "II", "III", "IV", "V"):
            if t == "III" and fy not in TITLE_III_FY:
                continue
            seq = [z for z in assigned if z[0] == t]
            block, mine = [], []
            for tt, r, aid, role in seq:
                cell = r["cells"].get((st, fy))
                if role == "total":
                    if cell is None or cell["amount"] is None:
                        block = [] if bureau_of_total(r["key"]) else block
                        continue
                    b = bureau_of_total(r["key"])
                    is_title = bool(title_total_of(r["key"]))
                    if not (b or is_title):
                        continue
                    pool = seq if is_title else [z for z in seq if z[2] and BUREAU.get(z[2]) == b
                                                 or z[2] is None and z[3] is None and bureau_of_heading(z[1]) == b]
                    parts = [(z, z[1]["cells"].get((st, fy))) for z in pool
                             if z[2] and z[3] in ADD + (("rescission", "rescission_defense", "rescission_unlabelled",
                                                                  "emergency_defense") if t == "V" else ())]
                    loose = [z for z in pool if z[2] is None and z[3] is None and z[1]["cells"].get((st, fy))
                             and (z[1]["cells"][(st, fy)]["amount"] or 0)]
                    got = sum(-abs(c["amount"] or 0) if z[3] == "offset" and not POSITIVE_OFFSET.search(letters(z[1]["label"]))
                              else (c["amount"] or 0) for z, c in parts if c)
                    if is_title:
                        # the title total as already held (the D-7 load, read from the same page): it wins over
                        # this read of the page where the two differ
                        held = live.get((f"ACC-CJS-TITLE-{title_total_of(r['key'])}-TOTAL", fy, st,
                                         "budget authority", ""))
                        if held and held[0]["amount"] != cell["amount"]:
                            held_vs_read.append([fy, st, src, r["page"], r["label"], held[0]["observation_id"],
                                                 held[0]["amount"], cell["amount"], got])
                            if got == held[0]["amount"]:
                                cell = dict(cell, amount=held[0]["amount"],
                                            amount_as_printed=f"{held[0]['amount'] // 1000:,} as held "
                                                              f"({held[0]['observation_id']})")
                    ok = got == cell["amount"]
                    sums.append((fy, st, src, r, cell, parts, loose, got, ok, is_title))
                    if b:
                        block = []
                    continue
                block.append((tt, r, aid, role))
    # the agency totals held as accounts (NASA, NSF): a Title III year the load reads (FY2027 House) records the
    # printed 'Total, <agency>' line where our accounts' lines sum to it (the earlier years are held as rollups)
    AGENCY_TOTAL = {"National Aeronautics and Space Administration": "ACC-NASA-TOTAL",
                    "National Science Foundation": "ACC-NSF-TOTAL"}
    for fy, st, src, r, cell, parts, loose, got, ok, is_title in sums:
        tid = AGENCY_TOTAL.get(bureau_of_total(r["key"]) or "")
        if is_title or not tid or not ok or live.get((tid, fy, st, "budget authority", "")):
            continue
        o = {"observation_id": alloc.next("OBS", "CJS"), "canonical_account_id": tid, "fiscal_year": fy, "stage": st,
             "chamber": CHAMBER[st], "bill_id": "", "report_id": "", "amount": cell["amount"],
             "amount_type": "budget authority", "offsetting_collections": "FALSE", "transfer_link_account_id": "",
             "source_document_id": src, "source_page": str(r["page"]),
             "source_table_or_section": f"Title III, {acc_all[tid]['agency']} -- printed as {r['label']!r} "
                                        f"[{cell['column_header']}]",
             "extraction_method": "AI-extracted" if cell.get("extraction_method") == "vision" else
             (cell.get("extraction_method") or "text-extracted"),
             "confidence": cell.get("extraction_confidence") or 0.95, "verification_status": "",
             "component": "", "headline_observation_id": "", "added_date": ""}
        new_obs.append(o)
        new_val.append({"observation_id": o["observation_id"], "rule_applied": "table_total",
                        "expected_result": f"the printed '{r['label']}' (p.{r['page']}) = the sum of our lines in it: "
                                           + " + ".join(f"{(c['amount'] or 0) // 1000:,}" for _, c in parts if c)
                                           + f" = {got // 1000:,} (thousands)",
                        "observed_result": f"{cell['amount'] // 1000:,} printed", "result": "pass"})
        report.append([fy, st, tid, "budget authority", "", "found", cell["amount"], r["page"], r["label"]])
    obs_at = {}
    for o in new_obs:
        obs_at.setdefault((o["source_document_id"], o["fiscal_year"], o["stage"], o["source_page"], o["amount"]), []).append(o)
    for fy, st, src, r, cell, parts, loose, got, ok, is_title in sums:
        what = f"the printed '{r['label']}' (p.{r['page']}, {cell['amount_as_printed']})"
        exp = (f"{what} = the sum of our lines in it: " + " + ".join(
            f"{(c['amount'] or 0) // 1000:,}" for _, c in parts if c) + f" = {got // 1000:,} (thousands)")
        if loose:
            exp += "; lines no account took: " + "; ".join(f"'{z[1]['label']}' {z[1]['cells'][(st, fy)]['amount_as_printed']}" for z in loose)
        for z, c in parts:
            if not c:
                continue
            for o in by_cell.get((z[2], fy, st), []):
                if o[2] is z[1]:
                    new_val.append({"observation_id": o[0]["observation_id"],
                                    "rule_applied": "table_total", "expected_result": exp,
                                    "observed_result": f"{cell['amount'] // 1000:,} printed",
                                    "result": "pass" if ok else "flag"})

    # 4. cross-document
    cross = []
    for (aid, fy, st), its in sorted(by_cell.items()):
        other = CROSS.get((fy, st))
        if not other or not (ROOT / "extractions" / f"{PKG[other]}.json").exists():
            continue
        _, _, _, oassigned = table(other)
        for o, role, r, cell in its:
            hits = [(z[1], z[1]["cells"].get((st, fy))) for z in oassigned
                    if z[2] == aid and z[3] == role and z[1]["cells"].get((st, fy)) is not None]
            if role in ("transfer_out",):
                continue
            vals = {(c["amount"] or 0) * (-1 if role == "transfer_out" else 1) for _, c in hits}
            if len(vals) != 1:
                cross.append([fy, st, aid, o["amount_type"], o["component"], o["observation_id"], o["amount"], "", "",
                              "not printed" if not hits else "ambiguous"])
                continue
            hr, hc = hits[0]
            ok = (hc["amount"] or 0) == o["amount"]
            cross.append([fy, st, aid, o["amount_type"], o["component"], o["observation_id"], o["amount"],
                          hc["amount"], hr["page"], "agree" if ok else "differ"])
            new_val.append({"observation_id": o["observation_id"], "rule_applied": "cross_document",
                            "expected_result": f"{other} p.{hr['page']} prints {(hc['amount'] or 0) // 1000:,} "
                                               f"('{hr['label']}' [{hc['column_header']}])",
                            "observed_result": f"{o['amount'] // 1000:,} as recorded", "result": "pass" if ok else "flag"})

    # 5. IDs and review status (the triage's standard reasons for routine notes; the rest pending)
    for v in new_val:
        v["validation_id"] = alloc.next("VAL", "CJS")
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
        except (ValueError, KeyError, AttributeError):
            fam, fmt = None, {}
        disp, reason = T.FAMILIES.get(fam, ("B", ""))
        if (v["rule_applied"] == "table_total" and "children found by the printed total" in (v["observed_result"] or "")
                and any(w is not v and w["rule_applied"] == "table_total" and w["result"] == "pass"
                        and w["expected_result"].startswith("the printed") for w in by_obs[v["observation_id"]])):
            v.update({"human_review_status": "resolved", "reviewer": TRIAGE_REVIEWER,
                      "resolution": "the parser placed this total's lines by the printed total; the agency or title "
                                    "sum check on the same figure (our accounts) matches it"})
            continue
        if disp == "A":
            try:
                v.update({"human_review_status": "resolved", "reviewer": TRIAGE_REVIEWER,
                          "resolution": reason.format(**fmt)})
                continue
            except (KeyError, IndexError):
                pass
        v["human_review_status"] = "pending"
    checks = collections.defaultdict(list)
    for v in new_val:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                            v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    order = ["validation_id", "observation_id", "rule_applied", "expected_result", "observed_result", "result",
             "human_review_status", "reviewer", "resolution"]
    new_val = [{k: v[k] for k in order} for v in new_val]

    # ---- report
    st_counts = collections.Counter(o["stage"] for o in new_obs)
    print(f"{len(new_acc)} new accounts; {len(new_obs)} observations {dict(st_counts)}; {len(new_val)} validations "
          f"({sum(v['human_review_status'] == 'pending' for v in new_val)} pending)")
    print("statuses:", dict(collections.Counter(o["verification_status"] for o in new_obs)))
    print("sum checks:", collections.Counter(("title" if s[9] else "agency", s[8]) for s in sums))
    print("cross-document:", collections.Counter(c[-1] for c in cross))
    print(f"held cells where the table differs: {len(held_diff)}; unmatched printed lines: {len(unmatched)}")
    with open(HERE / "report.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fiscal_year", "stage", "canonical_account_id", "amount_type", "component", "result", "amount",
                    "pdf_page", "printed"])
        w.writerows(report)
    with open(HERE / "unmatched.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fiscal_year", "stage", "source_document_id", "title", "pdf_page", "printed_path", "printed"])
        w.writerows(unmatched)
    with open(HERE / "cross_document.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fiscal_year", "stage", "canonical_account_id", "amount_type", "component", "our_observation_id",
                    "our_amount", "other_amount", "other_pdf_page", "result"])
        w.writerows(cross)
    with open(HERE / "sums.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fiscal_year", "stage", "source_document_id", "pdf_page", "total", "printed", "our_sum", "result",
                    "lines_no_account_took"])
        for fy, st, src, r, cell, parts, loose, got, ok, is_title in sums:
            w.writerow([fy, st, src, r["page"], r["label"], cell["amount"], got, "agree" if ok else "differ",
                        "; ".join(f"{z[1]['label']} {z[1]['cells'][(st, fy)]['amount_as_printed']}" for z in loose)])
    with open(HERE / "held_totals.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fiscal_year", "stage", "source_document_id", "pdf_page", "total", "held_observation_id",
                    "held_amount", "read_amount", "our_sum"])
        w.writerows(held_vs_read)
    print(f"held title totals this read of the page doesn't print: {len(held_vs_read)}")
    # placement: every account of Titles I, II, IV and V in the tables' printed order (100 on, clear of the title
    # totals' 30-34); ACC-DOJ-OIG is Title II's (it was filed under Title V for its FY2027 transfer line)
    placed = [x[0] for x in accts]
    placed.insert(placed.index("ACC-NOAA-ORF") + 1, "ACC-NOAA-PDF")
    placed += ["ACC-DOJ-WCF", "ACC-DOJ-OJP-RESC"] + [x[0] for x in TITLE_V_ONLY]
    seen_title = collections.Counter()
    placement = {}
    for aid in placed:
        t = "Title II" if aid == "ACC-DOJ-OIG" else acc_all[aid]["title"]
        seen_title[t] += 1
        placement[aid] = (t, 100 + seen_title[t])
    if a.write:
        for x in data["accounts"] + new_acc:
            if x["canonical_account_id"] in placement:
                x["title"], x["display_order"] = placement[x["canonical_account_id"]]
        data["accounts"].extend(new_acc)
        have_c = {c["component_id"] for c in data["components"]}
        used_c = {o["component"] for o in new_obs if o["component"]}
        data["components"].extend({"component_id": c, "label": A.COMPONENT_LABELS[c][0], "kind": k, "description": d}
                                  for c, k, d in A.COMPONENT_KINDS if c in used_c and c not in have_c)
        have = {b["reference_id"] for b in data["bill_report_refs"]}
        data["bill_report_refs"].extend(b for b in NEW_BRR if b["reference_id"] not in have)
        widen_table_pages(data["source_docs"], new_obs)
        data["observations"].extend(new_obs)
        data["validations"].extend(new_val)
        staged_data.save(data)
        print("appended to data/staged/")
    return {"obs": new_obs, "val": new_val, "unmatched": unmatched, "sums": sums}


if __name__ == "__main__":
    main()
