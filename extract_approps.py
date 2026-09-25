"""
extract_approps.py

Extract stage of the Data Pipeline: turns a PDF from document_store/ into
Appropriations Observations shaped like the Step 3 extraction schema (see the
scoping doc's "Extraction Schema and Validation Rules (Step 3)" section), then
runs the Step 3 checks that need nothing but the document itself
(validate_approps.py).

Per-page routing is deterministic, per the doc's Extraction instructions:
  1. Plain text extraction first (PyMuPDF).
  2. If the page's text is a GPO placeholder ("Insert offset folio ... here")
     or is near-empty once the typesetting slug is stripped, the page is an
     image-only insert: rasterize it and read it with a Claude vision call.
  3. Everything else stays text.

The vision call only transcribes: every cell comes back as the literal string
printed on the page. Everything numeric happens here in code, never in the
model: unit normalization to dollars, "---" (zero in a value column, no change
in a delta column), parenthetical memo rows (never additive), and rollup
reconciliation.

Vision results are cached per page (extraction_cache/<package_id>/pNNNN.json),
keyed to the PDF's sha256, so re-running never re-pays for a page that has
already been read. --offline uses the cache only and never calls the API.

Scope right now: the House committee report comparative statement of new
budget authority (the image-only fold-out table). Text pages are routed and
their text is kept for a later narrative pass, but no observations come from
them yet.

Usage:
    # Title III only (found by content, not page number)
    python extract_approps.py document_store/CRPT-119hrpt652.pdf --title "TITLE III"

    # Whole comparative table
    python extract_approps.py document_store/CRPT-119hrpt652.pdf

    # No API calls: use recorded vision transcriptions only
    python extract_approps.py document_store/CRPT-119hrpt652.pdf --title "TITLE III" \\
        --offline --cache-dir tests/fixtures/vision_cache \\
        --ground-truth tests/ground_truth/CRPT-119hrpt652_title_iii_fy2026_enacted.json

Requires ANTHROPIC_API_KEY for anything that isn't already cached. It is read
from the .env file next to this script (see .env.example), never from the
shell's inherited environment.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from dotenv import load_dotenv

import validate_approps

# Keys come from the project's .env, and win over anything inherited from the
# shell: cloud sessions don't reliably pass ANTHROPIC_API_KEY through, and
# Claude Code reads that same name for its own auth.
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, override=True)

DEFAULT_MODEL = "claude-opus-5"
PROMPT_VERSION = "2026-09-25.1"
CACHE_DIR = Path("./extraction_cache")
OUT_DIR = Path("./extractions")

# Pages whose only real text is shorter than this (after the GPO slug is
# stripped) are treated as image-only.
MIN_CONTENT_CHARS = 80
PLACEHOLDER_RE = re.compile(r"insert\s+offset\s+folio", re.I)

# The typesetting slug GPO stamps on every page of a committee report. None of
# it is content, so it can't count toward "does this page have a text layer".
SLUG_LINE_RES = [re.compile(p) for p in (
    r"^\d{1,4}$",                         # folio (page number)
    r"^VerDate\b",
    r"^\d{1,2}:\d{2} \w{3} \d{1,2}, \d{4}$",
    r"^Jkt \d+$",
    r"^PO \d+$",
    r"^Frm \d+$",
    r"^Fmt \d+$",
    r"^Sfmt \d+$",
    r"^[A-Z]:\\",                         # E:\HR\OC\HR652.XXX
    r"^[HS]R\d+(\.\d+)?$",               # HR652, HR652.034
    r"\bon \S+ with \S+$",                # "<operator> on <workstation> with HEARING"
)]

VISION_BASE_CONFIDENCE = 0.95
TITLE_HEADING_RE = re.compile(r"^\s*title\s+([ivxlc]+)\b", re.I)
DASH_RE = re.compile(r"^[-\u2010-\u2015\u2212]{1,}$")  # "---", "--", "—", "-"


# ---------------------------------------------------------------------------
# Document identity
# ---------------------------------------------------------------------------

BILL_VERSION_STAGES = {
    "rh": "House Reported", "rs": "Senate Reported",
    "eh": "House Passed", "es": "Senate Passed", "enr": "Enacted",
}


def describe_package(package_id):
    """Source Document fields that follow from a govinfo package id alone."""
    info = {"package_id": package_id, "document_type": "other", "stage": None,
            "chamber": None, "congress_session": None, "bill_id": None, "report_id": None}
    m = re.match(r"CRPT-(\d+)([hs])rpt(\d+)", package_id)
    if m:
        congress, ch, num = m.groups()
        info.update(document_type="committee_report", congress_session=congress,
                    chamber="House" if ch == "h" else "Senate",
                    stage="House Reported" if ch == "h" else "Senate Reported",
                    report_id=f"{'H' if ch == 'h' else 'S'}. Rpt. {congress}-{num}")
        return info
    m = re.match(r"BILLS-(\d+)(hr|s|hjres|sjres)(\d+)([a-z]+)$", package_id)
    if m:
        congress, btype, num, ver = m.groups()
        info.update(document_type="bill", congress_session=congress,
                    bill_id=f"{btype.upper()}{num}", stage=BILL_VERSION_STAGES.get(ver),
                    chamber="House" if btype.startswith("h") else "Senate")
        return info
    m = re.match(r"PLAW-(\d+)", package_id)
    if m:
        info.update(document_type="public_law", congress_session=m.group(1),
                    stage="Enacted", chamber="N/A")
    return info


SUBCOMMITTEES = {
    "COMMERCE, JUSTICE, SCIENCE": "CJS",
    "AGRICULTURE, RURAL DEVELOPMENT": "Agriculture-FDA",
    "ENERGY AND WATER": "Energy-Water",
    "FINANCIAL SERVICES AND GENERAL GOVERNMENT": "FSGG",
    "HOMELAND SECURITY": "Homeland Security",
    "INTERIOR, ENVIRONMENT": "Interior-Environment",
    "LABOR, HEALTH AND HUMAN SERVICES": "Labor-HHS-Education",
    "LEGISLATIVE BRANCH": "Legislative Branch",
    "MILITARY CONSTRUCTION, VETERANS AFFAIRS": "MilCon-VA",
    "NATIONAL SECURITY, DEPARTMENT OF STATE": "NSRP",
    "STATE, FOREIGN OPERATIONS": "SFOPS",
    "TRANSPORTATION, HOUSING AND URBAN DEVELOPMENT": "THUD",
    "DEPARTMENT OF DEFENSE APPROPRIATIONS": "Defense",
}


def detect_subcommittee(page_texts):
    head = " ".join(page_texts[:3]).upper()
    head = re.sub(r"\s+", " ", head)
    for needle, name in SUBCOMMITTEES.items():
        if needle in head:
            return name
    return None


# ---------------------------------------------------------------------------
# Step 1-3: per-page routing
# ---------------------------------------------------------------------------

def content_text(raw):
    """Page text with GPO's typesetting slug lines removed."""
    keep = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or any(r.search(s) for r in SLUG_LINE_RES):
            continue
        keep.append(s)
    return "\n".join(keep)


