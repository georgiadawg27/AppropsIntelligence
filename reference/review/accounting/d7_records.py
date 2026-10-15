"""
D-7: the printed summary lines of each held committee report's comparative table, recorded as observations on the
bill and title total rows (data/staged.json). Supersede, never overwrite; no ID reused; blank is not zero.

Reads summary_rows.csv (the text layer) and summary_rows_images.csv (the page images: the scanned tables, and every
text-layer page whose comparison columns didn't agree with its money columns). A row is used only where the
table's own comparison columns agree with it (or the table prints none: the one-column FY2026 Senate tables).

For every cell (subcommittee, fiscal year, stage) the held accounts take from a report or explanatory statement, the
same document's column gives the bill total and each title total of that cell, and the lines printed under them:

  bill   'Grand total' (the headline), Appropriations, Emergency appropriations, Rescissions, Advance appropriations,
         Trust funds, (Mandatory), (Discretionary); the Labor-HHS House RECAP's 'Mandatory, total in bill',
         'Discretionary, total in bill', 'Total, mandatory, current year', 'Subtotal, discretionary, current year',
         'Total, discretionary' and 'Grand total, this fiscal year'
  title  'Total, title X' (the headline), Federal funds, Current year, Advance appropriations, Trust funds,
         Emergency appropriations, Rescissions, Appropriations, 'Total, title X ... discretionary'

Lines outside the total's budget authority ((by transfer), (transfer out), the Prevention and Public Health Fund,
CURES, Pandemic Flu balances, cap adjustments ...), the RECAP's less/plus rows and scorekeeping items stay in the
CSVs as read, not recorded (LEFT below says why). A title we hold no total row for gets one (owner, D-7: 'add title
rows'), and each subcommittee gets its bill total row (the grid's bill_total).

    python reference/review/accounting/d7_records.py            # what would change
    python reference/review/accounting/d7_records.py --write    # write it (idempotent)
"""

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
STAGED = ROOT / "data" / "staged.json"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import accounts as A  # noqa: E402
import ids as IDS  # noqa: E402
import summary_rows as SR  # noqa: E402

TEXT = HERE / "summary_rows.csv"
IMAGES = HERE / "summary_rows_images.csv"
OUT = HERE / "summary_lines.csv"

# line (summary_rows.norm) -> component recorded, per block kind; None: the headline
RECORDED = {
    "bill": {"grand_total": None, "appropriations": "appropriations",
             "emergency_appropriations": "emergency_appropriations", "rescissions": "rescissions",
             "rescissions_emergency": "rescissions_emergency", "advance_appropriations": "advance_appropriations",
             "advance_appropriations_emergency": "advance_appropriations_emergency", "trust_funds": "trust_funds",
             "federal_funds": "federal_funds", "mandatory": "mandatory", "discretionary": "discretionary",
             "mandatory_total_in_bill": "mandatory", "discretionary_total_in_bill": "discretionary",
             "mandatory_current_year": "mandatory_available_this_fiscal_year",
             "discretionary_current_year": "discretionary_available_this_fiscal_year",
             "discretionary_total": "discretionary_after_scorekeeping",
             "grand_total_this_fiscal_year": "available_this_fiscal_year",
             "grand_total_excluding_other": "excluding_other_appropriations"},
    "title": {"title_total": None, "federal_funds": "federal_funds", "current_year": "current_year",
              "advance_appropriations": "advance_appropriations",
              "advance_appropriations_emergency": "advance_appropriations_emergency", "trust_funds": "trust_funds",
              "emergency_appropriations": "emergency_appropriations", "rescissions": "rescissions",
              "appropriations": "appropriations", "discretionary": "discretionary", "mandatory": "mandatory"},
}
LEFT = {
    "by_transfer": "a transfer line: not budget authority of the total",
    "transfer_out": "a transfer line: not budget authority of the total",
    "pphf": "the Prevention and Public Health Fund memo line: transfers, not budget authority of the total",
    "cures_act": "the CURES Act memo line (the Labor-HHS Title II total prints it outside its own sum)",
    "pandemic_flu_balances": "a memo line of balances, not new budget authority",
    "cap_adjustment": "a memo line of a cap adjustment inside an account's figure",
    "program_integrity": "a memo line of a cap adjustment inside an account's figure",
    "less_advances_subsequent": "a RECAP step between two printed totals (both recorded)",
    "less_advance_emergency": "a RECAP step between two printed totals (both recorded)",
    "plus_advances_prior": "a RECAP step between two printed totals (both recorded)",
    "plus_emergency_advances_prior": "a RECAP step between two printed totals (both recorded)",
    "scorekeeping_adjustments": "the RECAP's scorekeeping adjustments: CBO scoring, not the bill's budget authority",
    "other": "not a summary line of the total (an item the table prints among them)",
    "disaster_relief": "a supplemental act's total, not a summary line",
    "limitation": "a limitation, not budget authority",
    "budget_amendment": "a budget amendment memo line",
    "other_appropriations": "the Other Appropriations (supplemental acts) block's total, not a title",
}
COMPONENTS = A.SUMMARY_COMPONENTS
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
STAGE_CHAMBER = {"House Reported": "House", "Senate Reported": "Senate"}


