"""
ocr_tables.py

Text path for comparative tables in scanned documents that carry an OCR text
layer (e.g. the FY2026 CJS JES: every page an image under invisible Adobe
Paper Capture text). Free, like text_tables.py -- no API call -- but nothing
here can rely on drawn lines, because a scan has none:

  - Detection: a page is a table page when its OCR words contain the
    column-header row ("FY 2025 ... Enacted", "Request", "Final Bill", ...).
    The table's title varies by document type (a JES never says
    "COMPARATIVE STATEMENT"), the column headers don't.
  - Columns: from where the header words sit; each number goes to the column
    whose zone its centre falls in. A page whose header parses to a different
    number of columns than the table's is reported, not guessed at.
  - Numbers: OCR splits them ("7 , 334 , 200") and misreads the thousands
    comma as a period ("24 . 160"); a thousands table has no decimals, so a
    '.' between digit groups is a comma. Anything that still isn't a
    well-formed amount is reported as unparsed.
  - Title headings: OCR splits and mis-cases them ("TI TLE Il l - SCIENCE");
    only the TITLE keyword and its numeral are repaired.

It returns the same transcription shape as the vision and text paths, plus
each page's issues. extract_approps gates each page on those issues and on
the table's own arithmetic, and re-reads a failing page with a vision call --
vision only where the free path is shown to fail.
"""

import re
from collections import Counter

import text_tables as tt

NUM_TOKEN = re.compile(r"^[\(\)\+\-−\d,\.·•]+$")
RULE_ROW = re.compile(r"^[\s\-=_·•.,:]+$")
TITLE_OCR_RE = re.compile(r"^\s*T\s*I\s*T\s*L\s*E\s+([IVXLCl1|\s]+?)\s*[-—–:]\s*(.+)$")
UNITS_RE = re.compile(r"\(\s*amounts?\s+in\s+[a-z ]+\)|\[\s*in\s+[a-z ]+\]", re.I)


class OcrTableError(RuntimeError):
    pass


def has_ocr_layer(page):
    """A scanned page: its text is drawn invisibly (render mode 3) over an image."""
    if not page.get_images():
        return False
    trace = page.get_texttrace()
    return bool(trace) and sum(1 for t in trace if t.get("type") == 3) >= 0.8 * len(trace)


def words_printed(page):
    geo = tt.page_geometry(page)
    if geo is None:
        return []
    tf = tt._transform(geo["direction"])
    out = []
    for w in page.get_text("words"):
        u0, v0, u1, v1 = tt._box(tf, w[:4])
        out.append({"t": w[4], "u0": u0, "u1": u1, "v0": v0, "v1": v1, "vc": (v0 + v1) / 2})
    return out


