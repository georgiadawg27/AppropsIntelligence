"""
text_tables.py

Text path for comparative statements that GPO typeset as real text rather
than as image-only fold-out inserts -- confirmed for Senate committee reports
(S.Rept. 119-44, S.Rept. 118-62). See the scoping doc's "Text-native
comparative tables (Senate reports)" section.

Produces the same per-page transcription dict the vision path gets back from
Claude (table_title, units_declared, column_headers, rows[label, indent,
is_heading, rule_above, values, raw_text]), so everything downstream --
hierarchy, observations, validation -- is shared. No API call is made.

Everything is read from the page's own geometry:

  - The table is printed sideways. Each text line's writing direction gives
    the transform into "printed" coordinates: u runs left-to-right along a
    printed row, v runs top-to-bottom down the table.
  - Column boundaries are the drawn separator lines (constant u). Column
    *meaning* is read from each table's own header box every time -- the
    count varies by year (FY2026: 3 value columns, FY2024: 5).
  - Only the left page of a two-page spread carries the header box. A page
    without one inherits column meaning from the page immediately before it,
    and only if its separators line up exactly with that page's.
  - Sign glyphs: GPO's font encoding extracts "+" as "∂" and "-" as "¥".
    They're decoded here, and the decoding is then *checked* against every
    row whose delta column can be recomputed from its value columns
    (sign_glyph_check), rather than assumed to hold for every document.
  - Dot leaders: a run of dots after a label is spacing; a cell whose only
    content is a run of dots is a printed blank ("leader blank") -- not zero
    (that's "---") and not missing (no cell at all).

Not handled: a line-item label that wraps onto a second printed line. Neither
Senate table checked so far has one (their only two-line labels are centered
headings, e.g. an act name over its public law number, which really are two
headings). A wrap would surface as a heading with no leader immediately
followed by an indented row.
"""

import re
from collections import defaultdict

TABLE_TITLE_RE = re.compile(r"COMPARATIVE\s+STATEMENT\s+OF\s+NEW\s+BUDGET", re.I)
UNITS_RE = re.compile(r"\[\s*In\s+[a-z ]+\]|\(\s*(?:Amounts\s+)?in\s+[a-z ]+\)", re.I)
SIGN_GLYPHS = {"∂": "+", "¥": "-"}          # ∂ -> +, ¥ -> -
DOTS_RE = re.compile(r"^\.{2,}$")
LEADER_BLANK = "...."                                 # how a printed blank cell is passed on
LABEL_LEADER_RE = re.compile(r"\s+\.[.\s]*$")
NUMERIC_CELL_RE = re.compile(r"^\(?[+\-−]?\s*[\d,]+\s*\)?$")

ROW_TOL = 1.0        # lines whose printed baselines are this close share a row
COL_TOL = 1.0        # slack when testing which column a line sits in
DOUBLE_RULE_GAP = 3.0


class TextTableError(RuntimeError):
    pass


def decode_signs(s):
    for glyph, sign in SIGN_GLYPHS.items():
        s = s.replace(glyph, sign)
    return s


# ---------------------------------------------------------------------------
# Printed-coordinate transform
# ---------------------------------------------------------------------------

def _transform(direction):
    """(x, y) -> (u, v) for text written in this direction."""
    dx, dy = round(direction[0]), round(direction[1])
    if (dx, dy) == (1, 0):
        return lambda x, y: (x, y)
    if (dx, dy) == (0, -1):          # reads bottom-to-top (GPO fold-outs)
        return lambda x, y: (-y, x)
    if (dx, dy) == (0, 1):           # reads top-to-bottom
        return lambda x, y: (y, -x)
    if (dx, dy) == (-1, 0):
        return lambda x, y: (-x, -y)
    raise TextTableError(f"unsupported text direction {direction}")


def _box(tf, bbox):
    x0, y0, x1, y1 = bbox
    (ua, va), (ub, vb) = tf(x0, y0), tf(x1, y1)
    return min(ua, ub), min(va, vb), max(ua, ub), max(va, vb)


