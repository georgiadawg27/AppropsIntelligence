"""
A triage proposal for the validation records pending human review, and the page-check items.
Reads data/staged.json, the extractions and reference/review/review_list.csv; changes no data.

    python reference/review/triage.py           # write the three CSVs below and print the summary

Writes, under reference/review/:
  triage_proposal.csv       one row per pending validation record and per page-check item:
                            id, group, proposed disposition (A resolve in bulk / B keep for a person /
                            C fix the data), reason (A: the line that would go into `resolution`)
  triage_groups.csv         one row per group: its key, count, 2-3 examples, disposition and reason
  flagged_observations.csv  every observation with verification_status = flagged and the checks
                            that flag it

A group is a family (what the check found, read from its expected/observed text) within the
key (rule_applied, result, the observation's verification_status, subcommittee, source document).
Resolving a record changes human_review_status only: verification_status reads `result`, so a
resolved fail or cross_document flag still leaves the cell flagged.
"""

import collections
import csv
import glob
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STAGED = ROOT / "data" / "staged.json"
REVIEW_LIST = HERE / "review_list.csv"

CONFIRMING = ("structural", "table_total", "cross_document", "arithmetic")

# family -> (disposition, reason). A reasons are written as the `resolution` line.
FAMILIES = {
    "memo-of-which": ("A", "informational: the memo lines printed under this total are 'of which' amounts or alternate "
                           "(program-level) totals, not its parts; a memo breakdown cannot confirm the figure"),
    "memo-split-closes": ("A", "informational: the funding split printed under this total ({split}) sums exactly to it; "
                               "the flag comes from the 'of which' lines in the same memo list"),
    "memo-split-open": ("B", "check the printed page: the funding split ({split}) does not sum to the total; "
                             "confirm whether a footnoted line (e.g. 'Prevention and Public Health Fund 1/') is part of it"),
    "nested-misread": ("A", "parse artifact: the model-read indent nests this Medicaid line under 'Vaccines for Children' "
                            "(e.g. 5,608,606 vs 197,580,474); no sum relationship exists"),
    "nested-other": ("B", "check the printed indent: the nesting was read by the model and the parent does not equal "
                          "its indented lines"),
    "no-children": ("A", "informational: the table prints no lines under this total, so the sum check has nothing to compare"),
    "children-incomplete": ("A", "informational: the parser could not place every printed line under this total, so the sum "
                                 "was not checked; no check disagrees with the figure"),
    "parallel-total": ("A", "informational: a parallel total of the family's main total; no one printed set of rows "
                            "explains the difference in every column, so it cannot confirm the figure"),
    "routine-amount-type": ("A", "routine: the amount_type matches the printed label ({label})"),
    "amount-type-name-word": ("A", "false positive: 'Advance' is part of the account's name (ARPA-H); the line is budget "
                                   "authority, as recorded"),
    "subtotal-label-ba": ("A", "routine: budget authority on the 'Subtotal, Operations and Emergency Response' row that "
                               "defines this account"),
    "cr-estimate": ("B", "decide the FY2025 Enacted treatment: P.L. 119-4 sec. 1109(a) set no dollar level; the figure is "
                         "the report's estimate (keep as is with this note, or mark the year's Enacted figures as estimates)"),
    "chimp-mechanism": ("B", "decide how to represent the FY27 CHIMP / CHIMP Pop-Up (a dedicated amount_type or a "
                             "multi-year note); the figure itself is human-verified"),
    "title-ii-request-27503": ("A", "explained: the 27,503 is ACL's '(Evaluation Tap Funding)' in the FY2023 request "
                                    "column (Senate draft p.416); both documents print the same column, figure as printed"),
    "title-ii-scope": ("B", "confirm H.Rept. 118-585 p.339's AI-extracted Title II total against the PDF and the scope "
                            "note in title_ii_totals.csv (the two reports scope the general provisions differently)"),
    "bill-text-disagrees": ("B", "decide which figure stands: H.R. 5894 prints $370,772,000; the explanatory table and its "
                                 "change column print 370,722 thousand (kept)"),
    "sum-fail-confirmed": ("A", "parse artifact: the lines grouped under this total do not match the printed table's "
                                "structure; the figure is confirmed by {confirm}"),
    "sum-fail-unconfirmed": ("B", "check the printed page: the lines under this total do not sum to it and no other check "
                                  "confirms the figure"),
    "page-derived": ("A", "derived figure (a sum of printed lines), not printed on the page; the page check cannot find it"),
    "page-nearby": ("B", "open the PDF: the figure was found {where}; if it is the same line, correct source_page"),
    "page-not-found": ("B", "open the PDF at source_page and confirm the figure is printed there (the text layer and OCR "
                            "missed it); correct source_page if it is elsewhere"),
}