def route_page(page):
    raw = page.get_text()
    content = content_text(raw)
    if PLACEHOLDER_RE.search(raw):
        route, reason = "vision", "gpo_offset_folio_placeholder"
    elif len(content) < MIN_CONTENT_CHARS:
        route, reason = "vision", f"near_empty_text_layer ({len(content)} chars)"
    else:
        route, reason = "text", "text_layer_present"
    return {"page": page.number + 1, "route": route, "reason": reason,
            "text_chars": len(raw), "content_chars": len(content), "text": raw}


# ---------------------------------------------------------------------------
# Vision: rendering
# ---------------------------------------------------------------------------

# Rotation (PyMuPDF prerotate degrees) that turns each reported orientation
# upright. GPO's fold-out tables are printed with the text running bottom-to-
# top, which prerotate(90) fixes (checked against H.Rpt. 119-652 p.169).
ORIENTATION_ROTATION = {
    "upright": 0,
    "sideways_text_reads_bottom_to_top": 90,
    "sideways_text_reads_top_to_bottom": 270,
    "upside_down": 180,
}


def ink_bbox(page, dpi=36, threshold=160, margin_pt=12):
    """Bounding box (page coords) of everything that isn't white, so the page
    can be cropped before rasterizing -- more pixels go to the table itself."""
    pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    w, h, stride, s = pm.width, pm.height, pm.stride, pm.samples
    xs, ys = [], []
    for y in range(h):
        row = s[y * stride:y * stride + w]
        dark = [x for x, v in enumerate(row) if v < threshold]
        if dark:
            ys.append(y)
            xs.extend((dark[0], dark[-1]))
    if not xs:
        return page.rect
    scale = 72.0 / dpi
    r = pymupdf.Rect(min(xs) * scale - margin_pt, min(ys) * scale - margin_pt,
                     (max(xs) + 1) * scale + margin_pt, (max(ys) + 1) * scale + margin_pt)
    return r & page.rect


def render_png(page, dpi, rotation=0, crop=True, max_edge=2400):
    clip = ink_bbox(page) if crop else page.rect
    long_edge_pt = max(clip.width, clip.height)
    dpi = min(dpi, int(max_edge * 72 / long_edge_pt))
    zoom = dpi / 72.0
    pm = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(rotation), clip=clip)
    return pm.tobytes("png"), dpi


# ---------------------------------------------------------------------------
# Vision: Claude calls
# ---------------------------------------------------------------------------

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "orientation": {"type": "string", "enum": list(ORIENTATION_ROTATION)},
        "page_kind": {"type": "string", "enum": [
            "budget_authority_comparative_table", "other_table", "not_a_table"]},
        "table_title": {"type": "string"},
        "title_headings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["orientation", "page_kind", "table_title", "title_headings"],
    "additionalProperties": False,
}

