"""
D-7: the printed summary lines of each held committee report's comparative table, as printed.

Each report's (or explanatory statement's) comparative table ends with a summary under its grand total
(appropriations, emergency appropriations, rescissions, advance appropriations, trust funds, ...), and each title
total is followed by its own lines (federal funds, current year, advances, trust funds, mandatory and discretionary,
by transfer / transfer out ...). This reads those rows, every money column as printed, from the text layer:

    python reference/review/accounting/summary_rows.py --cache document_store      # downloads what it lacks

Writes summary_rows.csv beside this file: one row per printed row -- the document, PDF page, the block it closes
(bill or the title), the label as printed, each money column in thousands as printed (blank where the table prints
none), and whether the table's own comparison columns agree with it (`arithmetic`). A row whose comparison columns
don't agree with its money columns (an OCR misread) is not used: it is read from the page image instead
(summary_rows_images.csv, same columns, read_from 'page image'). Documents whose table pages have no text layer are
read from the page images only.

Needs pdftotext (poppler). The PDFs are public; nothing here uses a key. Nothing is fitted or guessed.
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
HERE = Path(__file__).resolve().parent
STAGED = ROOT / "data" / "staged.json"
OUT = HERE / "summary_rows.csv"
IMAGES = HERE / "summary_rows_images.csv"
COLUMNS = ["source_document_id", "pdf_page", "block", "seq", "label", "line", "c1", "c2", "c3", "d1", "d2",
           "arithmetic", "read_from", "row_text"]

# the documents whose comparative table is page images (no text layer on those pages): read from the images only
IMAGE_ONLY = {"SRC-CRPT-114HRPT605", "SRC-CRPT-114HRPT699", "SRC-CRPT-115HRPT704", "SRC-CRPT-116HRPT450",
              "SRC-CRPT-116HRPT455", "SRC-CRPT-117HRPT97", "SRC-CRPT-118HRPT582", "SRC-CRPT-118HRPT585",
              "SRC-CRPT-119HRPT271", "SRC-CRPT-119HRPT272", "SRC-CRPT-119HRPT652", "SRC-CRPT-119HRPT696",
              "SRC-EXPL-LHHS-FY2024-HOUSE", "SRC-EXPL-LHHS-FY2026-ENACTED", "SRC-EXPL-FY2026-PB"}

START = re.compile(r"^(Total,?\s+(?:title|tile)\b|Grand\s+total(?!,?\s+(?!this|current)[A-Z]{2})\b|RECAP\b|Mandatory,\s+total\s+in\s+bill|"
                   r"Discretionary,\s+total\s+in\s+bill)", re.I)
# rows a summary block carries (the vocabulary of the printed summaries); anything else ends the block
LINE = re.compile(r"^\(?\s*(federal\s+funds|current\s+year|trust\s+funds?|appropriations?\b|emergency|advance|"
                  r"new\s+advances|fiscal\s+year\s+\d{4}|fy\s*\d{4}|rescissions?|\(?recission|cures|21st|"
                  r"prevention\s+and\s+public|\(?by\s+transfer|\(?transfer\s+out|\(?mandatory|\(?discretionary|"
                  r"less\s+|plus\s+|subtotal|total,\s+(?:title|mandatory|discretionary|pandemic|prevention|"
                  r"scorekeeping)|total,?\s+title\b.*discretionary|grand\s+total|census|program\s+integrity|"
                  r"limitation|disaster|cap\s+adjustment|offsetting|other\s+appropriations\s*\(|budget\s+amendment)",
                  re.I)
SKIP = re.compile(r"^(VerDate|Jkt|PO\s+\d|Frm|Fmt|Sfmt|E:\\|HR\d|SR\d|Insert offset|\d{1,4}$|[A-Z]?\d{4,5}[A-Z.]*\d*$|"
                  r"COMPARATIVE STATEMENT|AND BUDGET|FOR FISCAL YEAR|AMOUNTS RECOMMENDED|\[In thousands|\(Amounts in|"
                  r"Item\b|Committee\b|recommendation|FY \d{4}|Senate Committee|compared with|Bill vs|Enacted|"
                  r"Request|Budget estimate|\d{4} appropriation|[=\-_ ]+$|h HEARING|rfrederick|\w+ on [A-Z0-9]+ with|"
                  r"Æ$|PsN:|Final Bill|\S+\s+\d{1,2}:\d{2}|Fiscal Year \d{4}\s*$)", re.I)
TITLE_HEAD = re.compile(r"^TITLE\s+[IVXL]+\b|^GENERAL PROVISIONS|^OTHER APPROPRIATIONS|^DISCRETIONARY RESCISSIONS|"
                        r"^CHANGES IN MANDATORY|^\d+\s+[A-Z]", re.I)
LEADER = re.compile(r"(?:\s?\.){2,}\s?[.,:\s]*")
NUM = re.compile(r"\(?\s?[∂¥+\-−·–]?\s?\d{1,3}(?:\s?[,']\s?\d{3})+\s?\)?|\(?[∂¥+\-−·–]?\d{1,3}\)?(?=\s|$)")
BLANK = re.compile(r"(?:\.\s?){2,}|—|(?<=\s)-(?=\s|$)")


def govinfo(url):
    m = re.search(r"/(\d{3})/crpt/([hs]rpt\d+)/", url or "")
    return f"https://www.govinfo.gov/content/pkg/CRPT-{m.group(1)}{m.group(2)}/pdf/CRPT-{m.group(1)}{m.group(2)}.pdf" \
        if m else url


def pages(doc, url, cache):
    pdf = cache / f"{doc}.pdf"
    if not pdf.exists():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                pdf.write_bytes(r.read())
        except OSError:
            req = urllib.request.Request(govinfo(url), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                pdf.write_bytes(r.read())
    txt = cache / f"{doc}.txt"
    if not txt.exists():
        subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)
    return txt.read_text(errors="replace").split("\f")


def amount(tok):
    """A printed amount in thousands: parentheses are the table's 'of which' marks, not a sign; ¥ / - / − are minus."""
    t = re.sub(r"[\s()]", "", tok)
    neg = t[:1] in "¥-−–·"
    t = re.sub(r"^[∂¥+\-−·–]", "", t)
    t = re.sub(r"[,']", "", t)
    return -int(t) if neg else int(t)