def memo_family(v, o, ext):
    labs = re.match(r"memo breakdown \[(.*)\] sums to", v["expected_result"]).group(1).split("; ")
    split = [l for l in labs if not l.startswith("(") and not l.lower().startswith(("total", "subtotal"))]
    # 'of which' lines printed without parentheses
    split = [l for l in split if l not in ("Program integrity (cap adjustment)", "NIH Innovation Account, CURES Act")]
    # program-level additions printed without parentheses (S.Rept. 114-274 p.237, under 'Total, Centers for Disease
    # Control'): added to reach the program level, never parts of the budget-authority total
    split = [l for l in split if not l.startswith(("Pandemic Flu balances", "Prevention and Public Health Fund"))]
    if not split:
        return "memo-of-which", {}
    tot = next((x for x in ext.get(o["source_document_id"], [])
                if x["amount"] == o["amount"] and x["fiscal_year"] == o["fiscal_year"] and x["stage"] == o["stage"]
                and str(x["source_page"]) == str(o["source_page"])), None)
    if tot is None:
        return "memo-split-open", {"split": "; ".join(split)}
    kids = {x["account_name_as_written"]: x["amount"] for x in ext[o["source_document_id"]]
            if x["account_path"].startswith(tot["account_path"] + " / ") and x["column_index"] == tot["column_index"] and x["is_memo"]}
    sets = [[l for l in split if l.lower().startswith(p)] for p in (("federal", "trust"), ("current", "advance"), ("discretionary", "mandatory"))]
    closes = [s for s in sets if s and all(l in kids for l in s) and sum(kids[l] for l in s) == o["amount"]]
    if closes:
        return "memo-split-closes", {"split": " + ".join(closes[0])}
    return "memo-split-open", {"split": "; ".join(split)}


def family(v, o, others):
    e, ob, rule = v["expected_result"] or "", v["observed_result"] or "", v["rule_applied"]
    if rule == "cross_document":
        return ("bill-text-disagrees", {}) if "bill text" in e else ("title-ii-scope", {})
    if rule == "semantic":
        if e.startswith("amount_type fits the row label"):
            label = re.match(r"label '([^']*)'", ob).group(1)
            if "Advance Research Projects Agency" in label:
                return "amount-type-name-word", {}
            if label.startswith("Subtotal, Operations and Emergency Response"):
                return "subtotal-label-ba", {}
            return "routine-amount-type", {"label": f"'{label}'"}
        if e.startswith("a dollar level set in law"):
            return "cr-estimate", {}
        if "CHIMP" in e:
            return "chimp-mechanism", {}
        if "27,503" in e:
            return "title-ii-request-27503", {}
    if e.startswith("memo breakdown"):
        return None, {}                               # memo_family, which needs the extractions
    if e.startswith("nested under"):
        return ("nested-misread", {}) if "'Vaccines for Children'" in e else ("nested-other", {})
    if "no children found" in ob:
        return "no-children", {}
    if "children not fully extracted" in ob:
        return "children-incomplete", {}
    if e.startswith("parallel total"):
        return "parallel-total", {}
    if e.startswith("the agency totals as recorded") and v["observation_id"] == "OBS-LHHS-0537":
        return "title-ii-request-27503", {}
    if v["result"] == "fail":
        if others:
            return "sum-fail-confirmed", {"confirm": "; ".join(others[:2])}
        return "sum-fail-unconfirmed", {}
    raise ValueError(f"no family for {v['validation_id']}: {e[:80]}")