CLASSIFY_PROMPT = """This is one scanned page of a U.S. congressional committee report.
Report:
- orientation: how the printed text is oriented in this image.
- page_kind: "budget_authority_comparative_table" if the page is part of a table of appropriations amounts by account with numeric columns (for example headed "COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY"); "other_table" for any other table; "not_a_table" otherwise (roll-call votes, prose, etc.).
- table_title: the table's printed title, verbatim, or "" if none.
- title_headings: every bill-title heading printed on the page, verbatim (for example "TITLE III - SCIENCE"). Only headings that begin with the word TITLE. [] if none."""

TRANSCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "orientation_ok": {"type": "boolean"},
        "table_title": {"type": "string"},
        "units_declared": {"type": "string"},
        "column_headers": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "indent": {"type": "integer"},
                "is_heading": {"type": "boolean"},
                "rule_above": {"type": "string", "enum": ["none", "single", "double"]},
                "values": {"type": "array", "items": {"type": "string"}},
                "raw_text": {"type": "string"},
            },
            "required": ["label", "indent", "is_heading", "rule_above", "values", "raw_text"],
            "additionalProperties": False,
        }},
        "legibility_notes": {"type": "string"},
    },
    "required": ["orientation_ok", "table_title", "units_declared", "column_headers",
                 "rows", "legibility_notes"],
    "additionalProperties": False,
}

TRANSCRIBE_SYSTEM = """You transcribe scanned pages of U.S. appropriations documents into JSON. You are a transcriber, not an analyst: every value you return must be exactly what is printed on the page. Never compute, total, correct, infer, or fill in a number. If a character is genuinely unreadable, transcribe your best reading and describe the problem in legibility_notes."""

TRANSCRIBE_PROMPT = """Transcribe the table on this page.

- orientation_ok: false if the text in this image is not upright (sideways or upside down); then return no rows.
- table_title: the printed table title, verbatim, lines joined with a single space.
- units_declared: the units line verbatim, e.g. "(Amounts in thousands)", or "" if none is printed.
- column_headers: the headers of the numeric columns, left to right, each header's lines joined with a single space (e.g. "FY 2026 Enacted", "Bill", "Bill vs. Enacted").
- rows: every row of the table body, top to bottom, including headings. Do not include the column-header block or rows that consist only of rule lines.
  - label: the row's label text, without dot leaders. If a label wraps onto a second printed line, join the two lines into one row with a single space and put the values on that row.
  - indent: how far the label starts from the left margin of the label column, in levels: 0 = flush left, 1 = indented once, 2 = indented twice, and so on. Centered headings count by their visual offset (usually 1 or more).
  - is_heading: true for a row that has no numbers in any column (title headings such as "TITLE III - SCIENCE", agency or bureau headings, headings ending in a colon).
  - rule_above: "single" if a dashed rule is printed across the numeric columns directly above this row's numbers, "double" for a rule of equals signs, otherwise "none". Ignore the rule under the column headers.
  - values: one string per column_headers entry, in the same order, exactly as printed: keep commas, a leading "+" or "-", surrounding parentheses, and dash placeholders such as "---". Use "" for a column that is blank on this row.
  - raw_text: the whole printed row as one line: label, then each printed value, separated by single spaces.
- legibility_notes: anything hard to read, or ""."""


class VisionError(RuntimeError):
    pass


def _client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(f"No ANTHROPIC_API_KEY: add it to {ENV_PATH} (copy .env.example), "
                         "or use --offline to run from cached vision results only.")
    return anthropic.Anthropic(api_key=key)


def _call_json(client, model, system, content, schema, effort, max_tokens, use_fallbacks):
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if system:
        kwargs["system"] = system
    if use_fallbacks:
        # Server-side refusal fallback: a declined request is re-run on a
        # fallback model inside the same call.
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    with client.beta.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise VisionError(f"model refused: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        raise VisionError("response truncated at max_tokens")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if text is None:
        raise VisionError("no text block in response")
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens,
             "model": msg.model}
    return json.loads(text), usage


def _image_block(png):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(png).decode("ascii")}}


def classify_page(client, model, page, use_fallbacks):
    png, _ = render_png(page, dpi=110, rotation=0)
    result, usage = _call_json(client, model, None, [_image_block(png), {"type": "text", "text": CLASSIFY_PROMPT}],
                               CLASSIFY_SCHEMA, effort="low", max_tokens=4000,
                               use_fallbacks=use_fallbacks)
    return result, usage


def transcribe_page(client, model, page, rotation, dpi, use_fallbacks):
    tried = []
    for rot in (rotation, (rotation + 180) % 360):
        png, used_dpi = render_png(page, dpi=dpi, rotation=rot)
        result, usage = _call_json(
            client, model, TRANSCRIBE_SYSTEM,
            [_image_block(png), {"type": "text", "text": TRANSCRIBE_PROMPT}],
            TRANSCRIBE_SCHEMA, effort="high", max_tokens=48000, use_fallbacks=use_fallbacks)
        tried.append(rot)
        if result.get("orientation_ok"):
            return result, usage, rot, used_dpi
    raise VisionError(f"page {page.number + 1}: no upright rendering among rotations {tried}")


