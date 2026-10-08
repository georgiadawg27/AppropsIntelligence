"""
Report only: H.R. 5894 (FY2024 Labor-HHS, as introduced) read as bill text, and the July 14,
2023 subcommittee mark against it.

    python reference/review/lhhs/fy2024_house/bill_text.py BILLS-118hr5894ih.xml BILLS-118hr5894ih.pdf MARK.pdf

1. The bill's Title II (HHS) appropriation headings and the first dollar amount in each heading's
   paragraph, from govinfo's bill XML (the method of reference/review/congress_api: the
   <appropriations-major|intermediate|small> headers, an "(including transfer of funds)"
   sub-header folded into the heading above it). Written to hr5894_title_ii.csv.
2. The same headings and amounts read from the text of both PDFs -- H.R. 5894 and the
   subcommittee mark -- with one parser, so the two are compared like for like; every heading
   whose amount differs, or that only one prints, goes to mark_vs_hr5894.csv.
3. Our FY2024 House figures (data/staged.json, from the explanatory materials' table) against those
   amounts: each account's headline, matched to the heading of the same name (its canonical name or
   the label the table printed), -> extracted_vs_hr5894.csv.
Nothing here changes data/staged.json.
"""

import csv
import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def xml_title_ii(path):
    s = Path(path).read_text(encoding="utf-8")
    titles = [(m.start(), html.unescape(re.sub("<[^>]+>", "", m.group(1))).strip())
              for m in re.finditer(r"<title\b[^>]*>\s*<enum>[^<]*</enum>\s*<header\b[^>]*>(.*?)</header>", s, re.S)]
    out, agency = [], ""
    for m in re.finditer(r"<appropriations-(major|intermediate|small)\b[^>]*>\s*<header\b[^>]*>(.*?)</header>(.*?)</appropriations-\1>",
                         s, re.S):
        title = ([t for p, t in titles if p <= m.start()] or [""])[-1]
        if not title.upper().startswith("DEPARTMENT OF HEALTH AND HUMAN SERVICES"):
            continue
        hdr = html.unescape(re.sub("<[^>]+>", "", m.group(2))).strip()
        body = html.unescape(re.sub("<[^>]+>", " ", m.group(3)))
        if m.group(1) == "intermediate" and not hdr.startswith("("):
            agency = hdr                              # the agency heading the next accounts sit under
        amt = re.search(r"\$([\d,]+)", body)
        if hdr.startswith("(") and out:
            out[-1]["header_note"] = hdr
            if out[-1]["amount"] is None and amt:
                out[-1]["amount"] = int(amt.group(1).replace(",", ""))
            continue
        out.append({"level": m.group(1), "heading": hdr, "amount": int(amt.group(1).replace(",", "")) if amt else None,
                    "header_note": "", "agency": agency})
    return out


def pdf_title_ii(path):
    """Headings (an all-caps line) and the first $ amount before the next heading, Title II only."""
    import fitz
    doc = fitz.open(path)
    lines = []
    for page in doc:
        for ln in page.get_text().splitlines():
            ln = ln.strip()
            # page slugs: GPO's (VerDate ... Sfmt, E:\BILLS\..., "kjohnson on DSK...") and the
            # committee draft's (file paths, "July 12, 2023 (7:34 p.m.)", l:\v7\...)
            if not ln or re.fullmatch(r"\d{1,3}", ln) or ln.startswith(("VerDate", "Jkt ", "PO 0", "Frm ", "Fmt ", "Sfmt ", "•")) \
                    or re.search(r"[A-Za-z]:\\|\.XML\b|\.xml\b| on DSK|^H\d{4}$|^\d\d:\d\d |\(\d+:\d\d [ap]\.m\.\)", ln):
                continue
            lines.append(ln)
    text_lines, out, in_ii = lines, [], False
    cur = None
    for ln in text_lines:
        caps = re.sub(r"[^A-Za-z]", "", ln)
        if re.match(r"^TITLE II\b", ln):
            in_ii = True
            continue
        if re.match(r"^TITLE III\b", ln):
            break
        if not in_ii:
            continue
        if caps and caps.isupper() and len(caps) > 3 and not ln.startswith("SEC."):
            if cur and cur["amount"] is None and cur["heading"] and not cur.get("closed"):
                # consecutive caps lines: an agency heading then its account, or one heading on two lines
                if ln.startswith("("):
                    continue
                cur["heading"] = (cur["heading"] + " " + ln).strip() if cur["heading"].endswith(",") or cur["heading"].endswith("AND") else ln
                continue
            cur = {"heading": ln, "amount": None}
            out.append(cur)
            continue
        if cur and cur["amount"] is None:
            m = re.search(r"\$([\d,]+)", ln)
            if m:
                cur["amount"] = int(m.group(1).replace(",", ""))
    return out


