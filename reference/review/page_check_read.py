"""
Read the pages behind reference/review/page_check_sheet.csv: for every distinct (document, page) pair
-- each page of a range -- get its text once, at a higher resolution than page_check.py's 300 dpi pass,
and find each figure in its row and column.

    python reference/review/page_check_read.py ocr        # text for every page (cached), once per page
    python reference/review/page_check_read.py match      # match every figure; writes page_check_matches.csv
    python reference/review/page_check_read.py apply      # results -> page_check_results.csv; narrow CJS ranges

Text: the page's own text layer, and tesseract (--psm 6) on the page's table image rendered at 400 dpi in
each orientation (0/90/270), keeping the reading with the most comma-grouped numbers. OCR spacing slips
("455 ,000") are closed before matching. A figure is matched when the line that prints it is the account's
row (its printed label, under the right agency heading on a page with two agencies) and it stands in the
stage's column (comparative statements: prior-year enacted, request, bill/final, then the two changes).
Rows the text can't settle go to a person, who reads the page image.
"""

import csv
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "links"))
SHEET = HERE / "page_check_sheet.csv"
STAGED = ROOT / "data" / "staged.json"
CACHE = Path.home() / ".cache" / "approps_page_read"
OUT = HERE / "page_check_matches.csv"
DPI = 400

# the stored file for each source document (document_store/manifest.json; the CJS FY2026 statement is the
# division A explanatory statement the rules.house.gov link serves)
FILES = {
    "SRC-CRPT-114HRPT605": "document_store/CRPT-114hrpt605.pdf", "SRC-CRPT-115HRPT231": "document_store/CRPT-115hrpt231.pdf",
    "SRC-CRPT-115HRPT704": "document_store/CRPT-115hrpt704.pdf", "SRC-CRPT-116HRPT101": "document_store/CRPT-116hrpt101.pdf",
    "SRC-CRPT-116HRPT455": "document_store/CRPT-116hrpt455.pdf", "SRC-CRPT-117HRPT395": "document_store/CRPT-117hrpt395.pdf",
    "SRC-CRPT-117HRPT403": "document_store/CRPT-117hrpt403.pdf", "SRC-CRPT-118HRPT582": "document_store/CRPT-118hrpt582.pdf",
    "SRC-CRPT-118HRPT585": "document_store/CRPT-118hrpt585.pdf", "SRC-CRPT-119HRPT271": "document_store/CRPT-119hrpt271.pdf",
    "SRC-CRPT-119HRPT272": "document_store/CRPT-119hrpt272.pdf", "SRC-CRPT-119HRPT696": "document_store/CRPT-119hrpt696.pdf",
    "SRC-EXPL-FY2026-PB": "fy26_cjs_jes.pdf",
    "SRC-EXPL-LHHS-FY2026-ENACTED": "document_store/MANUAL-LHHS-FY2026-Enacted-jes-e44f7662.pdf",
}
NUM = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+|[-+]?\d{1,3}(?!\d)|-{2,3}|—")


def pages(sp):
    ps = [int(x) for x in re.findall(r"\d+", str(sp))]
    return list(range(min(ps), max(ps) + 1))


def tidy(text):
    """Close OCR spacing slips inside numbers: '455 ,000' / '1, 250,000' / '-1 ,250' -> one token."""
    t = re.sub(r"(?<=\d)\s*,\s*(?=\d{3}\b)", ",", text)
    t = re.sub(r"(?<=\d) (?=\d{3},\d{3})", ",", t)          # '1 293,225,058' (comma read as space)
    return t


def ocr_page(path, pno):
    key = CACHE / f"{Path(path).stem}-p{pno}-{DPI}.json"
    if key.exists():
        return json.loads(key.read_text())
    doc = pymupdf.open(ROOT / path)
    page = doc[pno - 1]
    layer = page.get_text()
    best, best_rot = "", None
    infos = page.get_image_info()
    with tempfile.TemporaryDirectory() as td:
        for rot in (0, 90, 270):
            page.set_rotation(rot)
            clip = (pymupdf.Rect(max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))["bbox"])
                    * page.rotation_matrix) if infos else None
            png = Path(td) / f"r{rot}.png"
            page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, clip=clip).save(png)
            out = subprocess.run(["tesseract", str(png), "-", "--psm", "6"], capture_output=True, text=True,
                                 timeout=300).stdout
            score = lambda t: len(re.findall(r"\d{1,3}(?:,\d{3})+", tidy(t)))
            if score(out) > score(best):
                best, best_rot = out, rot
    r = {"layer": layer, "ocr": best, "rotation": best_rot}
    CACHE.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(r))
    return r