def confirming_passes(vals, v):
    out = []
    for w in vals:
        if w is v or w["result"] != "pass" or w["rule_applied"] not in CONFIRMING:
            continue
        e = w["expected_result"]
        if e.startswith("["):
            out.append(f"{w['validation_id']} (the printed change column {e.split(']')[0]}])")
        elif w["rule_applied"] == "cross_document":
            out.append(f"{w['validation_id']} (another document prints it)")
        elif "agency totals" in e:
            out.append(f"{w['validation_id']} (the agency totals sum to it)")
        else:
            out.append(f"{w['validation_id']} (a printed sum)")
    return out


def main():
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    by_obs = collections.defaultdict(list)
    for v in data["validations"]:
        by_obs[v["observation_id"]].append(v)
    # the extraction files, by source document (the comparative-table observations, memo lines included)
    pkg_to_src = {}
    for d in data["source_docs"]:
        m = re.search(r"(CRPT-\d+[hs]rpt\d+)", d["url_or_identifier"] or "", re.I)
        if m:
            pkg_to_src[m.group(1).upper()] = d["document_id"]
    ext = {}
    for f in glob.glob(str(ROOT / "extractions" / "*.title-ii.json")):
        name = Path(f).name.split(".")[0]
        src = pkg_to_src.get(name.upper()) or \
            {"MANUAL-LHHS-FY2023-SenateReported-explanatory_statement-88301550": "SRC-EXPL-LHHS-FY2023-SENATE",
             "MANUAL-LHHS-FY2024-HouseReported-explanatory_statement-408e3388": "SRC-EXPL-LHHS-FY2024-HOUSE"}.get(name)
        if src:
            ext[src] = json.loads(Path(f).read_text())["observations"]

    rows = []
    for v in data["validations"]:
        if v["human_review_status"] != "pending":
            continue
        o = obs[v["observation_id"]]
        others = confirming_passes(by_obs[o["observation_id"]], v)
        fam, fmt = family(v, o, others)
        if fam is None:
            fam, fmt = memo_family(v, o, ext)
        disp, reason = FAMILIES[fam]
        key = (v["rule_applied"], v["result"], o["verification_status"], acc[o["canonical_account_id"]]["subcommittee"],
               o["source_document_id"])
        rows.append({"id": v["validation_id"], "observation_id": o["observation_id"], "family": fam, "key": key,
                     "disposition": disp, "reason": reason.format(**fmt),
                     "example": f"{v['validation_id']} {o['canonical_account_id']} FY{o['fiscal_year']} {o['stage']}: "
                                f"{v['expected_result'][:90]} | {v['observed_result'][:70]}"})

    # the page-check items (review_list.csv): observations whose figure the text search didn't find on its page
    with open(REVIEW_LIST, newline="") as f:
        pages = [r for r in csv.DictReader(f) if r["reason"] == "page not confirmed by text search"]
    for r in pages:
        o = obs[r["id"]]
        sect = o["source_table_or_section"] or ""
        if re.search(r"rollup\b.*\bsum of|^sum of", sect, re.I):
            fam, fmt = "page-derived", {}
        elif "nearest page with it" in r["detail"]:
            n = int(re.search(r"is \+?(-?\d+) pages away", r["detail"]).group(1))
            fam, fmt = "page-nearby", {"where": f"{abs(n)} pages {'before' if n < 0 else 'after'} source_page {o['source_page']}"}
        else:
            fam, fmt = "page-not-found", {}
        disp, reason = FAMILIES[fam]
        key = ("page_check", "not confirmed", o["verification_status"], acc[o["canonical_account_id"]]["subcommittee"],
               o["source_document_id"])
        rows.append({"id": r["id"], "observation_id": r["id"], "family": fam, "key": key, "disposition": disp,
                     "reason": reason.format(**fmt), "example": f"{r['id']} {o['canonical_account_id']} FY{o['fiscal_year']} "
                                                               f"{o['stage']}: {r['detail'][:120]}"})

    # groups: family within the key, numbered in order of the family table then size
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["family"],) + r["key"]].append(r)
    order = list(FAMILIES)
    gids = {}
    for i, g in enumerate(sorted(groups, key=lambda g: (order.index(g[0]), -len(groups[g]), g)), start=1):
        gids[g] = f"G{i:03d}"
    for g, rs in groups.items():
        for r in rs:
            r["group"] = gids[g]

    with open(HERE / "triage_proposal.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["id", "group", "proposed_disposition", "reason", "family", "observation_id"])
        for r in sorted(rows, key=lambda r: (r["group"], r["id"])):
            w.writerow([r["id"], r["group"], r["disposition"], r["reason"], r["family"], r["observation_id"]])

    with open(HERE / "triage_groups.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["group", "family", "rule_applied", "result", "verification_status", "subcommittee",
                    "source_document_id", "count", "proposed_disposition", "reason", "examples"])
        for g in sorted(groups, key=lambda g: gids[g]):
            rs = sorted(groups[g], key=lambda r: r["id"])
            ex = [rs[0], rs[len(rs) // 2], rs[-1]] if len(rs) >= 3 else rs
            reasons = sorted({r["reason"] for r in rs})
            w.writerow([gids[g], *g, len(rs), rs[0]["disposition"],
                        reasons[0] if len(reasons) == 1 else f"{len(reasons)} variants, e.g. {reasons[0]}",
                        " || ".join(dict.fromkeys(r["example"] for r in ex))])

    # the flagged observations and what flags each
    import validate_approps as V                          # noqa: E402  (repo root on sys.path below)
    fam_of = {r["id"]: r["family"] for r in rows}
    flagged = [o for o in data["observations"] if o["verification_status"] == "flagged"]
    with open(HERE / "flagged_observations.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["observation_id", "canonical_account_id", "fiscal_year", "stage", "amount", "source_document_id",
                    "source_page", "failing_checks", "families", "proposed_dispositions"])
        for o in flagged:
            bad = [v for v in by_obs[o["observation_id"]]
                   if v["result"] == "fail" or V.review_flag(v["rule_applied"], v["result"], v["expected_result"])]
            w.writerow([o["observation_id"], o["canonical_account_id"], o["fiscal_year"], o["stage"], o["amount"],
                        o["source_document_id"], o["source_page"],
                        " || ".join(f"{v['validation_id']} {v['rule_applied']} {v['result']}: {v['expected_result'][:100]}" for v in bad),
                        "; ".join(sorted({fam_of.get(v["validation_id"], "-") for v in bad})),
                        "".join(sorted({FAMILIES[fam_of[v["validation_id"]]][0] for v in bad if v["validation_id"] in fam_of}))])

    # the summary
    pend = [r for r in rows if not r["key"][0] == "page_check"]
    print(f"{len(pend)} pending records + {len(rows) - len(pend)} page-check items in {len(groups)} groups; "
          f"{len(flagged)} flagged observations")
    fams = collections.defaultdict(lambda: [0, set()])
    for r in rows:
        fams[r["family"]][0] += 1
        fams[r["family"]][1].add(r["group"])
    for fam in FAMILIES:
        if fam in fams:
            print(f"{FAMILIES[fam][0]}  {fam:24s} {fams[fam][0]:4d} records  {len(fams[fam][1]):3d} groups")
    print(collections.Counter(r["disposition"] for r in pend), collections.Counter(r["disposition"] for r in rows if r not in pend))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    main()
