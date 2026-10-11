"""
Table lines read for components (accounting views, phase 1): lines the figure's own comparative table prints
inside an account's total that no observation holds yet.

Vaccine Injury Compensation Program Trust Fund: the table prints 'Post-FY 1988 Claims' and 'HRSA Administrative
expenses' (or 'HRSA administration') above 'Total, Vaccine Injury Compensation Trust Fund'. The administrative
line is the trust-fund limitation the bill and law text appropriate ('not to exceed $X shall be available from
the Trust Fund'); the claims are such sums as may be necessary and print no dollar amount in the text. Each
VICTF headline's administrative line, in the headline's own column, becomes a trust_fund_limitation component
(scripts/tag_components.py reads table_lines.csv).

    python reference/review/accounting/table_lines.py --cache document_store     # downloads what it lacks

Needs pdftotext (poppler). The PDFs are public (govinfo, congress.gov, the committees); nothing here uses a key.
A column is found by position: the administrative line's number that sits under the total's number equal to
the headline. Nothing is guessed: a page without the line, or without the headline in the total, is reported.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "table_lines.csv"
ACCOUNT = "ACC-HHS-HRSA-VICTF"
TOTAL = re.compile(r"Total,? Vaccine Injury Compensation", re.I)
ADMIN = re.compile(r"^\s*HRSA Administrati(?:ve expenses|on)\b", re.I)
# Pages whose text layer lacks the table (an image, or OCR too broken to place a column): read from the page
# image by eye on 2026-10-11 (Claude Code), the column checked against the total the headline records.
# observation_id -> (administrative line in thousands, the column it was read from, the total that column prints)
IMAGE_READS = {
    "OBS-LHHS-1788": (15_200, "Bill", 276_697),               # FY2024 House draft explanatory materials p.256
    "OBS-LHHS-0313": (15_700, "Bill", 282_427),               # H.Rept. 118-585 p.313 (printed 309)
    "OBS-LHHS-0316": (15_200, "FINAL BILL", 287_262),         # Congressional Record H1636, 2026-01-22
    "OBS-LHHS-0317": (15_200, "Bill", 287_262),               # H.Rept. 119-271 p.340 (printed 336)
    "OBS-LHHS-0318": (15_200, "FY 2026 Request", 287_262),
    "OBS-LHHS-0478": (15_200, "FY 2025 Estimate", 281_927),
    "OBS-LHHS-0320": (15_200, "Committee Recommendation", 291_878),   # H.Rept. 119-696 p.371 (printed 367)
    "OBS-LHHS-0321": (15_200, "FY 2027 Request*", 291_878),
    "OBS-LHHS-2312": (10_200, "Bill", 275_800),               # H.Rept. 116-450 p.398 (printed 394)
    "OBS-LHHS-2409": (16_200, "FY 2021 Request", 281_800),
    "OBS-LHHS-2903": (10_200, "FY 2020 Enacted", 295_800),
    "OBS-LHHS-3768": (8_350, "Bill", 248_350),                # H.Rept. 114-699 p.235
    "OBS-LHHS-3865": (9_200, "FY 2017 Request", 249_200),
    "OBS-LHHS-2712": (11_200, "Bill", 319_200),               # H.Rept. 116-62 p.332 (printed 328)
    "OBS-LHHS-2804": (11_200, "FY 2020 Request", 319_200),
    "OBS-LHHS-3282": (9_200, "FY 2019 Enacted", 317_200),
    "OBS-LHHS-3661": (9_200, "FY 2018 Enacted", 277_200),     # H.Rept. 115-862 p.361
}
NUM = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])|(?<![\d,])\d{1,3}(?![\d,])")


def fetch(url, dest):
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (approps-intelligence table lines)"})
        with urllib.request.urlopen(req, timeout=120) as r:
            dest.write_bytes(r.read())
    return dest


def page_text(pdf, page):
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout


def numbers(line):
    """[(value, end column)] for each number printed on a table line (after its leader dots)."""
    m = re.search(r"\.{3,}", line)
    start = m.end() if m else 0
    return [(int(x.group().replace(",", "")), x.end()) for x in NUM.finditer(line, start)]


def read(pdf, page, headline):
    """-> (amount in thousands, the admin line, the total line) or (None, why, '')."""
    lines = page_text(pdf, page).splitlines()
    tot = next((i for i, x in enumerate(lines) if TOTAL.search(x)), None)
    if tot is None:
        return None, "no 'Total, Vaccine Injury Compensation' line on the page", ""
    admin = next((i for i in range(tot - 1, max(tot - 8, -1), -1) if ADMIN.search(lines[i])), None)
    if admin is None:
        return None, "no 'HRSA Administrative expenses' line above the total", ""
    t = numbers(lines[tot])
    col = [end for v, end in t if v * 1000 == headline]
    if not col:
        return None, f"the total line does not print {headline // 1000:,}", ""
    a = [v for v, end in numbers(lines[admin]) if abs(end - col[0]) <= 2]
    if len(a) != 1:
        return None, "no administrative amount under the headline's column", ""
    return a[0], " ".join(lines[admin].split()), " ".join(lines[tot].split())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / "document_store")
    a = ap.parse_args(argv)
    a.cache.mkdir(exist_ok=True)
    data = json.loads((ROOT / "data" / "staged.json").read_text())
    docs = {d["document_id"]: d for d in data["source_docs"]}
    rows, misses = [], []
    for o in sorted(data["observations"], key=lambda o: o["observation_id"]):
        if (o["canonical_account_id"] != ACCOUNT or o.get("component") or o["amount_type"] != "budget authority"
                or o["verification_status"] == "superseded"):
            continue
        d = docs[o["source_document_id"]]
        pdf = fetch(d["url_or_identifier"], a.cache / f"{o['source_document_id']}.pdf")
        page = int(re.match(r"\d+", o["source_page"]).group())
        amt, line, total = read(pdf, page, o["amount"])
        how = "text layer"
        if amt is None and o["observation_id"] in IMAGE_READS:
            amt, col, tot = IMAGE_READS[o["observation_id"]]
            if tot * 1000 != o["amount"]:
                raise SystemExit(f"{o['observation_id']}: the image read's total {tot:,} is not the headline")
            line = f"HRSA Administrative expenses {amt:,} [{col}]"
            total = f"Total, Vaccine Injury Compensation Trust Fund {tot:,} [{col}]"
            how = "page image, read by eye"
        if amt is None:
            misses.append(f"{o['observation_id']} ({o['source_document_id']} p.{page}): {line}")
            continue
        rows.append({"observation_id": o["observation_id"], "source_document_id": o["source_document_id"],
                     "page": page, "line": "HRSA Administrative expenses", "amount": amt * 1000,
                     "printed_line": line, "printed_total": total, "read_from": how})
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} lines -> {OUT.relative_to(ROOT)}")
    for m in misses:
        print("not read:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
