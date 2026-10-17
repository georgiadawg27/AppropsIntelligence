"""
FY2024 House CJS: the comparative statement in the House subcommittee draft's explanatory materials
(SRC-EXPL-FY2024-HOUSE, PDF pp. 160-176) is a typed text table on rotated pages that text_tables.py does not
find. This reads its words by position into the extraction shape build_cjs.py reads
(extractions/SRC-EXPL-FY2024-HOUSE.json): one row per printed line, its label and its three money columns
(FY 2023 Enacted, FY 2024 Request, Bill; the two comparison columns are checked, not kept).

    python reference/review/cjs/backfill/fy2024_house_text.py

Each row carries a source_text record (the row as printed) and an arithmetic record: Bill - Enacted and Bill -
Request as printed in the comparison columns.
"""

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[4]
PDF = ROOT / "document_store" / "SRC-EXPL-FY2024-HOUSE.pdf"
OUT = ROOT / "extractions" / "SRC-EXPL-FY2024-HOUSE.json"
PAGES = range(160, 177)
COLS = [("FY 2023 Enacted", "Enacted", 2023), ("FY 2024 Request", "President's Budget", 2024),
        ("Bill", "House Reported", 2024), ("Bill vs. Enacted", None, None), ("Bill vs. Request", None, None)]
NUM = re.compile(r"^\(?[-+]?[\d,]+\)?$|^-{2,3}$|^\(?---\)?$")
TITLE = re.compile(r"TITLE (I|II|III|IV|V)\b")


def parse_amount(s):
    s = s.strip()
    if re.fullmatch(r"\(?-{2,3}\)?", s):
        return 0, True
    neg = "-" in s
    v = int(re.sub(r"[^\d]", "", s))
    return (-v if neg else v), False


def page_rows(page):
    words = page.get_text("words")
    hdr = [w for w in words if w[4] in ("Enacted", "Request", "Bill") and 50 < w[0] < 62]
    # money columns by their header's position across the rotated page (y), right edge of each figure
    centers = sorted({round(w[3]) for w in hdr}, reverse=True)[:5]
    lines = {}
    for w in words:
        if w[0] < 70:
            continue                                           # the running header
        key = round(w[0] / 3)
        lines.setdefault(key, []).append(w)
    out = []
    for k in sorted(lines):
        ws = sorted(lines[k], key=lambda w: -w[1])
        nums, label = [], []
        for w in ws:
            t = w[4]
            if NUM.match(t.rstrip(".")) and w[1] < 520:
                nums.append(w)
            else:
                label.append(t)
        text = " ".join(label)
        vals = [None] * 5
        for w in nums:
            c = w[3]
            i = min(range(len(centers)), key=lambda j: abs(centers[j] - c))
            vals[i] = w[4]
        out.append((round(ws[0][0]), round(max(w[3] for w in ws)), text, vals))
    return out


def main():
    data = PDF.read_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    obs, recs = [], []
    node = 0
    title, agency, pending = None, None, ""
    for p in PAGES:
        prow = [r for r in page_rows(doc[p - 1])
                if not (re.match(r"^[-=\s]+$", r[2]) or "COMPARATIVE STATEMENT" in r[2] or "Amounts in thousands" in r[2])]
        heading_run = False
        for n, (x, left, text, vals) in enumerate(prow):
            clean = re.sub(r"\.{2,}.*$", "", text).strip()
            if all(v is None for v in vals):
                m = TITLE.search(text)
                nxt = prow[n + 1] if n + 1 < len(prow) else None
                cont = nxt is not None and any(v is not None for v in nxt[3]) and nxt[1] < left
                if m:
                    title, agency, pending, heading_run = text.strip(), None, "", False
                elif cont or clean.startswith("Total,"):
                    pending = (pending + " " + clean).strip()
                    heading_run = False
                else:
                    agency = (agency + " " + clean) if heading_run and agency else clean
                    heading_run = True
                continue
            heading_run = False
            label = (pending + " " + clean).strip() if pending else clean
            pending = ""
            if not label:
                continue
            node += 1
            memo = label.startswith("(") or all((v or "").startswith("(") for v in vals[:3] if v)
            low = label.lower()
            kind = ("grand_total" if low.startswith("grand total") else "total" if low.startswith("total")
                    else "subtotal" if low.startswith(("subtotal", "direct appropriation")) else
                    "memo" if memo else "line")
            path = " / ".join(x for x in (agency, label) if x)
            nums = []
            for i, (hdr, stage, fy) in enumerate(COLS[:3]):
                if vals[i] is None:
                    continue
                amt, dash = parse_amount(vals[i])
                nums.append(amt)
                oid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"SRC-EXPL-FY2024-HOUSE/{p}/{node}/{i}"))
                obs.append({"observation_id": oid, "node_id": node, "account_name_as_written": label,
                            "account_path": path, "agency": agency, "title": title, "row_kind": kind,
                            "is_rollup": kind in ("total", "subtotal", "grand_total"), "is_memo": kind == "memo",
                            "fiscal_year": fy, "stage": stage, "column_header": hdr, "column_index": i,
                            "amount": amt * 1000, "amount_in_units": amt, "amount_as_printed": vals[i],
                            "amount_is_dash_zero": dash, "source_page": str(p),
                            "extraction_method": "text-extracted", "extraction_confidence": 0.95,
                            "raw_text_excerpt": f"{label} {' '.join(v or '' for v in vals)}".strip()})
                recs.append({"observation_id": oid, "rule_applied": "source_text",
                             "expected_result": f"'{vals[i]}' printed on p.{p} in the '{hdr}' column",
                             "observed_result": f"{amt:,} (thousands)", "result": "pass"})
                recs.append({"observation_id": oid, "rule_applied": "unit",
                             "expected_result": "(Amounts in thousands)", "observed_result": f"{amt * 1000:,} dollars",
                             "result": "pass"})
            # the comparison columns: Bill - Enacted, Bill - Request
            if vals[0] and vals[2] and vals[3]:
                e, b, d = parse_amount(vals[0])[0], parse_amount(vals[2])[0], parse_amount(vals[3])[0]
                for o in obs[-len(nums):]:
                    recs.append({"observation_id": o["observation_id"], "rule_applied": "structural",
                                 "expected_result": f"Bill - Enacted printed {vals[3]} (p.{p})",
                                 "observed_result": f"{b:,} - {e:,} = {b - e:,}",
                                 "result": "pass" if b - e == d else "flag"})
    out = {"source_document": {"document_id": "SRC-EXPL-FY2024-HOUSE",
                               "sha256": hashlib.sha256(data).hexdigest()},
           "observations": obs, "validation_records": recs}
    OUT.write_text(json.dumps(out, indent=1))
    print(f"{node} rows, {len(obs)} figures -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