def rows():
    """The usable rows, in printed order, by document: image reads replace the text layer on the pages they cover."""
    text = list(csv.DictReader(TEXT.open()))
    img = list(csv.DictReader(IMAGES.open())) if IMAGES.exists() else []
    covered = {(r["source_document_id"], r["pdf_page"]) for r in img}
    by_doc = collections.defaultdict(list)
    for r in text:
        if r["label"].startswith("[block ends]") or (r["source_document_id"], r["pdf_page"]) in covered:
            continue
        by_doc[r["source_document_id"]].append(r)
    for r in img:
        by_doc[r["source_document_id"]].append(r)
    for doc in by_doc:
        by_doc[doc].sort(key=lambda r: (int(r["pdf_page"]), int(r["seq"])))
    return by_doc


def usable(r):
    return r["arithmetic"] in ("agrees", "no comparison columns")


def line_of(r):
    """The line a row names; the grand total of a RECAP ('this fiscal year' / 'current year') kept apart."""
    lab = re.sub(r"\s+", " ", r["label"]).strip(" .:").lower()
    if re.match(r"grand total.*excluding other", lab):
        return "grand_total_excluding_other"
    if re.match(r"grand total,? (this fiscal year|current year)", lab):
        return "grand_total_this_fiscal_year"
    if re.match(r"total,? other appropriations", lab):
        return "other_appropriations"
    if re.match(r"^\(?emergency advance appropriations|^\(advance appropriations.*\(emergency\)", lab):
        return "advance_appropriations_emergency"
    if re.match(r"^\(?rescissions? of emergency|^\(recission of emergency", lab):
        return "rescissions_emergency"
    if re.match(r"^new advance", lab):
        return "advance_appropriations"
    if re.match(r"^current year \(emergency\)", lab):
        return "other"
    if re.match(r"^total:? discretionary, current year", lab):
        return "discretionary_current_year"
    if re.match(r"^fy ?\d{4}$", lab):
        return "advance_appropriations"
    return SR.norm(r["label"])


def blocks(doc_rows):
    """[(block, [rows])] in printed order: a title block, or the bill block (grand total, its summary, RECAP)."""
    out = []
    for r in doc_rows:
        b = r["block"]
        if not out or out[-1][0] != b:
            out.append((b, []))
        out[-1][1].append(r)
    merged = collections.OrderedDict()
    for b, rs in out:
        merged.setdefault(b, []).extend(rs)
    return list(merged.items())


def column(doc, fy, stage):
    """The money column (c1, c2, c3) of the document that prints the cell's figure."""
    dfy, dstage = int(doc["fiscal_year"]), doc["stage"]
    if fy == dfy and stage == dstage:
        return "c3"
    if fy == dfy and stage == "President's Budget":
        return "c2"
    if fy == dfy - 1 and stage == "Enacted":
        return "c1"
    return None


COLUMN_WORD = {"c1": "prior-year enacted column", "c2": "budget request column"}


def column_word(doc, col):
    if col != "c3":
        return COLUMN_WORD[col]
    return {"House Reported": "Bill column", "Senate Reported": "Committee recommendation column",
            "Enacted": "Final bill column"}.get(doc["stage"], "recommendation column")