# ---------------------------------------------------------------------------
# Vision cache
# ---------------------------------------------------------------------------

class VisionCache:
    def __init__(self, root, package_id, pdf_sha256):
        self.dir = Path(root) / package_id
        self.pdf_sha256 = pdf_sha256

    def path(self, page_no):
        return self.dir / f"p{page_no:04d}.json"

    def load(self, page_no):
        p = self.path(page_no)
        if not p.exists():
            return None
        entry = json.loads(p.read_text())
        if entry.get("meta", {}).get("pdf_sha256") != self.pdf_sha256:
            print(f"  cache {p} is for a different version of this PDF; ignoring", file=sys.stderr)
            return None
        return entry

    def save(self, page_no, entry):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path(page_no).write_text(json.dumps(entry, indent=2))


# ---------------------------------------------------------------------------
# Rows -> hierarchy
# ---------------------------------------------------------------------------

def norm_name(s):
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_key(label):
    m = TITLE_HEADING_RE.match(label or "")
    return f"title {m.group(1).lower()}" if m else None


def parse_cell(raw):
    """-> dict(kind, value, paren). kind: blank | dash | number | unparsed."""
    s = (raw or "").strip().replace("\u2212", "-")
    if not s:
        return {"kind": "blank", "value": None, "paren": False, "raw": raw}
    paren = s.startswith("(") and s.endswith(")")
    inner = s[1:-1].strip() if paren else s
    if DASH_RE.match(inner):
        return {"kind": "dash", "value": 0, "paren": paren, "raw": raw}
    m = re.fullmatch(r"([+-]?)\s*\$?([\d,]+)", inner)
    if not m or not re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", m.group(2)):
        return {"kind": "unparsed", "value": None, "paren": paren, "raw": raw}
    v = int(m.group(2).replace(",", ""))
    return {"kind": "number", "value": -v if m.group(1) == "-" else v, "paren": paren, "raw": raw}


def classify_row(row, cells):
    label = row["label"].strip()
    low = label.lower()
    if all(c["kind"] == "blank" for c in cells):
        return "title_heading" if title_key(label) else "heading"
    printed = [c for c in cells if c["kind"] in ("number", "dash", "unparsed")]
    if (label.startswith("(") and label.endswith(")")) or \
            (printed and all(c["paren"] for c in printed if c["kind"] != "dash")
             and any(c["paren"] for c in printed)):
        return "memo"
    if low.startswith("grand total"):
        return "grand_total"
    if low.startswith("total"):
        return "total"
    if low.startswith("subtotal") or row.get("rule_above", "none") != "none":
        return "subtotal"
    return "line"


class Node:
    _seq = 0

    def __init__(self, row, kind, cells, page, path, title):
        Node._seq += 1
        self.id = Node._seq
        self.row, self.kind, self.cells, self.page = row, kind, cells, page
        self.path, self.title = path, title
        self.children, self.memos = [], []
        self.parent_line = None
        self.frames = []           # enclosing agency/bureau heading names, outermost first
        self.match = None          # how a rollup found its children
        self.complete = True       # False when its children may not all be extracted

    @property
    def label(self):
        return self.row["label"].strip()


class Frame:
    def __init__(self, name, kind, complete=True):
        self.name, self.kind, self.complete = name, kind, complete
        self.items, self.run_start = [], 0