def page_geometry(page):
    """Text lines and drawn rules of one page, in printed coordinates."""
    raw_lines = []
    dirs = defaultdict(int)
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            text = "".join(s["text"] for s in l["spans"])
            if not text.strip():
                continue
            raw_lines.append((l, text))
            dirs[(round(l["dir"][0]), round(l["dir"][1]))] += len(text)
    if not dirs:
        return None
    direction = max(dirs, key=dirs.get)          # the table's direction, not the folio's
    tf = _transform(direction)
    lines = []
    for l, text in raw_lines:
        if (round(l["dir"][0]), round(l["dir"][1])) != direction:
            continue                              # page number / slug printed upright
        u0, v0, u1, v1 = _box(tf, l["bbox"])
        lines.append({"text": text, "u0": u0, "u1": u1, "v0": v0, "v1": v1,
                      "size": l["spans"][0]["size"],
                      "fonts": sorted({s["font"] for s in l["spans"]})})
    seps, rules = [], []
    for dr in page.get_drawings():
        for it in dr["items"]:
            if it[0] != "l":
                continue
            (ua, va), (ub, vb) = tf(it[1].x, it[1].y), tf(it[2].x, it[2].y)
            if abs(ua - ub) < 0.2:                # vertical in printed view: column separator
                seps.append((round(ua, 1), min(va, vb), max(va, vb)))
            elif abs(va - vb) < 0.2:              # horizontal in printed view: a rule
                rules.append((round(va, 1), min(ua, ub), max(ua, ub)))
    return {"direction": direction, "lines": lines, "seps": seps, "rules": rules}


# ---------------------------------------------------------------------------
# Finding the table
# ---------------------------------------------------------------------------

def separator_positions(geo):
    return tuple(sorted({u for u, _, _ in geo["seps"]}))


def header_box(geo, label_edge):
    """The two full-width rules (label column through the last value column)
    that bound the header block, or None on a continuation page."""
    full = sorted({v for v, u0, u1 in geo["rules"] if u0 <= label_edge + COL_TOL})
    return (full[0], full[1]) if len(full) >= 2 else None


def find_table_pages(doc_pdf, text_pages):
    """
    Text-routed pages that belong to a comparative statement: a page whose
    text carries the table title AND whose layout has column separators
    starts (or continues) the table; a following page with the same
    separator geometry and no title is its right-hand continuation. A title
    match without separators (the table of contents) is not a table page.
    """
    found, prev = [], None
    for p in sorted(text_pages):
        has_title = bool(TABLE_TITLE_RE.search(doc_pdf[p - 1].get_text()))
        if not has_title and not (prev and prev[0] == p - 1):
            prev = None
            continue                              # geometry only where a table could be
        geo = page_geometry(doc_pdf[p - 1])
        seps = separator_positions(geo) if geo else ()
        if seps and has_title:
            found.append(p)
            prev = (p, seps)
        elif seps and prev and prev[0] == p - 1 and seps == prev[1]:
            found.append(p)
            prev = (p, seps)
        else:
            prev = None
    return found


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def _columns(seps):
    """Value columns as [u_left, u_right) between consecutive separators; the
    label column is everything left of the first separator."""
    bounds = list(seps) + [float("inf")]
    return [(bounds[i], bounds[i + 1]) for i in range(len(seps))]


def _overlapping(cols, u0, u1):
    return [i for i, (a, b) in enumerate(cols) if u0 < b - COL_TOL and u1 > a + COL_TOL]