def page_list():
    with open(SHEET, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows, sorted({(r["source_document_id"], p) for r in rows for p in pages(r["source_page"])})


def cmd_ocr():
    rows, pairs = page_list()
    print(f"{len(rows)} rows, {len(pairs)} distinct pages")
    for doc, p in pairs:
        r = ocr_page(FILES[doc], p)
        grouped = r"\d{1,3}(?:,\d{3})+"
        n, m = len(re.findall(grouped, tidy(r["ocr"]))), len(re.findall(grouped, r["layer"]))
        print(f"{doc} p{p}: rotation {r['rotation']}, {n} numbers by OCR, {m} in the text layer")


if __name__ == "__main__" and sys.argv[1] == "ocr":
    cmd_ocr()


# ---------------------------------------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------------------------------------

# the column each (document's bill year, stage) prints in: comparative statements print the prior year's
# enacted (or estimate), the request, the bill (final bill in an enacted statement), then two changes
BILL_YEAR = {"SRC-CRPT-114HRPT605": 2017, "SRC-CRPT-115HRPT231": 2018, "SRC-CRPT-115HRPT704": 2019,
             "SRC-CRPT-116HRPT101": 2020, "SRC-CRPT-116HRPT455": 2021, "SRC-CRPT-117HRPT395": 2023,
             "SRC-CRPT-117HRPT403": 2023, "SRC-CRPT-118HRPT582": 2025, "SRC-CRPT-118HRPT585": 2025,
             "SRC-CRPT-119HRPT271": 2026, "SRC-CRPT-119HRPT272": 2026, "SRC-CRPT-119HRPT696": 2027,
             "SRC-EXPL-FY2026-PB": 2026, "SRC-EXPL-LHHS-FY2026-ENACTED": 2026}
SINGLE_COLUMN = {"SRC-EXPL-LHHS-FY2026-ENACTED"}          # prints the final bill only


def column(doc, fy, stage):
    if doc in SINGLE_COLUMN:
        return 0
    by = BILL_YEAR[doc]
    if stage == "Enacted" and fy == by - 1:
        return 0
    if stage == "President's Budget" and fy == by:
        return 1
    if stage in ("House Reported", "Enacted") and fy == by:
        return 2
    return None


VAL = re.compile(r"^\(?[-+~—]*[\d§$S]{1,3}(?:[,.][\d§SO]{3})*\)?[.,']*$")
BLANK = re.compile(r"^[^\w\d]*(?:-{1,3}|—|[a-z“”‘’>+~\-]{1,4})[^\w\d]*$", re.I)
STOP = {"and", "of", "the", "for", "on", "in", "a", "to", "&", "with", "title", "ii", "iii", "department"}


def num(tok):
    t = tok.strip("()'.,").replace("§", "5").replace("$", "5").replace("S", "5").replace("O", "0").replace("~", "-").replace("—", "-")
    t = re.sub(r"(?<=\d)\.(?=\d{3}\b)", ",", t)
    t = t.replace("+", "")
    if not re.fullmatch(r"-*\d{1,3}(?:,\d{3})*", t):
        return None
    neg = t.startswith("-")
    v = int(t.lstrip("-").replace(",", ""))
    return -v if neg else v


def parse_line_left(line):
    """The columns read from the left: from the first comma-grouped number or dash run on."""
    toks = line.split()
    first = next((i for i, t in enumerate(toks) if i > 0 and (VAL.match(t) and re.search(r"[\d§]{1,3}[,.][\d§O]{3}", t)
                  or re.fullmatch(r"-{2,3}|—", t))), None)
    while first is not None and first > 1 and re.fullmatch(r"[§$S]|\d{1,3}", toks[first - 1]) and not toks[first - 2].endswith("."):
        first -= 1
    if first is None:
        return line, []
    cols = []
    for t in toks[first:]:
        n = num(t)
        cols.append(n if n is not None else (None if BLANK.match(t) else t))
    return " ".join(toks[:first]), cols


def parse_line(line):
    """-> (label, [column tokens]) ; a column token is an int, None (blank) or a str (an unreadable run).
    The columns are read from the right: the trailing run of numbers and blank marks ('---' and the short
    OCR noise a dash run turns into), stopping at the leader dots or a word of the label."""
    toks = line.split()
    cols, i = [], len(toks) - 1
    while i > 0:
        t = toks[i]
        n = num(t)
        if n is not None and not t.endswith(".."):
            cols.append(n)
        elif re.fullmatch(r"\(?[-+]?\d{1,3}(?:,\d{3}){4,}\)?[.,]*", t):
            cols.append(t)                             # a joined run of numbers
        elif re.search(r"\d", t) and len(t) > 3 and not t.endswith(".."):
            cols.append(t)                             # a number OCR garbled ('+24,9899,203')
        elif BLANK.match(t) and not t.endswith("..") and len(t) <= 4:
            cols.append(None)
        else:
            break
        i -= 1
    cols.reverse()
    # leading blanks belong to the label's leader noise unless a number follows them
    if not any(isinstance(c, (int, str)) for c in cols):
        return line, []
    return " ".join(toks[:i + 1]), cols


def words(s):
    return [w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOP and len(w) > 1]


def label_score(expected, printed):
    import difflib
    ew, pw = words(expected), words(printed)
    if not ew:
        return 0.0
    hit = sum(1 for w in ew if any(difflib.SequenceMatcher(None, w, p).ratio() >= 0.8 for p in pw))
    return hit / len(ew)


def expected_label(o):
    s = o["source_table_or_section"] or ""
    m = re.search(r"printed as '([^']+)'", s)
    if m:
        return m.group(1)
    last = s.split(",")[-1] if s.startswith("Title III") else s
    parts = [p.strip() for p in s.split(",")]
    last = ", ".join(parts[2:]) if s.startswith("Title") and len(parts) > 2 else parts[-1]
    last = last.split(" -- ")[0]
    return re.sub(r"\((?:base|emergency)\)", "", last).strip()


AGENCIES = {"NASA": ("national aeronautics", "aeronautics and space administration"),
            "NSF": ("national science foundation",), "Executive Office of the President": ("executive office", "science and technology policy")}


def rows_of(text):
    """The page's lines as (label, cols, agency heading above it), a label carried over from number-less lines."""
    out, carry, agency = [], "", None
    for raw in tidy(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        label, cols = parse_line(line)
        if not cols and not parse_line_left(line)[1]:
            for a, keys in AGENCIES.items():
                if any(k in low for k in keys) and len(line) < 70:
                    agency = a
            carry = (carry + " " + line).strip() if len(carry) < 200 else line
            continue
        _, left = parse_line_left(line)
        out.append((((carry + " ") if carry else "") + label, (cols, left), agency, line))
        carry = ""
    return out


def split_joined(tok):
    """'935,000,588,700,935,000' -> every way to read it as consecutive numbers."""
    groups = tok.strip("()'.,").split(",")
    res = []

    def rec(i, acc):
        if i == len(groups):
            res.append(acc)
            return
        for j in range(i + 1, min(len(groups), i + 4) + 1):
            if j > i + 1 or True:
                piece = groups[i:j]
                if all(len(g) == 3 for g in piece[1:]) and 1 <= len(piece[0]) <= 3 and all(g.isdigit() for g in piece) \
                        and not (piece[0].startswith("0") and piece[0] != "0"):
                    rec(j, acc + [int("".join(piece))])
    rec(0, [])
    return [r for r in res if len(r) > 1]


def candidates_cols(cols, k=5):
    """Column readings, expanding a joined run of numbers into its possible splits; the last k slots are
    the table's columns (anything left of them is leader noise)."""
    joined = any(isinstance(c, str) and re.fullmatch(r"\(?[-+]?\d{1,3}(?:,\d{3}){4,}\)?[.,]*", c.strip()) or
                 isinstance(c, int) and abs(c) >= 10 ** 13 for c in cols)
    return [(c[-k:], joined) for c in _expand(cols) if len(c) >= k]


def _expand(cols):
    out = [[]]
    for c in cols:
        if isinstance(c, str) and re.fullmatch(r"\(?\d{1,3}(?:,\d{3}){4,}\)?[.,]*", c.strip()):
            out = [o + s for o in out for s in split_joined(c)]
        elif isinstance(c, int) and abs(c) >= 10 ** 13:
            out = [o + s for o in out for s in split_joined(f"{c:,}")] or [o + [c] for o in out]
        else:
            out = [o + [c] for o in out]
    return out


def arithmetic_ok(cols, ci, fig, doc):
    """With the figure in column ci, do the row's printed changes agree (bill - prior, bill - request)?"""
    if doc in SINGLE_COLUMN or len(cols) < 5:
        return False
    c = list(cols[:5])
    c[ci] = fig
    v = [x if isinstance(x, int) else (0 if x is None else None) for x in c]
    checks = []
    if v[2] is not None and v[0] is not None and v[3] is not None:
        checks.append(v[2] - v[0] == v[3])
    if v[2] is not None and v[1] is not None and v[4] is not None:
        checks.append(v[2] - v[1] == v[4])
    # the figure's own column must take part in an identity that holds
    involved = {0: [0], 1: [1], 2: [0, 1]}[ci]
    return any(checks[k] for k in involved if k < len(checks)) and all(checks)


def near(a, b):
    """OCR slip: the digits differ in at most one place (same length)."""
    sa, sb = str(abs(a)), str(abs(b))
    return len(sa) == len(sb) and sum(x != y for x, y in zip(sa, sb)) <= 1


def match_row(r, o, texts):
    """-> list of per-page findings: (page, kind, detail). kind: exact | arithmetic | wrong_column | none"""
    fig = round(o["amount"] / 1000) if o["amount"] % 1000 == 0 else o["amount"] / 1000
    ci = column(r["source_document_id"], o["fiscal_year"], o["stage"])
    want = expected_label(o)
    agency = None
    s = o["source_table_or_section"] or ""
    for a in AGENCIES:
        if f", {a}," in s:
            agency = a
    found = []
    for p, text in texts:
        best = None
        for label, cols, ag, line in rows_of(text):
            sc = label_score(want, label)
            if sc < 0.6 or (agency and ag and ag != agency):
                continue
            k = 1 if r["source_document_id"] in SINGLE_COLUMN else 5
            right, left = cols
            readings = candidates_cols(right, k) + [(c, False) for c in _expand(left)]
            for cc, joined in readings:
                ints = [x for x in cc if isinstance(x, int)]
                if ci is not None and ci < len(cc) and cc[ci] == fig and (not joined or arithmetic_ok(cc, ci, fig, r["source_document_id"])):
                    kind = "exact"
                elif ci is not None and ci < len(cc) and isinstance(cc[ci], int) and (near(cc[ci], fig) or str(abs(fig)).endswith(str(abs(cc[ci])))) \
                        and arithmetic_ok(cc, ci, fig, r["source_document_id"]):
                    kind = "arithmetic"
                elif ci is not None and ci < len(cc) and not isinstance(cc[ci], int) and arithmetic_ok(cc, ci, fig, r["source_document_id"]):
                    kind = "arithmetic"
                elif fig in ints:
                    kind = "wrong_column"
                else:
                    continue
                rank = {"exact": 0, "arithmetic": 1, "wrong_column": 2}[kind]
                if best is None or (rank, -sc) < (best[0], -best[1]):
                    best = (rank, sc, kind, line)
        if best:
            found.append((p, best[2], best[3]))
    return found


def cmd_match():
    rows, _ = page_list()
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    out = []
    for r in rows:
        o = obs[r["observation_id"]]
        texts = []
        for p in pages(r["source_page"]):
            t = ocr_page(FILES[r["source_document_id"]], p)
            texts.append((p, t["ocr"] + "\n" + t["layer"]))
        f = match_row(r, o, texts)
        good = [x for x in f if x[1] in ("exact", "arithmetic")]
        if len(good) == 1:
            status = "confirmed" if good[0][1] == "exact" else "confirmed_arithmetic"
        elif len(good) > 1:
            status = "multiple_pages"
        elif f:
            status = "wrong_column"
        else:
            status = "not_found"
        out.append({"observation_id": r["observation_id"], "source_document_id": r["source_document_id"],
                    "source_page": r["source_page"], "account": r["account"], "fiscal_year": r["fiscal_year"],
                    "stage": r["stage"], "figure_thousands": r["figure_thousands"], "status": status,
                    "pages": ";".join(str(x[0]) for x in good) or ";".join(str(x[0]) for x in f),
                    "line": " || ".join(x[2][:160] for x in (good or f))})
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    import collections
    print(collections.Counter(x["status"] for x in out))


if __name__ == "__main__" and sys.argv[1] == "match":
    cmd_match()


# ---------------------------------------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------------------------------------

READS = HERE / "page_check_image_reads.jsonl"       # the rows a person settled by reading the page
RESULTS = HERE / "page_check_results.csv"
REVIEWER = "page image check 2026-10-08"
# page checks the owner settled: observation_id -> (page now cited, reviewer, resolution, note)
OWNER = {"OBS-0771": ("227", "owner 2026-10-08", "printed in S.Rept. 118-198 p.227",
                      "re-sourced to SRC-CRPT-118SRPT198 p.227 (Research and related activities (emergency), Budget "
                      "estimate column); H.Rept. 118-582 pp.247-248 print no line for it")}


def results():
    """observation_id -> result: the text match, or the page read for the rows the text did not settle."""
    with open(OUT, newline="") as f:
        matches = {r["observation_id"]: r for r in csv.DictReader(f)}
    reads = {}
    for line in READS.read_text().splitlines():
        r = json.loads(line)
        reads[r["obs"]] = r
    out = {}
    for oid, m in matches.items():
        if oid in reads:
            r = reads[oid]
            ok = r["status"].startswith("confirmed")
            out[oid] = {"status": "confirmed" if ok else "needs_owner", "page": str(r["page"]) if ok else "",
                        "method": r["method"], "note": r["note"]}
        elif m["status"] in ("confirmed", "confirmed_arithmetic"):
            out[oid] = {"status": "confirmed", "page": m["pages"],
                        "method": "page text" if m["status"] == "confirmed" else "page text, row arithmetic",
                        "note": ("OCR reads " if m["status"] == "confirmed_arithmetic" else "") + m["line"][:200]}
        else:
            out[oid] = {"status": "needs_owner", "page": m["pages"], "method": "page text", "note": m["line"][:200]}
    return out


def cmd_apply():
    with open(SHEET, newline="") as f:
        sheet = {r["observation_id"]: r for r in csv.DictReader(f)}
    res = results()
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    rows = []
    narrowed = 0
    for oid, r in sorted(res.items()):
        s, o = sheet[oid], obs[oid]
        cited = s.get("cited_page") or s["source_page"]       # the page cited before any narrowing
        rng = len(pages(cited)) > 1
        resolved = r["status"] == "confirmed"
        if oid in OWNER:
            page, who, resolution, note = OWNER[oid]
            rows.append({"observation_id": oid, "source_document_id": s["source_document_id"], "cited_page": cited,
                         "result": "resolved_by_owner", "page_seen": page, "method": "owner",
                         "human_review_status": "resolved", "reviewer": who, "resolution": resolution, "note": note})
            continue
        if resolved and rng:
            how = "page image" if r["method"] == "page image" else "page text"
            note = f"narrowed from {cited} by {how}"
            if o["source_page"] == cited:                     # once
                o["source_page"] = r["page"]
                o["source_table_or_section"] = f"{o['source_table_or_section']} -- {note}"
                narrowed += 1
        rows.append({"observation_id": oid, "source_document_id": s["source_document_id"], "cited_page": cited,
                     "result": ("narrowed" if rng else "confirmed") if resolved else "needs_owner",
                     "page_seen": r["page"] if resolved else "", "method": r["method"],
                     "human_review_status": "resolved" if resolved else "pending",
                     "reviewer": REVIEWER if resolved else "",
                     "resolution": f"figure seen on PDF p.{r['page']}" if resolved else "",
                     "note": r["note"]})
    with open(RESULTS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    import collections
    print(collections.Counter(r["result"] for r in rows), f"{narrowed} source_page values narrowed")


if __name__ == "__main__" and sys.argv[1] == "apply":
    cmd_apply()