def build_hierarchy(rows, table_starts_with_title):
    """
    Turn the page-ordered row stream into Nodes with each rollup's children
    attached. Structure comes from the printed labels and rules only:

      TITLE heading          -> opens a title frame (closing whatever is open)
      other heading          -> opens an agency/bureau frame; a sibling heading
                                closes the previous one if it already has lines
      line                   -> item of the innermost open frame
      Subtotal / ruled row   -> sums the items since the frame's last subtotal
      Total, <name>          -> closes the frame with that name (anything opened
                                inside it is folded in); sums its items
      Grand total            -> sums the title totals
      memo (parenthetical)   -> attached to the row above it, never summed
    """
    nodes = []
    root = Frame("<document>", "root", complete=table_starts_with_title)
    stack = [root]
    current_title = None
    last = None

    def frame_path():
        return [f.name for f in stack if f.kind == "heading"]

    def fold_into_parent():
        f = stack.pop()
        stack[-1].items.extend(f.items)
        stack[-1].complete = stack[-1].complete and f.complete
        return f

    for page, row, cells in rows:
        kind = classify_row(row, cells)
        label = row["label"].strip()

        if kind == "title_heading":
            while len(stack) > 1:
                fold_into_parent()
            current_title = label
            stack.append(Frame(label, "title"))
            last = None
            continue

        if kind == "heading":
            top = stack[-1]
            if top.kind == "heading" and top.items:
                fold_into_parent()
            stack.append(Frame(label.rstrip(":").strip(), "heading",
                               complete=stack[-1].complete))
            continue

        if kind == "memo":
            node = Node(row, kind, cells, page, (last.path if last else []) + [label], current_title)
            node.parent_line = last
            node.frames = list(last.frames) if last else []
            if last is not None:
                last.memos.append(node)
            nodes.append(node)
            continue

        node = Node(row, kind, cells, page, frame_path() + [label], current_title)
        node.frames = frame_path()
        top = stack[-1]

        if kind == "line":
            prev = top.items[-1] if top.items else None
            if prev is not None and prev.kind == "line" and row.get("indent", 0) > prev.row.get("indent", 0):
                node.parent_line = prev
                node.path = frame_path() + [prev.label, label]
            top.items.append(node)

        elif kind == "subtotal":
            node.children = top.items[top.run_start:]
            node.match = "run_since_last_subtotal"
            node.complete = top.complete
            top.items = top.items[:top.run_start] + [node]
            top.run_start = len(top.items)

        elif kind == "grand_total":
            while len(stack) > 1:
                fold_into_parent()
            node.children = [n for n in root.items]
            node.match = "title_totals"
            node.complete = root.complete
            node.path, node.frames = [label], []
            root.items.append(node)

        elif kind == "total":
            target = norm_name(re.sub(r"^total\s*,?\s*", "", label, flags=re.I))
            tkey = title_key(target)
            idx = None
            for i in range(len(stack) - 1, 0, -1):
                f = stack[i]
                if f.kind == "title" and tkey and title_key(f.name) == tkey:
                    idx = i
                    break
                fn = norm_name(f.name)
                if f.kind == "heading" and fn and (fn == target or target.startswith(fn) or fn.startswith(target)):
                    idx = i
                    break
            if idx is not None:
                node.frames = [f.name for f in stack[:idx + 1] if f.kind == "heading"]
                node.path = node.frames + [label]
                while len(stack) - 1 > idx:
                    fold_into_parent()
                f = stack.pop()
                node.children, node.match, node.complete = f.items, "named_frame", f.complete
                stack[-1].items.append(node)
                if f.kind == "title":
                    # rows after a title total (memos) still belong to it
                    current_title = f.name
            elif top.kind == "heading":
                f = stack.pop()
                node.children, node.match, node.complete = f.items, "innermost_heading_unmatched", False
                stack[-1].items.append(node)
            else:
                node.children, node.match, node.complete = top.items[top.run_start:], "unmatched", False
                top.items = top.items[:top.run_start] + [node]
                top.run_start = len(top.items)

        last = node
        nodes.append(node)

    return nodes


# ---------------------------------------------------------------------------
# Columns and observations
# ---------------------------------------------------------------------------

def classify_columns(headers, bill_fy, doc_stage):
    cols = []
    for i, h in enumerate(headers):
        hn = re.sub(r"\s+", " ", h).strip()
        low = hn.lower()
        col = {"index": i, "header": hn, "kind": "value", "fiscal_year": None, "stage": None}
        if " vs" in low or "compared" in low or "change" in low:
            col["kind"] = "delta"
            parts = re.split(r"\s+vs\.?\s+", hn, flags=re.I)
            col["minuend"], col["subtrahend"] = (parts + [None])[:2]
        else:
            m = re.search(r"(?:FY\s*)?(\d{4})", hn)
            if m:
                col["fiscal_year"] = int(m.group(1))
            if "enacted" in low:
                col["stage"] = "Enacted"
            elif "request" in low or "budget estimate" in low:
                col["stage"] = "President's Budget"
                col["fiscal_year"] = col["fiscal_year"] or bill_fy
            elif low == "bill" or low.startswith("bill") or "recommended" in low:
                col["stage"] = doc_stage
                col["fiscal_year"] = col["fiscal_year"] or bill_fy
        cols.append(col)
    for col in cols:
        if col["kind"] == "delta":
            col["minuend_index"] = _match_col(cols, col.get("minuend"))
            col["subtrahend_index"] = _match_col(cols, col.get("subtrahend"))
    return cols


def _match_col(cols, name):
    if not name:
        return None
    n = name.strip().lower()
    for c in cols:
        if c["kind"] != "value":
            continue
        h = c["header"].lower()
        if h == n or h.endswith(n) or (n == "enacted" and "enacted" in h) or (n == "request" and "request" in h):
            return c["index"]
    return None


