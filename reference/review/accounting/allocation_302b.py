"""
D-8: each held committee report's comparison of the bill with its subcommittee's 302(b) allocation (the table the
report prints under section 308(a) of the Congressional Budget Act, prepared in consultation with CBO), read from
the report's text layer into allocation_302b_text.csv beside this file. One row per printed row (Discretionary,
Mandatory, and the Senate's Security / Nonsecurity, Defense / Non-defense splits) with the four printed cells in
the report's own order of meaning: the allocation's budget authority and outlays, the bill's budget authority and
outlays, in millions of dollars as printed. A cell the table prints as a leader ('....') or 'NA' is blank (blank
is not zero); a footnote mark is dropped (it is kept in the row text).

Each number is put in the column whose header it sits under (the header words' x positions on the page). This is
the second reading: the rows recorded are allocation_302b_rows.csv, read from the page images, whose 'text_layer'
column says where this reading agrees (d8_records.py records from that file).

    python reference/review/accounting/allocation_302b.py --cache document_store   # downloads what it lacks

Needs PyMuPDF (requirements.txt) and the report PDFs (gitignored document_store/, fetched from the document's
address in data/staged.json).
"""

import argparse
import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / "allocation_302b_text.csv"
COLUMNS = ["source_document_id", "pdf_page", "printed_page", "chamber", "row", "label", "allocation_ba",
           "allocation_outlays", "bill_ba", "bill_outlays", "footnotes", "row_text"]
START = re.compile(r"Comparison of amounts in the bill with|COMPARISON WITH (THE )?BUDGET RESOLUTION|SEC\. 308\(A\)")
ROWS = re.compile(r"^(Discretionary|Mandatory|Security|Nonsecurity|Non-security|Defense|Non-?defense|General purpose"
                  r"|Base|Emergency|Program integrity)\b", re.I)
NUM = re.compile(r"^\$?\d{1,3}(,\d{3})*\*?$")
BLANK = re.compile(r"^(\.{2,}|NA|—|-)$")


def fetch(doc, cache):
    pdf = cache / f"{doc['document_id']}.pdf"
    if not pdf.exists():
        req = urllib.request.Request(doc["url_or_identifier"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r:
            pdf.write_bytes(r.read())
    return pdf


def lines_of(page):
    """[(y, [(x0, x1, text, size)])] for each printed line of the page, words in x order."""
    out = {}
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for m in re.finditer(r"\S+", sp["text"]):
                    # the span's x range spread over its characters: good enough to place a word in a column
                    n = max(len(sp["text"]), 1)
                    w = (sp["bbox"][2] - sp["bbox"][0]) / n
                    x0 = sp["bbox"][0] + w * m.start()
                    x1 = sp["bbox"][0] + w * m.end()
                    y = round((sp["bbox"][1] + sp["bbox"][3]) / 2)
                    out.setdefault(y, []).append((x0, x1, m.group(), sp["size"]))
    merged = []
    for y in sorted(out):
        if merged and y - merged[-1][0] <= 3:
            merged[-1][1].extend(out[y])
        else:
            merged.append((y, list(out[y])))
    return [(y, sorted(ws)) for y, ws in merged]


def header_columns(lines, chamber):
    """(the four columns' x centers, in the order allocation BA, allocation outlays, bill BA, bill outlays; the
    index of the header line), or None."""
    if chamber == "House":
        # '302(b) Allocation' | 'This Bill', each over 'Budget Authority' | 'Outlays'
        for i, (_, ws) in enumerate(lines):
            heads = [(x0, x1) for x0, x1, t, _ in ws if t in ("Budget", "Outlays")]
            if len(heads) >= 4:
                cs = []
                for i, (x0, x1, t, _) in enumerate(ws):
                    if t == "Budget":
                        nxt = ws[i + 1] if i + 1 < len(ws) else (x0, x1, "", 0)
                        cs.append((x0 + nxt[1]) / 2)
                    elif t == "Outlays":
                        cs.append((x0 + x1) / 2)
                if len(cs) == 4:
                    return cs, i
    else:
        # 'Budget authority' | 'Outlays', each over 'Committee allocation' | 'Amount in bill'
        for i, (_, ws) in enumerate(lines):
            heads = [(x0, x1) for x0, x1, t, _ in ws if t in ("Committee", "Amount")]
            if len(heads) == 4:
                c = [(x0 + x1) / 2 for x0, x1 in heads]
                # the line under them ('allocation', 'in bill') is wider: center each pair of lines
                if i + 1 < len(lines):
                    under = [w for w in lines[i + 1][1] if w[2] in ("allocation", "in")]
                    if len(under) == 4:
                        c = [(a + (u[0] + u[1]) / 2) / 2 for a, u in zip(c, under)]
                return [c[0], c[2], c[1], c[3]], i
    return None


def read_table(pdf_path, chamber):
    doc = fitz.open(pdf_path)
    for pno in range(len(doc)):
        page = doc[pno]
        if not START.search(page.get_text()):
            continue
        lines = lines_of(page)
        got = header_columns(lines, chamber)
        if got is None:
            continue
        cols, start = got
        printed = next((ws[0][2] for _, ws in lines[:3] if ws and ws[0][2].isdigit()), "")
        out = []
        for _, ws in lines[start:start + 16]:
            text = " ".join(w[2] for w in ws)
            if re.match(r"^(Projection|Financial|\d ?Includes|\* ?Includes|NOTE)", text):
                break
            m = ROWS.match(text)
            if not m:
                continue
            body = [w for w in ws if w[0] > ws[0][1]]
            sizes = sorted(w[3] for w in body if NUM.match(w[2]))
            big = sizes[-1] if sizes else 0
            cells, notes = [None] * 4, []
            for x0, x1, t, size in body:
                if NUM.match(t) and size < big - 1:
                    notes.append(t)             # a footnote mark: smaller type
                    continue
                if not (NUM.match(t) or BLANK.match(t)) or cols is None:
                    continue
                c = min(range(4), key=lambda k: abs((x0 + x1) / 2 - cols[k]))
                if NUM.match(t):
                    if t.endswith("*"):
                        notes.append("*")
                    cells[c] = int(t.replace(",", "").replace("$", "").rstrip("*"))
            label = re.sub(r"[ .]+$", "", re.split(r"\s\.{2,}", text)[0]).strip()
            out.append({"pdf_page": pno + 1, "printed_page": printed, "row": label.lower(), "label": label,
                        "allocation_ba": cells[0], "allocation_outlays": cells[1], "bill_ba": cells[2],
                        "bill_outlays": cells[3], "footnotes": " ".join(notes), "row_text": text})
        if out:
            return out, cols
    return [], None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", default=str(ROOT / "document_store"))
    args = p.parse_args(argv)
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    data = json.loads((ROOT / "data" / "staged.json").read_text())
    rows = []
    for d in sorted(data["source_docs"], key=lambda d: d["document_id"]):
        if d["document_type"] != "committee_report":
            continue
        chamber = "House" if "HRPT" in d["document_id"] else "Senate"
        got, cols = read_table(fetch(d, cache), chamber)
        if not got:
            print(f"{d['document_id']}: no comparison table found")
        if cols is None:
            print(f"{d['document_id']}: column headers not found")
        for r in got:
            rows.append({"source_document_id": d["document_id"], "chamber": chamber, **r})
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r[k] for k in COLUMNS})
    print(f"{len(rows)} rows -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