def cluster_rows(words):
    if not words:
        return []
    words = sorted(words, key=lambda w: w["vc"])
    h = sorted(w["v1"] - w["v0"] for w in words)[len(words) // 2]
    rows = []
    for w in words:
        if rows and abs(w["vc"] - rows[-1]["vc"]) <= h * 0.55:
            rows[-1]["w"].append(w)
            rows[-1]["vc"] = sum(x["vc"] for x in rows[-1]["w"]) / len(rows[-1]["w"])
        else:
            rows.append({"vc": w["vc"], "w": [w]})
    for r in rows:
        r["w"].sort(key=lambda w: w["u0"])
        r["text"] = " ".join(w["t"] for w in r["w"])
    return rows, h


def find_header(rows):
    """The two-line column header: 'FY 2025  FY 2026  Final Bill ...' over
    'Enacted  Request  Final Bill  vs Enacted ...'."""
    for i, r in enumerate(rows[:-1]):
        top, bottom = r["text"].lower(), rows[i + 1]["text"].lower()
        if re.search(r"\bfy\b", top) and "enacted" in bottom and ("request" in bottom or "bil" in bottom):
            return i
    return None


def header_columns(r1, r2, em):
    """Header words within 1.5 line-heights of each other belong to one column
    header ("Final" + "Bill"); columns themselves sit ~3 line-heights apart."""
    ws = [w for w in r1["w"] + r2["w"] if re.search(r"[A-Za-z0-9]", w["t"])]
    groups = []
    for w in sorted(ws, key=lambda w: w["u0"]):
        if groups and w["u0"] <= groups[-1]["u1"] + 1.5 * em:
            groups[-1]["w"].append(w)
            groups[-1]["u1"] = max(groups[-1]["u1"], w["u1"])
        else:
            groups.append({"u0": w["u0"], "u1": w["u1"], "w": [w]})
    cols = []
    for g in groups:
        top = [x["t"] for x in sorted(g["w"], key=lambda x: x["u0"]) if x in r1["w"]]
        bot = [x["t"] for x in sorted(g["w"], key=lambda x: x["u0"]) if x in r2["w"]]
        cols.append({"u0": g["u0"], "u1": g["u1"], "header": repair_header(" ".join(top + bot))})
    return cols


BILL_OCR_RE = re.compile(r"\bB\s*i\s*[l1I|]\s*[l1I|]\b")


def repair_header(h):
    """Header words OCR mangles the same way every time: l / 1 / I in "Bill"."""
    return re.sub(r"\s+", " ", BILL_OCR_RE.sub("Bill", h)).strip()


def header_key(h):
    """OCR-tolerant comparison key: letters and digits only, with the usual
    l/1/I confusion folded ("Final Bil 1" == "Final Bill")."""
    return re.sub(r"[^a-z0-9]", "", h.lower().replace("1", "l").replace("|", "l"))


def repair_title(label):
    m = TITLE_OCR_RE.match(label)
    if not m:
        return label
    numeral = re.sub(r"\s+", "", m.group(1)).upper().replace("L", "I").replace("1", "I").replace("|", "I")
    if not re.fullmatch(r"[IVXLC]+", numeral):
        return label
    name = re.sub(r"\s+", " ", m.group(2)).strip()
    return f"TITLE {numeral} - {name}"


def norm_amount(tokens):
    s = "".join(tokens).replace("−", "-").replace(" ", "")
    s = re.sub(r"[-=_]{2,}", "", s)          # a printed rule that skew put on this line; a minus is one character
    s = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", ",", s)
    s = re.sub(r"^[\.,]+|[\.,]+$", "", s)
    return s, bool(re.fullmatch(r"\(?[+-]?\d{1,3}(,\d{3})*\)?", s))


def page_rows(page, cols_hint=None):
    """-> (columns, header_found, table_title, units, rows, issues)."""
    got = cluster_rows(words_printed(page))
    if not got:
        return cols_hint, False, "", "", [], []
    rows, em = got
    hi = find_header(rows)
    cols, title, units, start = cols_hint, "", "", 0
    if hi is not None:
        cols = header_columns(rows[hi], rows[hi + 1], em)
        above = [r["text"] for r in rows[:hi]]
        units = next((UNITS_RE.search(t).group(0) for t in above if UNITS_RE.search(t)), "")
        title = " ".join(t for t in above if not UNITS_RE.search(t)).strip()
        start = hi + 2
    if not cols:
        return None, False, "", "", [], ["no column header on this page or the one before it"]
    centers = [(c["u0"] + c["u1"]) / 2 for c in cols]
    first_w = cols[0]["u1"] - cols[0]["u0"]
    bounds = [centers[0] - max(first_w, centers[1] - centers[0] if len(cols) > 1 else first_w) / 2 - 20] + \
             [(centers[i] + centers[i + 1]) / 2 for i in range(len(cols) - 1)] + [float("inf")]
    out, issues = [], []
    for r in rows[start:]:
        if RULE_ROW.match(r["text"]):
            continue                                  # a rule printed as dashes
        label_w, cells = [], [[] for _ in cols]
        for w in r["w"]:
            ctr = (w["u0"] + w["u1"]) / 2
            # separators and signs often come out as tokens of their own
            # ("- 179 , 500"), so a token counts if it's only number characters
            numeric = NUM_TOKEN.match(w["t"])
            if ctr < bounds[0] or not numeric:
                if ctr < bounds[0] or re.search(r"[A-Za-z]", w["t"]):
                    label_w.append(w)
                continue
            i = next(i for i in range(len(cols)) if bounds[i] <= ctr < bounds[i + 1])
            cells[i].append(w["t"])
        label = " ".join(w["t"] for w in label_w)
        label = re.sub(r"(\s*[\.·,:]\s*){2,}.*$", "", label)       # dot leaders and what OCR made of them
        label = re.sub(r"[\s\.,·:]+$", "", label).strip()
        label = repair_title(label)
        values, states = [], []
        for c in cells:
            if not c:
                values.append("")
                states.append("absent")
                continue
            s, ok = norm_amount(c)
            if not s:                                 # only rule ink in this cell
                values.append("")
                states.append("absent")
                continue
            values.append(s)
            states.append("number" if ok else "unparsed")
            if not ok:
                issues.append(f"unparsed cell {''.join(c)!r} in {label[:40]!r}")
        if not label and all(s == "absent" for s in states):
            continue
        out.append({"label": label, "u_start": label_w[0]["u0"] if label_w else None, "em": em,
                    "values": values, "cell_states": states,
                    "raw_text": " ".join([label] + [v for v in values if v]),
                    "text_as_extracted": r["text"]})
    return cols, hi is not None, title, units, out, issues


def merge_wraps(rows):
    """A label-only line followed by a line carrying the numbers that starts
    lower-case, with "(", or further right, is one wrapped label."""
    out = []
    for r in rows:
        prev = out[-1] if out else None
        if (prev is not None and all(s == "absent" for s in prev["cell_states"]) and prev["label"]
                and not prev["label"].isupper() and not prev["label"].startswith("TITLE ")
                and any(s != "absent" for s in r["cell_states"]) and r["label"]
                and (r["label"][0].islower() or r["label"][0] == "("
                     or (r["u_start"] or 0) > (prev["u_start"] or 0) + 4)):
            prev.update(r, label=f"{prev['label']} {r['label']}", u_start=prev["u_start"],
                        raw_text=f"{prev['label']} {r['raw_text']}",
                        text_as_extracted=f"{prev['text_as_extracted']} / {r['text_as_extracted']}")
            continue
        out.append(r)
    return out


def find_table_pages(doc_pdf, ocr_pages):
    """OCR pages whose words carry the column-header row."""
    found = []
    for p in sorted(ocr_pages):
        got = cluster_rows(words_printed(doc_pdf[p - 1]))
        if got and find_header(got[0]) is not None:
            found.append(p)
    return found


def extract_table(doc_pdf, pages):
    """-> ({page: transcription}, {page: [issues]}). The table's columns are
    the most common header across its pages; a page whose header has a
    different column count, or doesn't match, is an issue for that page."""
    parsed, cols_hint = {}, None
    for p in pages:
        cols, has_header, title, units, rows, issues = page_rows(doc_pdf[p - 1], cols_hint)
        parsed[p] = (cols, has_header, title, units, merge_wraps(rows), issues)
        cols_hint = cols if has_header else cols_hint
    header_sets = Counter(tuple(header_key(c["header"]) for c in v[0]) for v in parsed.values() if v[0])
    if not header_sets:
        raise OcrTableError("no page with a readable column header")
    canon_keys = header_sets.most_common(1)[0][0]
    canon = next([c["header"] for c in v[0]] for v in parsed.values()
                 if v[0] and tuple(header_key(c["header"]) for c in v[0]) == canon_keys)
    table_title = next((v[2] for v in parsed.values() if v[2]), "")
    table_units = Counter(v[3] for v in parsed.values() if v[3]).most_common(1)
    table_units = table_units[0][0] if table_units else ""
    left = min((r["u_start"] for v in parsed.values() for r in v[4] if r["u_start"] is not None), default=0.0)
    trs, page_issues = {}, {}
    for p, (cols, has_header, title, units, rows, issues) in parsed.items():
        issues = list(issues)
        keys = tuple(header_key(c["header"]) for c in cols) if cols else ()
        if len(keys) != len(canon_keys):
            issues.append(f"header parsed to {len(keys)} columns, table has {len(canon_keys)}")
        elif keys != canon_keys:
            issues.append(f"header {[c['header'] for c in cols]} doesn't match the table's {canon}")
        for r in rows:
            r["indent"] = 0 if r["u_start"] is None else round((r["u_start"] - left) / (r["em"] or 7.0))
            r["is_heading"] = all(s == "absent" for s in r["cell_states"])
            r["rule_above"] = "none"
        trs[p] = {"orientation_ok": True, "table_title": table_title, "units_declared": units or table_units,
                  "column_headers": canon, "rows": rows, "legibility_notes": "; ".join(issues)}
        page_issues[p] = issues
    return trs, page_issues