def norm(h):
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\(.*?\)", "", h.lower())).strip()


def compare_extracted(rows):
    """Our FY2024 House headline figures (budget authority, no component) vs the bill's heading amounts."""
    import json
    data = json.loads((HERE.parents[3] / "data" / "staged.json").read_text())
    acct = {a["canonical_account_id"]: a for a in data["accounts"] if a["subcommittee"] == "LHHS"}
    by_head = {}
    for r in rows:
        by_head.setdefault(norm(r["heading"]), []).append(r)

    def pick(name, agency):
        cands = by_head.get(norm(name), [])
        same = [r for r in cands if norm(agency) in norm(r["agency"]) or norm(r["agency"]) in norm(agency)]
        return (same or cands or [None])[0]
    out = []
    for o in data["observations"]:
        if not (o["fiscal_year"] == 2024 and o["stage"] == "House Reported" and o["canonical_account_id"] in acct
                and o["amount_type"] == "budget authority" and not o["component"]):
            continue
        a = acct[o["canonical_account_id"]]
        m = re.search(r"printed as (['\"])(.*?)\1", o["source_table_or_section"])
        names = [a["canonical_name"], m.group(2) if m else ""]
        hit = next((pick(n, a["agency"]) for n in names if n and norm(n) in by_head), None)
        if a.get("total_scope"):
            res = "a total: the bill appropriates by heading, not by agency or title"
        elif hit is None:
            res = "no heading of that name in Title II"
        elif hit["amount"] is None:
            res = "no dollar amount in the heading's paragraph"
        else:
            res = "same" if hit["amount"] == o["amount"] else f"differs by {o['amount'] - hit['amount']:+,}"
        out.append([o["observation_id"], o["canonical_account_id"], a["canonical_name"], o["amount"],
                    hit["heading"] if hit else "", hit["amount"] if hit and hit["amount"] is not None else "", res])
    with open(HERE / "extracted_vs_hr5894.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["observation_id", "canonical_account_id", "canonical_name", "our_fy2024_house_dollars", "hr5894_heading",
                    "hr5894_first_amount_dollars", "result"])
        w.writerows(sorted(out, key=lambda r: r[1]))
    return out


def main(argv):
    xml, bill_pdf, mark_pdf = argv
    rows = xml_title_ii(xml)
    with open(HERE / "hr5894_title_ii.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agency_heading", "level", "heading", "first_amount_dollars", "header_note"])
        for r in rows:
            w.writerow([r["agency"], r["level"], r["heading"], r["amount"] if r["amount"] is not None else "", r["header_note"]])
    bill, mark = pdf_title_ii(bill_pdf), pdf_title_ii(mark_pdf)
    def index(rs):
        out = {}
        for r in rs:
            k = norm(r["heading"])
            out.setdefault(k, []).append(r)
        return out
    b, m = index(bill), index(mark)
    diffs = []
    for k in sorted(set(b) | set(m)):
        bl, ml = b.get(k, []), m.get(k, [])
        for i in range(max(len(bl), len(ml))):
            x = bl[i] if i < len(bl) else None
            y = ml[i] if i < len(ml) else None
            xa, ya = (x or {}).get("amount"), (y or {}).get("amount")
            if xa != ya:
                diffs.append([(x or y)["heading"], ya if ya is not None else "", xa if xa is not None else "",
                              ("only in the mark" if not x else "only in H.R. 5894" if not y else
                               f"{(xa or 0) - (ya or 0):+,}")])
    with open(HERE / "mark_vs_hr5894.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["heading", "mark_july_14_2023_dollars", "hr5894_introduced_dollars", "difference"])
        w.writerows(diffs)
    print(f"H.R. 5894 Title II: {len(rows)} headings from the XML, {sum(r['amount'] is not None for r in rows)} with an amount; "
          f"PDF text: {len(bill)} headings in H.R. 5894, {len(mark)} in the mark; {len(diffs)} differ")
    from collections import Counter
    cmp = compare_extracted(rows)
    print("extracted vs H.R. 5894:", dict(Counter(r[-1] if not r[-1].startswith("differs") else "differs" for r in cmp)))
    return rows


if __name__ == "__main__":
    main(sys.argv[1:])