def cells(data):
    """{(subcommittee, fy, stage): document} -- the report or explanatory statement the cell's total rows (else most
    of its accounts) take their figures from."""
    docs = {s["document_id"]: s for s in data["source_docs"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    count = collections.defaultdict(collections.Counter)
    totals = collections.defaultdict(collections.Counter)
    for o in data["observations"]:
        if docs[o["source_document_id"]]["document_type"] not in ("committee_report", "explanatory_statement"):
            continue
        a = acc[o["canonical_account_id"]]
        k = (a["subcommittee"], int(o["fiscal_year"]), o["stage"])
        (totals if a.get("total_scope") else count)[k][o["source_document_id"]] += 1
    out = {}
    for k in set(count) | set(totals):
        pick = totals[k] or count[k]
        out[k] = pick.most_common(1)[0][0]
    return out


def title_name(rows_, roman):
    labels = collections.Counter()
    for r in rows_:
        m = re.match(r"total,?\s+title\s+[ivxl!|]+[,.]?\s*(.*)$", re.sub(r"\s+", " ", r["label"]).strip(" ."), re.I)
        if m and m.group(1) and "discretionary" not in m.group(1).lower():
            labels[m.group(1).strip(" ,.")] += 1
    return labels.most_common(1)[0][0] if labels else ""


def table_pages(data, added=()):
    """A document's recorded table pages, with the page of every summary line recorded from it added (as its own
    entry: '120-121; 115; 124'), so each citation is inside its document's table pages. -> {document: new range}"""
    import approps_store as S
    docs = {s["document_id"]: s for s in data["source_docs"]}
    totals = {a["canonical_account_id"] for a in data["accounts"] if a.get("total_scope") in ("title", "bill")}
    out = {}
    for o in list(data["observations"]) + list(added):
        if o["canonical_account_id"] not in totals or " -- printed as '" not in o["source_table_or_section"]:
            continue
        d = docs[o["source_document_id"]]
        cur = out.get(d["document_id"], d["source_page"] or "")
        if not S.within(o["source_page"], cur):
            out[d["document_id"]] = "; ".join(x for x in (cur, o["source_page"]) if x)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    data = json.loads(STAGED.read_text())
    retired = json.loads(RETIRED.read_text())
    docs = {s["document_id"]: s for s in data["source_docs"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    by_doc = rows()
    cell_doc = cells(data)
    alloc = IDS.Allocator([o["observation_id"] for o in data["observations"]] + list(retired.get("observations", {})))
    live = {}
    for o in data["observations"]:
        if o["verification_status"] != "superseded":
            live[(o["canonical_account_id"], int(o["fiscal_year"]), o["stage"], o["amount_type"],
                  o.get("component") or "")] = o

    # the total rows: each subcommittee's bill total, a title total for every title its documents print
    sc_titles = collections.defaultdict(lambda: collections.defaultdict(list))
    for (sc, fy, st), doc in cell_doc.items():
        for b, rs in blocks(by_doc.get(doc, [])):
            if b.startswith("Title "):
                sc_titles[sc][b] += rs
    new_accounts, total_id = [], {}
    for sc in sorted(sc_titles):
        existing = {a["title"]: a["canonical_account_id"] for a in data["accounts"]
                    if a["subcommittee"] == sc and a.get("total_scope") == "title"}
        top = max((a["display_order"] or 0 for a in data["accounts"] if a["subcommittee"] == sc
                   and isinstance(a["display_order"], int)), default=0)
        for t in sorted(sc_titles[sc], key=lambda t: ROMAN.get(t.split()[1], 99)):
            if t in existing:
                total_id[(sc, t)] = existing[t]
                continue
            aid = f"ACC-{sc}-TITLE-{t.split()[1]}-TOTAL"
            name = title_name(sc_titles[sc][t], t.split()[1])
            top += 1
            new_accounts.append({
                "canonical_account_id": aid, "canonical_name": f"{t}, {name} (title total)" if name else f"{t} (title total)",
                "agency": name or t, "bureau": "(Title Total)", "treasury_account_symbol": "", "status": "active",
                "historical_names": "", "historical_identifiers": "", "fund_type": "general", "subcommittee": sc,
                "notes": f"Derived rollup -- the printed 'Total, {t}' row of each report's comparative table, with the "
                         "lines printed under it (D-7, reference/review/accounting/d7_records.py).",
                "title": t, "display_order": top, "total_scope": "title", "parent_account_id": ""})
            total_id[(sc, t)] = aid
        bid = f"ACC-{sc}-BILL-TOTAL"
        if not any(a["subcommittee"] == sc and a.get("total_scope") == "bill" for a in data["accounts"]):
            new_accounts.append({
                "canonical_account_id": bid, "canonical_name": "Bill total (grand total)", "agency": "Bill total",
                "bureau": "(Bill Total)", "treasury_account_symbol": "", "status": "active", "historical_names": "",
                "historical_identifiers": "", "fund_type": "general", "subcommittee": sc,
                "notes": "Derived rollup -- the printed 'Grand total' row that ends each report's comparative table, "
                         "with the summary lines printed under it (D-7, reference/review/accounting/d7_records.py).",
                "title": None, "display_order": None, "total_scope": "bill", "parent_account_id": ""})
        total_id[(sc, "bill")] = next((a["canonical_account_id"] for a in data["accounts"]
                                       if a["subcommittee"] == sc and a.get("total_scope") == "bill"), bid)

    # the observations, cell by cell
    added, kept, report = [], [], []
    for (sc, fy, st), doc_id in sorted(cell_doc.items()):
        doc = docs[doc_id]
        col = column(doc, fy, st)
        rs_doc = by_doc.get(doc_id)
        if not rs_doc:
            report.append((sc, fy, st, doc_id, "", "", "", "no summary rows read for this document"))
            continue
        if col is None:
            report.append((sc, fy, st, doc_id, "", "", "", "the document prints no column for this cell"))
            continue
        one_col = all(r["arithmetic"] == "no comparison columns" for r in rs_doc)
        if one_col and col != "c3":
            report.append((sc, fy, st, doc_id, "", "", "", "a one-column table: prints only its own recommendation"))
            continue
        for b, rs in blocks(rs_doc):
            kind = "bill" if b == "bill" else "title" if b.startswith("Title ") else None
            if kind is None:
                for r in rs:
                    report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"], "not recorded: " + LEFT["other_appropriations"]))
                continue
            aid = total_id.get((sc, "bill" if kind == "bill" else b))
            lines = [(r, line_of(r)) for r in rs]
            heads = [r for r, ln in lines if ln == ("grand_total" if kind == "bill" else "title_total")]
            # two grand totals (S.Rept. 118-207, 119-55): the last, which ends the table with the summary lines, is the
            # bill total; the first (titles only, ahead of the rescissions, CHIMPs and Other Appropriations) is a view
            head = heads[-1] if kind == "bill" and heads else heads[0] if heads else None
            if head is None and kind == "bill":
                # no plain 'Grand total' row: the RECAP's 'Grand Total, current year' is the bill total where it equals
                # the sum of the title totals as printed (H.Rept. 114-699, S.Rept. 114-274); otherwise none
                titles_sum = sum(int(r2[col] or 0) for b2, rs2 in blocks(rs_doc) if b2.startswith("Title ")
                                 for r2 in rs2 if line_of(r2) == "title_total")
                cand = next((r for r, ln in lines if ln == "grand_total_this_fiscal_year" and r[col]
                             and int(r[col]) == titles_sum), None)
                if cand is not None:
                    head = cand
                else:
                    recap = next((r for r, ln in lines if ln == "grand_total_this_fiscal_year"), None)
                    report.append((sc, fy, st, doc_id, recap["pdf_page"] if recap else "", b, recap["label"] if recap else "",
                                   "no bill total recorded: the table prints no plain 'Grand total'"
                                   + (f"; its RECAP 'Grand Total, current year' ({int(recap[col] or 0):,}) is a current-"
                                      f"year scope, not the sum of the title totals as printed ({titles_sum:,})"
                                      if recap else "")))
                    continue
            if head is None or not head[col]:
                report.append((sc, fy, st, doc_id, "", b, "", "no total row with a figure in this column"))
                continue
            pre = {id(r) for r in rs[:rs.index(head)]} if len(heads) > 1 and kind == "bill" else set()
            lines = [(head, "grand_total" if kind == "bill" else "title_total")] + [(r, ln) for r, ln in lines if r is not head]
            seen = collections.Counter()
            h_obs = None
            for r, ln in lines:
                comp = RECORDED[kind].get(ln, "#")
                if r is head:
                    comp = None
                elif comp is None:
                    comp = "#"
                if id(r) in pre and ln == "grand_total":
                    comp = "before_rescissions_and_other"
                elif id(r) in pre and ln in ("mandatory", "discretionary"):
                    comp = ln + "_before_rescissions_and_other"
                if comp == "#":
                    report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"],
                                   "not recorded: " + LEFT.get(ln, LEFT["other"])))
                    continue
                if r["arithmetic"] == "no amounts" or r[col] in ("", None):
                    continue                    # blank is not zero: the table prints nothing on this line here
                if not usable(r):
                    report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"],
                                   f"not recorded: the read doesn't agree with the table's comparison columns ({r['arithmetic']})"))
                    if comp is None:
                        break
                    continue
                if comp and comp.startswith("advance_appropriations"):
                    seen[comp] += 1
                    if seen[comp] > 1:
                        comp += "_later"
                        if seen[comp.removesuffix("_later")] > 2:
                            report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"],
                                           "not recorded: a third advance line (no component for it)"))
                            continue
                elif comp:
                    seen[comp] += 1
                    if seen[comp] > 1:
                        report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"],
                                       f"not recorded: a second '{comp}' line under the same total"))
                        continue
                val = r[col]
                if val in ("", None):
                    continue                    # blank is not zero: the table prints nothing in this column
                amount = int(val) * 1000
                key = (aid, fy, st, "budget authority", comp or "")
                old = live.get(key)
                if old is not None:
                    if old["amount"] == amount:
                        kept.append(old)
                        if comp is None:
                            h_obs = old
                        continue
                    report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"],
                                   f"not recorded: {old['observation_id']} already holds {old['amount']:,} "
                                   f"({old['source_document_id']} p.{old['source_page']}); this column prints {amount:,}"))
                    if comp is None:
                        break
                    continue
                if comp and h_obs is None:
                    report.append((sc, fy, st, doc_id, r["pdf_page"], b, r["label"], "not recorded: no headline"))
                    continue
                code = "LHHS" if sc == "LHHS" else sc
                o = {"observation_id": alloc.next("OBS", code), "canonical_account_id": aid, "fiscal_year": fy,
                     "stage": st, "chamber": STAGE_CHAMBER.get(st, "N/A"), "bill_id": "", "report_id": "",
                     "amount": amount, "amount_type": "budget authority",
                     "offsetting_collections": "FALSE", "transfer_link_account_id": "",
                     "source_document_id": doc_id, "source_page": str(r["pdf_page"]),
                     "source_table_or_section": f"{'Bill total' if kind == 'bill' else b} -- printed as "
                                                f"'{re.sub(chr(32) + '+', ' ', r['label']).strip()}' [{column_word(doc, col)}]",
                     "extraction_method": "text-extracted" if r["read_from"] == "text layer" else "AI-extracted",
                     "confidence": 0.95, "verification_status": "unverified",
                     "component": comp or "", "headline_observation_id": h_obs["observation_id"] if comp else ""}
                added.append(o)
                live[key] = o
                if comp is None:
                    h_obs = o

    comps_new = [{"component_id": c, "label": COMPONENTS[c][0], "kind": COMPONENTS[c][1],
                  "description": COMPONENTS[c][2]}
                 for c in sorted({o["component"] for o in added} & set(COMPONENTS))
                 if c not in {x["component_id"] for x in data["components"]}]
    by = collections.Counter((acc.get(o["canonical_account_id"], {}).get("subcommittee")
                              or o["canonical_account_id"].split("-")[1], o["stage"]) for o in added)
    print(f"accounts added: {len(new_accounts)} ({', '.join(a['canonical_account_id'] for a in new_accounts)})")
    print(f"components added: {', '.join(c['component_id'] for c in comps_new) or 'none'}")
    print(f"observations added: {len(added)}; already held, equal: {len(kept)}")
    for k, n in sorted(by.items()):
        print(f"  {k[0]} {k[1]}: {n}")
    why = collections.Counter(x[7].split(" (")[0].split(":")[0] if x[7].startswith("not recorded: O") else x[7][:70]
                              for x in report)
    print("not recorded / reported:")
    for k, n in why.most_common():
        print(f"  {n:4d}  {k}")
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["subcommittee", "fiscal_year", "stage", "source_document_id", "pdf_page", "block", "label", "outcome"])
        sc_of = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"] + new_accounts}
        for o in sorted(added + kept, key=lambda o: o["observation_id"]):
            d7 = bool(re.match(r"(Title [IVX]+|Bill total) -- printed as '", o["source_table_or_section"]))
            w.writerow([sc_of[o["canonical_account_id"]], o["fiscal_year"], o["stage"], o["source_document_id"],
                        o["source_page"], o["canonical_account_id"],
                        re.search(r"'(.*)'", o["source_table_or_section"]).group(1),
                        f"{'recorded' if d7 else 'already held, equal'} {o['observation_id']} "
                        f"{o['component'] or 'headline'} {o['amount']:,}"])
        for x in sorted(report, key=lambda x: (x[0], x[1], x[2], str(x[4]))):
            w.writerow(x)
    pages = table_pages(data, added)
    for d_id, ps in sorted(pages.items()):
        print(f"  table pages {d_id}: {docs[d_id]['source_page']!r} -> {ps!r}")
    if args.write and (added or new_accounts or pages):
        for d_id, ps in pages.items():
            docs[d_id]["source_page"] = ps
        data["accounts"] += new_accounts
        data["components"] += comps_new
        data["observations"] += added
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")


if __name__ == "__main__":
    sys.exit(main())