UNIT_MULTIPLIERS = {"dollars": 1, "thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}


def parse_units(declared):
    low = (declared or "").lower()
    for word in ("billions", "millions", "thousands"):
        if word in low:
            return word
    if "dollars" in low:
        return "dollars"
    return None


def amount_type_for(node):
    low = node.label.lower()
    t = {"amount_type": "budget authority", "offsetting_collections": False, "transfer_direction": None}
    if "transfer" in low:
        t["amount_type"] = "transfer"
        t["transfer_direction"] = "out" if "transfer out" in low or "transfers out" in low else "in"
    elif "rescission" in low:
        t["amount_type"] = "rescission"
    elif "offsetting" in low or "fee collection" in low:
        t["amount_type"] = "offsetting_collection"
        t["offsetting_collections"] = True
    return t


def fund_type_hint(label):
    low = label.lower()
    if "trust fund" in low:
        return "trust"
    if "working capital" in low:
        return "working_capital"
    if "revolving" in low:
        return "revolving"
    return "unknown"


OBS_NAMESPACE = uuid.UUID("5b1c1c55-8a53-4cbe-9f0a-6f2c2a0b0d3e")


def build_observations(nodes, cols, unit, page_meta, doc, table_title):
    """One observation per (row, value column). Delta columns are derivable and
    produce none; headings produce none."""
    obs = []
    mult = UNIT_MULTIPLIERS[unit]
    for node in nodes:
        for col in cols:
            if col["kind"] != "value":
                continue
            cell = node.cells[col["index"]] if col["index"] < len(node.cells) else parse_cell("")
            if cell["kind"] == "blank":
                continue
            amount = cell["value"]
            key = f"{doc['package_id']}|p{node.page}|{' / '.join(node.path)}|{col['header']}"
            src = page_meta[node.page]
            t = amount_type_for(node)
            heading_frames = node.frames
            obs.append({
                "observation_id": str(uuid.uuid5(OBS_NAMESPACE, key)),
                "node_id": node.id,
                "account_name_as_written": node.label,
                "account_path": " / ".join(node.path),
                "agency": heading_frames[0] if heading_frames else (node.label if node.kind == "line" else None),
                "bureau": heading_frames[1] if len(heading_frames) > 1 else None,
                "title": node.title,
                "row_kind": node.kind,
                "is_rollup": node.kind in ("subtotal", "total", "grand_total"),
                "is_memo": node.kind == "memo",
                "fiscal_year": col["fiscal_year"],
                "stage": col["stage"],
                "chamber": doc["chamber"] if col["stage"] == doc["stage"] else None,
                "bill_id": doc["bill_id"],
                "report_id": doc["report_id"],
                "column_header": col["header"],
                "column_index": col["index"],
                "amount": amount,
                "amount_as_printed": (cell["raw"] or "").strip(),
                "amount_unit": unit,
                "amount_dollars": amount * mult if amount is not None else None,
                "amount_is_dash_zero": cell["kind"] == "dash",
                **t,
                "transfer_counterpart_name_as_written": None,
                "fund_type_hint": fund_type_hint(node.label),
                "source_page": str(node.page),
                "source_table_or_section": f"{table_title} -- {node.title or '(continued from an earlier page)'}",
                "raw_text_excerpt": node.row.get("raw_text", ""),
                "extraction_method": src["extraction_method"],
                "extraction_confidence": VISION_BASE_CONFIDENCE,
                "verification_status": "unverified",
            })
    return obs


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def select_table_pages(entries, target_key):
    """Pages to transcribe, from the classify pass. With a title filter:
    from the first page showing that title's heading through the first later
    page showing a different title heading (the title's total can sit on it)."""
    table = [p for p, e in entries if (e.get("classify") or {}).get("page_kind") == "budget_authority_comparative_table"]
    if not target_key:
        return table
    start = None
    for i, p in enumerate(table):
        keys = [title_key(h) for h in (dict(entries)[p]["classify"].get("title_headings") or [])]
        if start is None and target_key in keys:
            start = i
        elif start is not None and any(k and k != target_key for k in keys):
            return table[start:i + 1]
    return table[start:] if start is not None else table


def title_filter_ok(node, target_key):
    return target_key is None or title_key(node.title or "") == target_key


def run(pdf_path, title=None, model=DEFAULT_MODEL, cache_dir=CACHE_DIR, offline=False,
        dpi=200, out_dir=OUT_DIR, use_fallbacks=True, verbose=True):
    pdf_path = Path(pdf_path)
    data = pdf_path.read_bytes()
    pdf_sha = hashlib.sha256(data).hexdigest()
    package_id = pdf_path.stem
    doc_pdf = pymupdf.open(stream=data, filetype="pdf")
    doc = describe_package(package_id)
    cache = VisionCache(cache_dir, package_id, pdf_sha)
    target_key = title_key(title) if title else None
    if title and not target_key:
        raise SystemExit(f"--title must look like 'TITLE III', got {title!r}")

    log = print if verbose else (lambda *a, **k: None)

    # 1. Route every page.
    routes = [route_page(doc_pdf[i]) for i in range(len(doc_pdf))]
    vision_pages = [r["page"] for r in routes if r["route"] == "vision"]
    log(f"{package_id}: {len(routes)} pages, {len(routes) - len(vision_pages)} text, "
        f"{len(vision_pages)} image-only -> vision")

    client = None

    def get_client():
        nonlocal client
        if offline:
            raise VisionError("offline mode: page not in cache")
        if client is None:
            client = _client()
        return client

    # 2. Classify image-only pages (cheap, low-res), from cache where possible.
    entries = {}
    usage_log = []
    uncached = []
    for p in vision_pages:
        entry = cache.load(p) or {"meta": {"pdf_sha256": pdf_sha, "page": p}}
        if entry.get("classify") is None and entry.get("transcription") is None:
            try:
                result, usage = classify_page(get_client(), model, doc_pdf[p - 1], use_fallbacks)
            except VisionError as e:
                if offline:
                    uncached.append(p)
                else:
                    log(f"  p{p}: not classified ({e})")
                continue
            entry["classify"] = result
            entry["meta"].update(model=usage["model"], prompt_version=PROMPT_VERSION,
                                 source="claude_api", classified_at=_now())
            usage_log.append(("classify", p, usage))
            cache.save(p, entry)
        if entry.get("classify") is None and entry.get("transcription") is not None:
            # A recorded transcription implies the page is a table page.
            entry["classify"] = {"orientation": "upright", "page_kind": "budget_authority_comparative_table",
                                 "table_title": entry["transcription"].get("table_title", ""),
                                 "title_headings": [r["label"] for r in entry["transcription"]["rows"]
                                                    if title_key(r["label"]) and r.get("is_heading")]}
        entries[p] = entry

    if uncached:
        log(f"  offline: {len(uncached)} image-only pages not in cache, skipped: {uncached}")
    ordered = sorted(entries.items())
    table_pages = select_table_pages(ordered, target_key)
    all_table_pages = select_table_pages(ordered, None)
    log(f"  comparative-table pages: {all_table_pages}")
    log(f"  selected for transcription: {table_pages}")

    # 3. Transcribe selected pages; extend forward if the title's total
    #    hasn't shown up yet (a heading the low-res pass missed).
    def ensure_transcribed(p):
        entry = entries[p]
        if entry.get("transcription") is not None:
            return True
        c = entry["classify"]
        rot = ORIENTATION_ROTATION.get(c.get("orientation"), 0)
        try:
            result, usage, used_rot, used_dpi = transcribe_page(get_client(), model, doc_pdf[p - 1],
                                                                rot, dpi, use_fallbacks)
        except VisionError as e:
            log(f"  p{p}: not transcribed ({e})")
            return False
        entry["transcription"] = result
        entry["meta"].update(model=usage["model"], prompt_version=PROMPT_VERSION, source="claude_api",
                             rotation=used_rot, dpi=used_dpi, transcribed_at=_now())
        usage_log.append(("transcribe", p, usage))
        cache.save(p, entry)
        return True

    done = [p for p in table_pages if ensure_transcribed(p)]

    def title_total_seen():
        for p in done:
            for r in entries[p]["transcription"]["rows"]:
                if r["label"].lower().startswith("total") and title_key(re.sub(r"^total\s*,?\s*", "", r["label"], flags=re.I)) == target_key:
                    return True
        return False

    if target_key:
        remaining = [p for p in all_table_pages if p > (max(done) if done else 0)]
        while not title_total_seen() and remaining:
            p = remaining.pop(0)
            if ensure_transcribed(p):
                done.append(p)
        if not title_total_seen():
            log(f"  WARNING: never found the total row for {title}")

    # 4. Rows -> hierarchy -> observations.
    page_meta = {}
    rows = []
    units_by_page = {}
    headers = None
    table_title = ""
    for p in sorted(done):
        entry = entries[p]
        tr = entry["transcription"]
        src = entry["meta"].get("source", "claude_api")
        page_meta[p] = {"extraction_method": "human-entered" if src == "manual_transcription" else "AI-extracted",
                        "source": src, "model": entry["meta"].get("model"),
                        "units_declared": tr.get("units_declared", ""),
                        "units_parsed": parse_units(tr.get("units_declared", "")),
                        "column_headers": tr.get("column_headers", []),
                        "legibility_notes": tr.get("legibility_notes", "")}
        units_by_page[p] = tr.get("units_declared", "")
        headers = headers or tr["column_headers"]
        table_title = table_title or tr.get("table_title", "")
        for r in tr["rows"]:
            vals = list(r.get("values", []))
            vals += [""] * (len(tr["column_headers"]) - len(vals))
            rows.append((p, r, [parse_cell(v) for v in vals]))

    if not rows:
        raise SystemExit("nothing transcribed -- set ANTHROPIC_API_KEY or point --cache-dir at recorded pages")

    first_table_page = all_table_pages[0] if all_table_pages else None
    starts_with_title = bool(rows) and done and min(done) == first_table_page and \
        classify_row(rows[0][1], rows[0][2]) == "title_heading"
    Node._seq = 0
    nodes = build_hierarchy(rows, starts_with_title)

    m = re.search(r"BILL FOR (\d{4})", table_title, re.I)
    bill_fy = int(m.group(1)) if m else None
    cols = classify_columns(headers, bill_fy, doc["stage"])

    declared_units = {parse_units(u) for u in units_by_page.values()}
    unit = next(iter(declared_units)) if len(declared_units) == 1 else None
    if unit is None:
        raise SystemExit(f"table pages disagree on units (or declare none): {units_by_page}")

    selected_nodes = [n for n in nodes if title_filter_ok(n, target_key)]
    observations = build_observations(selected_nodes, cols, unit, page_meta, doc, table_title)

    # 5. Validate.
    records, summary = validate_approps.validate(selected_nodes, cols, observations, page_meta, unit)

    page_texts = [r["text"] for r in routes]
    source_document = {
        "document_id": str(uuid.uuid5(OBS_NAMESPACE, f"{package_id}|{pdf_sha}")),
        "package_id": package_id,
        "source_agency": "GPO",
        "url_or_identifier": f"https://api.govinfo.gov/packages/{package_id}/pdf",
        "content_sha256": pdf_sha,
        "document_type": doc["document_type"],
        "fiscal_year": bill_fy,
        "stage": doc["stage"],
        "congress_session": doc["congress_session"],
        "subcommittee": detect_subcommittee(page_texts),
        "report_id": doc["report_id"],
        "bill_id": doc["bill_id"],
        "retrieval_timestamp": None,
    }
    result = {
        "source_document": source_document,
        "extraction": {
            "extracted_at": _now(),
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "title_filter": title,
            "table_title": table_title,
            "amount_unit_declared": unit,
            "columns": cols,
            "pages_transcribed": sorted(done),
            "page_sources": {str(p): {k: v for k, v in m.items()} for p, m in page_meta.items()},
            "vision_calls_this_run": [{"pass": k, "page": p, **u} for k, p, u in usage_log],
        },
        "page_routing": [{k: v for k, v in r.items() if k != "text"} for r in routes],
        "observations": observations,
        "validation_records": records,
        "validation_summary": summary,
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f".{target_key.replace(' ', '-')}" if target_key else ""
    out_path = out_dir / f"{package_id}{suffix}.json"
    out_path.write_text(json.dumps(result, indent=2))
    text_path = out_dir / f"{package_id}.text-pages.jsonl"
    with text_path.open("w") as f:
        for r in routes:
            if r["route"] == "text":
                f.write(json.dumps({"page": r["page"], "text": r["text"]}) + "\n")
    result["_paths"] = {"observations": str(out_path), "text_pages": str(text_path)}
    return result


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def compare_ground_truth(result, gt_path):
    gt = json.loads(Path(gt_path).read_text())
    by_key = {(o["account_path"], o["column_header"]): o for o in result["observations"]}
    rows, ok = [], True
    for item in gt["expected"]:
        o = by_key.get((item["account_path"], gt["column_header"]))
        got = o["amount_dollars"] if o else None
        match = got == item["amount_dollars"]
        ok &= match
        rows.append((item["name"], item["amount_dollars"], got, match))
    return ok, rows


def print_report(result, gt_rows=None):
    s = result["validation_summary"]
    ex = result["extraction"]
    print(f"\nTable: {ex['table_title']}  [{ex['amount_unit_declared']} -> dollars]")
    print(f"Pages transcribed: {ex['pages_transcribed']}  "
          f"(sources: {sorted({v['source'] for v in ex['page_sources'].values()})})")
    print(f"Observations: {len(result['observations'])}")
    print("\nValidation checks (by rule):")
    for rule, counts in s["by_rule"].items():
        print(f"  {rule:15s} " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for rule, why in s["not_run"].items():
        print(f"  {rule:15s} not run: {why}")
    print("\nRollup reconciliation:")
    for line in s["rollup_lines"]:
        print("  " + line)
    print(f"\nverification_status: {s['verification_status_counts']}")
    if gt_rows is not None:
        print("\nGround truth:")
        for name, want, got, match in gt_rows:
            g = f"{got:,}" if got is not None else "MISSING"
            print(f"  {'OK  ' if match else 'FAIL'} {name:65s} want {want:>16,}  got {g:>16}")


def main():
    ap = argparse.ArgumentParser(description="Extract appropriations observations from a stored PDF.")
    ap.add_argument("pdf", help="PDF from document_store/, e.g. document_store/CRPT-119hrpt652.pdf")
    ap.add_argument("--title", help='Only this bill title, found by its heading, e.g. "TITLE III"')
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--offline", action="store_true", help="Use cached vision results only; never call the API")
    ap.add_argument("--no-fallbacks", action="store_true", help="Don't send the server-side refusal fallback beta")
    ap.add_argument("--ground-truth", help="JSON of expected dollar figures to diff against")
    args = ap.parse_args()

    result = run(args.pdf, title=args.title, model=args.model, cache_dir=args.cache_dir,
                 offline=args.offline, dpi=args.dpi, out_dir=args.out_dir,
                 use_fallbacks=not args.no_fallbacks)
    gt_ok, gt_rows = (True, None)
    if args.ground_truth:
        gt_ok, gt_rows = compare_ground_truth(result, args.ground_truth)
    print_report(result, gt_rows)
    print(f"\nWrote {result['_paths']['observations']}")
    failed = result["validation_summary"]["failures"]
    if failed or not gt_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