def split_row(raw):
    """-> (label, [(x_end, token)]) for one raw layout line: the label is what precedes the dot leader (or, where
    the OCR has none, the first run of two or more spaces after it)."""
    m = None
    for m in LEADER.finditer(raw):
        if m.start() > 0 and re.search(r"[A-Za-z)]", raw[:m.start()]):
            break
    else:
        m = None
    if m:
        label, start = raw[:m.start()].strip(), m.end()
    else:
        g = re.search(r"[A-Za-z)\]]\s{2,}(?=[(∂¥+\-−·–]?\s?\d)", raw)
        if not g:
            return raw.strip(), []
        label, start = raw[:g.start() + 1].strip(), g.end()
    toks = []
    rest = raw[start:]
    for t in NUM.finditer(rest):
        toks.append((start + t.end(), t.group().strip()))
    for b in BLANK.finditer(rest):
        toks.append((start + b.end(), None))
    toks.sort()
    return label, toks


def norm(label):
    """The line a printed label names (a key for the records; the label itself is kept as printed)."""
    l = re.sub(r"\s+", " ", label).strip(" .:").lower()
    l = l.replace("recission", "rescission")
    rules = [
        (r"^grand total,? this fiscal year", "grand_total_this_fiscal_year"),
        (r"^grand total", "grand_total"),
        (r"^total,? (?:title|tile) .*discretionary", "discretionary"),
        (r"^total,? (?:title|tile)\b", "title_total"),
        (r"^mandatory, total in bill", "mandatory_total_in_bill"),
        (r"^discretionary, total in bill", "discretionary_total_in_bill"),
        (r"^total, mandatory, current year", "mandatory_current_year"),
        (r"^subtotal, discretionary, current year", "discretionary_current_year"),
        (r"^total, discretionary", "discretionary_total"),
        (r"^total, scorekeeping", "scorekeeping_adjustments"),
        (r"^\(?mandatory\)?$", "mandatory"),
        (r"^\(?discretionary\)?$", "discretionary"),
        (r"^less advances for subsequent", "less_advances_subsequent"),
        (r"^less advance emergency", "less_advance_emergency"),
        (r"^plus emergency advances", "plus_emergency_advances_prior"),
        (r"^plus advances", "plus_advances_prior"),
        (r"^federal funds", "federal_funds"),
        (r"^trust funds?", "trust_funds"),
        (r"^\(?advance appropriations?,? fy ?\d", "advance_appropriations"),
        (r"^\(?advance appropriations?\)?\s*\(emergency", "advance_appropriations_emergency"),
        (r"^\(?advance", "advance_appropriations"),
        (r"^new advances", "advance_appropriations"),
        (r"^fiscal year \d{4} advance|^fy ?\d{4} advance", "advance_appropriations"),
        (r"^fiscal year \d{4}$", "advance_appropriations"),
        (r"^current year", "current_year"),
        (r"^emergency appropriations?", "emergency_appropriations"),
        (r"^\(?rescission of emergency|^\(rescission.*emergency", "rescissions_emergency"),
        (r"^rescissions?", "rescissions"),
        (r"^appropriations", "appropriations"),
        (r"cures", "cures_act"),
        (r"prevention and public health", "pphf"),
        (r"^total, pandemic", "pandemic_flu_balances"),
        (r"^\(?by transfer", "by_transfer"),
        (r"^\(?transfer out", "transfer_out"),
        (r"census cap", "cap_adjustment"),
        (r"cap adjustment", "cap_adjustment"),
        (r"disaster", "disaster_relief"),
        (r"program integrity", "program_integrity"),
        (r"limitation", "limitation"),
        (r"budget amendment", "budget_amendment"),
    ]
    for pat, key in rules:
        if re.search(pat, l):
            return key
    return "other"