def read_header(geo):
    """
    Column headers from this page's own header box. A header line that spans
    several value columns (FY2024's "Senate Committee recommendation compared
    with (+ or -)") is prefixed to each column underneath it.
    """
    seps = separator_positions(geo)
    label_edge = min(l["u0"] for l in geo["lines"])
    box = header_box(geo, label_edge)
    if box is None:
        return None
    top, bottom = box
    cols = _columns(seps)
    parts = [[] for _ in cols]
    title_lines, units = [], ""
    for l in sorted(geo["lines"], key=lambda l: (l["v0"], l["u0"])):
        if l["v1"] <= top + COL_TOL:
            text = re.sub(r"\s+", " ", l["text"]).strip()
            if UNITS_RE.fullmatch(text):
                units = text
            else:
                title_lines.append(text)
            continue
        if l["v0"] >= bottom - COL_TOL or l["u1"] <= seps[0] + COL_TOL:
            continue                              # body, or the label column's "Item"
        for i in _overlapping(cols, l["u0"], l["u1"]):
            parts[i].append((l["v0"], l["text"].strip()))
    headers = []
    for i, ps in enumerate(parts):
        if not ps:
            raise TextTableError(f"value column {i + 1} has no header text")
        headers.append(decode_signs(re.sub(r"\s+", " ", " ".join(t for _, t in sorted(ps)))).strip())
    return {"table_title": " ".join(title_lines), "units_declared": units,
            "column_headers": headers, "seps": seps, "body_top": bottom}


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def _clean_value(text):
    """-> (value string for parse_cell, cell state). States: number | leader_blank | other."""
    raw = text.strip()
    if DOTS_RE.fullmatch(raw.replace(" ", "")):
        return LEADER_BLANK, "leader_blank"
    s = decode_signs(raw)
    s = re.sub(r"^\.{2,}\s*", "", s)             # dots running up to a number are spacing
    s = re.sub(r"\s+", "", s)                     # "(36,225,943 )" -> "(36,225,943)"
    return s, ("number" if NUMERIC_CELL_RE.fullmatch(s) else "other")


def read_rows(geo, seps, body_top):
    cols = _columns(seps)
    body = [l for l in geo["lines"] if l["v0"] >= body_top - COL_TOL]
    body.sort(key=lambda l: l["v1"])
    strips = []
    for l in body:
        if strips and abs(l["v1"] - strips[-1][-1]["v1"]) <= ROW_TOL:
            strips[-1].append(l)
        else:
            strips.append([l])

    # Rules across the value columns only (full-width rules are the header box).
    value_rules = sorted(v for v, u0, u1 in geo["rules"] if u0 >= seps[0] - 2 * COL_TOL)

    rows, prev_bottom = [], body_top
    for strip in strips:
        top = min(l["v0"] for l in strip)
        label_parts, cells, leader = [], [None] * len(cols), False
        for l in sorted(strip, key=lambda l: l["u0"]):
            if l["u1"] <= seps[0] + COL_TOL:
                label_parts.append(l)
                continue
            hit = _overlapping(cols, l["u0"], l["u1"])
            if len(hit) != 1:
                raise TextTableError(f"text {l['text']!r} straddles value columns {hit}")
            i = hit[0]
            if cells[i] is not None:
                raise TextTableError(f"two entries in value column {i + 1}: {cells[i][0]!r}, {l['text']!r}")
            cells[i] = (l["text"], l["fonts"])
        label = " ".join(l["text"].strip() for l in label_parts)
        if LABEL_LEADER_RE.search(label):
            leader = True
            label = LABEL_LEADER_RE.sub("", label)
        label = decode_signs(label).strip()

        values, states = [], []
        for c in cells:
            if c is None:
                values.append("")
                states.append("absent")
                continue
            v, st = _clean_value(c[0])
            values.append(v)
            states.append(st)

        rules = [v for v in value_rules if prev_bottom - COL_TOL <= v <= top + COL_TOL]
        if not rules:
            rule_above = "none"
        elif len(rules) == 1 or max(rules) - min(rules) > DOUBLE_RULE_GAP:
            rule_above = "single"
        else:
            rule_above = "double"
        prev_bottom = max(l["v1"] for l in strip)

        rows.append({
            "label": label,
            "u_start": min((l["u0"] for l in label_parts), default=None),
            "font_size": (label_parts or strip)[0]["size"],
            "has_leader": leader,
            "is_heading": all(s == "absent" for s in states),
            "rule_above_printed": rule_above,
            "values": values,
            "cell_states": states,
            "cells_as_extracted": [c[0].strip() if c else None for c in cells],
            "raw_text": " ".join([label] + [v for v in values if v]),
            "text_as_extracted": " | ".join(l["text"].strip() for l in sorted(strip, key=lambda l: l["u0"])),
        })
    return rows


