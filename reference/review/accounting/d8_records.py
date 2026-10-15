"""
D-8: each held committee report's comparison of the bill with its subcommittee's 302(b) allocation, recorded at bill
level in the report's own words (reference/specs/anchored-totals.md rule 1; ACCOUNTING_RULES 'Totals' 7): one
observation per printed cell of its Discretionary ('General purpose discretionary') and Mandatory rows, on the
subcommittee's bill total, for the report's fiscal year and stage, as a view of the bill total the same report
records (D-7). Budget authority is amount_type 'budget authority', outlays 'outlay'; millions as printed, times
1,000,000. Supersede, never overwrite; no ID reused; blank is not zero (a leader or 'NA' records nothing).

Reads allocation_302b_rows.csv (the page images, checked against the text layer: allocation_302b.py). Not recorded,
each with why in allocation_302b_lines.csv: the Security / Defense splits (the spec records discretionary and
mandatory), a report whose bill total D-7 doesn't hold (no headline to be a view of), and a cell HELD below (printed
under a header its own report contradicts: for the owner).

    python reference/review/accounting/d8_records.py            # what would change
    python reference/review/accounting/d8_records.py --write    # write it (idempotent)
"""

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
STAGED = ROOT / "data" / "staged.json"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
sys.path.insert(0, str(ROOT))
import accounts as A  # noqa: E402
import ids as IDS  # noqa: E402

ROWS = HERE / "allocation_302b_rows.csv"
OUT = HERE / "allocation_302b_lines.csv"
# printed cell -> (component prefix, amount_type, the column as the report heads it)
CELLS = {"allocation_ba": ("allocation_302b", "budget authority", "allocation, budget authority"),
         "allocation_outlays": ("allocation_302b", "outlay", "allocation, outlays"),
         "bill_ba": ("bill_302b", "budget authority", "this bill, budget authority"),
         "bill_outlays": ("bill_302b", "outlay", "this bill, outlays")}
STAGE_CHAMBER = {"House Reported": "House", "Senate Reported": "Senate"}
# cells not recorded until the owner rules (a printed position its own report's text contradicts)
HELD = {("SRC-CRPT-118HRPT585", "Discretionary", "allocation_outlays"):
        "printed under '302(b) Allocation, Outlays' with the budget-authority cell blank; the report's own text calls "
        "$185,797,000,000 the 302(b) allocation of current year discretionary funding (H.Rept. 118-585): which "
        "column it belongs to is for the owner"}


def kind_of(label):
    low = label.lower()
    if "discretionary" in low:
        return "discretionary"
    if low.startswith("mandatory"):
        return "mandatory"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    data = json.loads(STAGED.read_text())
    retired = json.loads(RETIRED.read_text())
    docs = {s["document_id"]: s for s in data["source_docs"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    sub_of_doc = {}
    for o in data["observations"]:
        sub_of_doc.setdefault(o["source_document_id"], acc[o["canonical_account_id"]]["subcommittee"])
    bill = {a["subcommittee"]: a["canonical_account_id"] for a in data["accounts"] if a.get("total_scope") == "bill"}
    alloc = IDS.Allocator([o["observation_id"] for o in data["observations"]] + list(retired.get("observations", {})))
    live = {(o["canonical_account_id"], int(o["fiscal_year"]), o["stage"], o["amount_type"], o.get("component") or ""): o
            for o in data["observations"] if o["verification_status"] != "superseded"}
    added, kept, report = [], [], []
    for r in csv.DictReader(ROWS.open()):
        doc = docs[r["source_document_id"]]
        sc, fy, st = sub_of_doc[doc["document_id"]], int(doc["fiscal_year"]), doc["stage"]
        kind = kind_of(r["row_label"])
        base = [sc, fy, st, doc["document_id"], r["pdf_page"], r["printed_page"], r["row_label"]]
        if kind is None:
            report.append(base + ["", "", "not recorded: a split of the discretionary row (the spec records "
                                          "discretionary and mandatory)"])
            continue
        aid = bill.get(sc)
        head = live.get((aid, fy, st, "budget authority", ""))
        for col, (prefix, atype, words) in CELLS.items():
            val = r[col]
            if val == "":
                continue                    # blank is not zero: the table prints a leader or NA here
            amount = int(val) * 1_000_000
            if (doc["document_id"], r["row_label"], col) in HELD:
                report.append(base + [words, amount, "held for the owner: " + HELD[(doc["document_id"], r["row_label"], col)]])
                continue
            if head is None or head["source_document_id"] != doc["document_id"]:
                report.append(base + [words, amount, "not recorded: D-7 holds no bill total from this report for "
                                                     "the cell (no headline to be a view of)"])
                continue
            comp = f"{prefix}_{kind}"
            key = (aid, fy, st, atype, comp)
            old = live.get(key)
            if old is not None:
                if old["amount"] == amount:
                    kept.append(old)
                    report.append(base + [words, amount, f"already held, equal {old['observation_id']}"])
                else:
                    report.append(base + [words, amount, f"not recorded: {old['observation_id']} holds "
                                                         f"{old['amount']:,}"])
                continue
            o = {"observation_id": alloc.next("OBS", sc), "canonical_account_id": aid, "fiscal_year": fy,
                 "stage": st, "chamber": STAGE_CHAMBER.get(st, "N/A"), "bill_id": "", "report_id": "",
                 "amount": amount, "amount_type": atype, "offsetting_collections": "FALSE",
                 "transfer_link_account_id": "", "source_document_id": doc["document_id"],
                 "source_page": str(r["pdf_page"]),
                 "source_table_or_section": f"Section 308(a) comparison with the 302(b) allocation -- printed as "
                                            f"'{r['row_label']}' [{words}] (printed page {r['printed_page']}, "
                                            f"in millions)",
                 "extraction_method": "AI-extracted", "confidence": 0.95, "verification_status": "unverified",
                 "component": comp, "headline_observation_id": head["observation_id"]}
            added.append(o)
            live[key] = o
            report.append(base + [words, amount, f"recorded {o['observation_id']} {comp} ({atype})"])
    comps_new = [{"component_id": c, "label": A.SUMMARY_COMPONENTS[c][0], "kind": A.SUMMARY_COMPONENTS[c][1],
                  "description": A.SUMMARY_COMPONENTS[c][2]}
                 for c in sorted({o["component"] for o in added})
                 if c not in {x["component_id"] for x in data["components"]}]
    print(f"components added: {', '.join(c['component_id'] for c in comps_new) or 'none'}")
    print(f"observations added: {len(added)}; already held, equal: {len(kept)}")
    for k, n in sorted(collections.Counter((o["canonical_account_id"], o["stage"]) for o in added).items()):
        print(f"  {k[0]} {k[1]}: {n}")
    why = collections.Counter(x[-1].split(":")[0] if not x[-1].startswith("recorded") else "recorded" for x in report)
    for k, n in why.most_common():
        print(f"  {n:4d}  {k}")
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["subcommittee", "fiscal_year", "stage", "source_document_id", "pdf_page", "printed_page",
                    "row", "cell", "amount", "outcome"])
        w.writerows(report)
    if args.write and added:
        data["components"] += comps_new
        data["observations"] += added
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")


if __name__ == "__main__":
    sys.exit(main())