def doc_rows(doc, pg_texts):
    """Every summary row of one document: [(pdf_page, block, label, [(x_end, token)], raw)]."""
    rows = []
    for j, p in enumerate(pg_texts):
        for raw in p.splitlines():
            if raw.strip():
                rows.append((j + 1, raw))
    # the comparative table: rows after the last 'COMPARATIVE STATEMENT' / 'AMOUNTS RECOMMENDED' heading's first page
    out, block, i = [], None, 0
    while i < len(rows):
        pg, raw = rows[i]
        s = raw.strip()
        lab, toks = split_row(raw)
        if START.match(s) and (toks or re.match(r"^RECAP", s, re.I)):
            block = "bill" if re.match(r"^(grand|recap|mandatory|discretionary)", s, re.I) else title_of(s)
            if block is None:
                block = "title ?"
            out.append((pg, block, lab, toks, raw))
        elif block and SKIP.match(s):
            pass
        elif block and LINE.match(s) and not TITLE_HEAD.match(s):
            # a label wrapped onto the next line ('Total, Title II, Department of Health and Human' / 'Services ...')
            if not toks and i + 1 < len(rows):
                lab2, toks2 = split_row(rows[i + 1][1])
                if toks2 and not LINE.match(rows[i + 1][1].strip()):
                    out.append((pg, block, (lab + " " + lab2).strip(), toks2, raw + " / " + rows[i + 1][1]))
                    i += 2
                    continue
            out.append((pg, block, lab, toks, raw))
        elif block and START.match(s):
            # a total row whose numbers wrapped to the next line
            lab2, toks2 = split_row(rows[i + 1][1]) if i + 1 < len(rows) else ("", [])
            block = "bill" if re.match(r"^(grand|recap|mandatory|discretionary)", s, re.I) else title_of(s)
            out.append((pg, block or "title ?", (s + " " + lab2).strip(), toks2,
                        raw + " / " + (rows[i + 1][1] if i + 1 < len(rows) else "")))
            i += 2
            continue
        else:
            if block:
                out.append((pg, block, "[block ends] " + s[:80], [], raw))
            block = None
        i += 1
    return out


ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}


def title_of(s):
    m = re.match(r"^Total,?\s+(?:title|tile)\s+([IVXl1|]+(?:\s?[Il1|])*)\b", s, re.I)
    if not m:
        return None
    r = re.sub(r"\s", "", m.group(1)).upper().replace("L", "I").replace("1", "I").replace("|", "I")
    return f"Title {r}" if r in ROMAN else None


def columns(toks_rows):
    """Column right edges from the rows with the most tokens on a page (clusters of x_end)."""
    xs = sorted(x for toks in toks_rows for x, _ in toks)
    if not xs:
        return []
    groups, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= 6:
            cur.append(x)
        else:
            groups.append(cur)
            cur = [x]
    groups.append(cur)
    return [max(g) for g in groups]