# ---------------------------------------------------------------------------
# Whole table
# ---------------------------------------------------------------------------

def extract_table(doc_pdf, pages):
    """
    -> {page: transcription} for the given table pages, plus a report of how
    each page's column meaning was established.
    """
    geos = {p: page_geometry(doc_pdf[p - 1]) for p in pages}
    header_for, header_src = {}, {}
    for p in pages:
        h = read_header(geos[p])
        if h is not None:
            header_for[p], header_src[p] = h, p
            continue
        left = p - 1
        if left not in header_for:
            raise TextTableError(f"p{p} has no header row and p{left} is not a table page with one")
        if separator_positions(geos[p]) != header_for[left]["seps"]:
            raise TextTableError(f"p{p} separators {separator_positions(geos[p])} don't line up with "
                                 f"its left-hand page p{left} {header_for[left]['seps']}")
        # Right-hand continuation page: the body starts at the top of the page.
        header_for[p] = dict(header_for[left], body_top=float("-inf"))
        header_src[p] = left

    # Rows for every page, then indent levels against the whole table's left
    # edge (a continuation page has no flush-left heading of its own).
    page_rows = {p: read_rows(geos[p], header_for[p]["seps"], header_for[p]["body_top"]) for p in pages}
    starts = [r["u_start"] for rows in page_rows.values() for r in rows if r["u_start"] is not None]
    left_edge = min(starts) if starts else 0.0
    out = {}
    for p in pages:
        rows = page_rows[p]
        for r in rows:
            em = r["font_size"] or 7.0
            r["indent"] = 0 if r["u_start"] is None else round((r["u_start"] - left_edge) / em)
            # Single rules sit above subtotals and totals; a double rule closes
            # a section *under* a total, so it isn't this row's.
            r["rule_above"] = "single" if r["rule_above_printed"] == "single" else "none"
        h = header_for[p]
        out[p] = {
            "orientation_ok": True,
            "table_title": h["table_title"],
            "units_declared": h["units_declared"],
            "column_headers": h["column_headers"],
            "column_headers_from_page": header_src[p],
            "rows": rows,
            "legibility_notes": "",
        }
    return out


def sign_glyph_check(transcriptions, cols):
    """
    Evidence for the ∂/¥ decoding, per page: for each delta cell printed
    with a sign glyph, recompute minuend - subtrahend from the value columns
    and record whether the decoded sign agrees.
    """
    from extract_approps import parse_cell   # local import: extract_approps imports this module
    report = {}
    for p, tr in sorted(transcriptions.items()):
        agree, disagree, examples = defaultdict(int), defaultdict(int), []
        for r in tr["rows"]:
            for d in (c for c in cols if c["kind"] == "delta"):
                mi, si = d.get("minuend_index"), d.get("subtrahend_index")
                if mi is None or si is None:
                    continue
                cells = [parse_cell(r["values"][i]) for i in (mi, si, d["index"])]
                if any(c["kind"] not in ("number", "blank") for c in cells) or cells[2]["kind"] != "number":
                    continue
                if cells[2]["paren"]:
                    continue                      # memo rows: checked by the memo breakdown instead
                m, s, dv = (c["value"] or 0 for c in cells)
                raw = r["cells_as_extracted"][d["index"]] or ""
                if not any(g in raw for g in SIGN_GLYPHS) or dv == 0:
                    continue
                expected_sign = "+" if m - s > 0 else "-"
                decoded_sign = "+" if dv > 0 else "-"
                key = f"{'∂' if decoded_sign == '+' else '¥'}->{decoded_sign}"
                if abs(dv) == abs(m - s) and expected_sign == decoded_sign:
                    agree[key] += 1
                else:
                    disagree[key] += 1
                    examples.append(r["label"])
        report[p] = {"agree": dict(agree), "disagree": dict(disagree), "disagree_rows": examples[:5]}
    return report