def edges_of(toks_rows):
    """Each column's right edge: the median x_end of the column's token over the rows printing every column."""
    if not toks_rows:
        return []
    n = len(toks_rows[0])
    return [sorted(r[k][0] for r in toks_rows)[len(toks_rows) // 2] for k in range(n)]


def assign(toks, edges):
    cols = [None] * len(edges)
    for x, t in toks:
        k = min(range(len(edges)), key=lambda e: abs(edges[e] - x))
        if t is not None:
            cols[k] = t
    return cols


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=str(ROOT / "document_store"))
    p.add_argument("--doc", action="append")
    p.add_argument("--merge-images", metavar="FOLDER", help="write summary_rows_images.csv from the page-image reads")
    args = p.parse_args()
    if args.merge_images:
        return merge_images(args.merge_images)
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    data = json.loads(STAGED.read_text())
    docs = {s["document_id"]: s for s in data["source_docs"]}
    held = sorted({o["source_document_id"] for o in data["observations"]
                   if docs.get(o["source_document_id"], {}).get("document_type")
                   in ("committee_report", "explanatory_statement")})
    out = []
    for doc in held:
        if args.doc and doc not in args.doc:
            continue
        if doc in IMAGE_ONLY:
            continue
        rows = doc_rows(doc, pages(doc, docs[doc]["url_or_identifier"], cache))
        counts = {}
        for r in rows:
            counts[len(r[3])] = counts.get(len(r[3]), 0) + 1
        ncols = max((k for k in counts if k), key=lambda k: (counts[k], k), default=0)
        by_page = {}
        for r in rows:
            by_page.setdefault(r[0], []).append(r)
        doc_edges = edges_of([r[3] for r in rows if len(r[3]) == ncols])
        for pg, rs in by_page.items():
            edges = edges_of([r[3] for r in rs if len(r[3]) == ncols]) or doc_edges
            for seq, (pg_, block, lab, toks, raw) in enumerate(rs):
                cols = [t for _, t in toks] if len(toks) == ncols else assign(toks, edges) if edges else []
                money = ncols - 2 if ncols >= 3 else ncols
                vals = [amount(c) if c else None for c in cols]
                c = vals[:money] + [None] * (3 - money)
                d = vals[money:money + 2] + [None] * (2 - len(vals[money:money + 2]))
                if money == 1:
                    c = [None, None, vals[0] if vals else None]
                    d = [None, None]
                arith = check(c, d, money)
                out.append({"source_document_id": doc, "pdf_page": pg, "block": block, "seq": seq, "label": lab,
                            "line": norm(lab) if not lab.startswith("[block ends]") else "",
                            "c1": c[0], "c2": c[1], "c3": c[2], "d1": d[0], "d2": d[1], "arithmetic": arith,
                            "read_from": "text layer", "row_text": re.sub(r"\s{2,}", "  ", LEADER.sub(" .. ", raw)).strip()})
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} rows -> {OUT.relative_to(ROOT)}")


def check(c, d, money):
    """The table's own arithmetic: recommendation less each earlier column = its comparison column."""
    if money < 3:
        return "no comparison columns"
    if all(v is None for v in c):
        return "no amounts"
    v = [x or 0 for x in c]
    want = [v[2] - v[0], v[2] - v[1]]
    got = [x or 0 for x in d]
    if want == got:
        return "agrees"
    return f"differs: expected {want[0]:+,} / {want[1]:+,}"


# documents whose table prints two money columns only (prior enacted, bill) and one comparison (bill vs. enacted)
TWO_COLUMN = {"SRC-CRPT-119HRPT272", "SRC-CRPT-119HRPT652"}
# documents whose table prints one money column only (the recommendation or final bill)
ONE_COLUMN = {"SRC-EXPL-LHHS-FY2026-ENACTED", "SRC-CRPT-119SRPT44", "SRC-CRPT-119SRPT55"}


def merge_images(folder):
    """The page-image reads (one CSV per document: source_document_id, pdf_page, block, label, c1..d2, row_text)
    -> summary_rows_images.csv, with each row's line and the table's own arithmetic."""
    out = []
    for f in sorted(Path(folder).glob("SRC-*.csv")):
        for seq, r in enumerate(csv.DictReader(f.open())):
            v = {k: (int(r[k]) if r[k].strip() else None) for k in ("c1", "c2", "c3", "d1", "d2")}
            doc = r["source_document_id"]
            if doc in ONE_COLUMN:
                arith = "no comparison columns"
            elif all(v[k] is None for k in ("c1", "c2", "c3")):
                arith = "no amounts"
            elif doc in TWO_COLUMN:
                want = (v["c3"] or 0) - (v["c1"] or 0)
                arith = "agrees" if want == (v["d1"] or 0) else f"differs: expected {want:+,}"
            else:
                arith = check([v["c1"], v["c2"], v["c3"]], [v["d1"], v["d2"]], 3)
            out.append({"source_document_id": doc, "pdf_page": r["pdf_page"], "block": r["block"], "seq": 1000 + seq,
                        "label": r["label"].strip(), "line": norm(r["label"]), **v, "arithmetic": arith,
                        "read_from": "page image", "row_text": r["row_text"]})
    with IMAGES.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} rows -> {IMAGES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
