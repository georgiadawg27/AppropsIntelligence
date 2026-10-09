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
already been read. --offline uses the cache only and never calls the API;
--live does the opposite: it ignores cached results and re-reads every
image-only page with a fresh API call (writing the new results back to the
cache), which is how the extraction itself -- not just the parsing -- gets
tested.

Scope right now: the comparative statement of new budget authority, from
either route:
  - image-only fold-out inserts (House reports) -> Claude vision calls;
  - fold-outs typeset as real text (Senate reports) -> text_tables.py, read
    straight from the text layer and its drawn rules with no API call. A
    document with no image-only pages never needs ANTHROPIC_API_KEY.
Other text pages are kept for a later narrative pass; no observations come
from them yet.

Usage:
    # Title III only (found by content, not page number)
    python extract_approps.py document_store/CRPT-119hrpt652.pdf --title "TITLE III"

    # Whole comparative table
    python extract_approps.py document_store/CRPT-119hrpt652.pdf

    # Fresh vision calls for every page, cache ignored (tests the model's reading)
    python extract_approps.py document_store/CRPT-119hrpt652.pdf --title "TITLE III" --live \\
        --ground-truth tests/ground_truth/CRPT-119hrpt652_title_iii_fy2026_enacted.json

    # No API calls: use recorded vision transcriptions only
    python extract_approps.py document_store/CRPT-119hrpt652.pdf --title "TITLE III" \\
        --offline --cache-dir tests/fixtures/vision_cache \\
        --ground-truth tests/ground_truth/CRPT-119hrpt652_title_iii_fy2026_enacted.json

Requires ANTHROPIC_API_KEY for anything that isn't already cached. It is read
from the .env file next to this script (see .env.example), never from the
shell's inherited environment.
"""

import argparse
from collections import Counter
import base64
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from dotenv import load_dotenv

import accounts
import ocr_tables
import text_tables
from subcommittees import subcommittee_of, subcommittees_named
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

# Text first: a page is read from its text layer whenever it has one; a page image goes to vision only where the
# page has no usable text layer, or where the text reading fails the table's own arithmetic. Every run has a hard
# cap on page images sent (classify, transcribe and re-read calls alike, each rendering counted): reaching it stops
# the run and reports. APPROPS_MAX_PAGE_IMAGES or --max-page-images sets it.
MAX_PAGE_IMAGES = int(os.environ.get("APPROPS_MAX_PAGE_IMAGES", "80"))


class PageImageCapReached(SystemExit):
    """The run's page-image cap is spent: the run stops here and says so."""


class PageImageBudget:
    def __init__(self, cap):
        self.cap, self.used, self.pages = cap, 0, []

    def spend(self, page_label):
        if self.used >= self.cap:
            raise PageImageCapReached(
                f"page-image cap reached: {self.used} of {self.cap} page images sent this run "
                f"({', '.join(self.pages)}); stopped before {page_label}. Raise --max-page-images "
                f"(or APPROPS_MAX_PAGE_IMAGES) only with the owner's approval.")
        self.used += 1
        self.pages.append(page_label)


_BUDGET = None            # the running extraction's PageImageBudget (run() sets it)

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
LEADER_BLANK_RE = re.compile(r"^\.{2,}$")                # a cell printed as dot leaders only


# ---------------------------------------------------------------------------
# Document identity
# ---------------------------------------------------------------------------

BILL_VERSION_STAGES = {
    "rh": "House Reported", "rs": "Senate Reported",
    "eh": "House Passed", "es": "Senate Passed", "enr": "Enacted",
}


MANUAL_DOC_TYPES = {"jes": "explanatory_statement", "explanatory_statement": "explanatory_statement",
                    "committee_report": "committee_report",
                    "congressional_budget_justification": "congressional_budget_justification",
                    "bill": "bill", "public_law": "public_law", "other": "other"}
STAGE_CHAMBER = {"House Reported": "House", "House Passed": "House", "Senate Reported": "Senate",
                 "Senate Passed": "Senate", "Enacted": "N/A", "President's Budget": "N/A"}


def load_manifest_entry(package_id, manifest_path):
    try:
        return json.loads(Path(manifest_path).read_text()).get(package_id)
    except (OSError, ValueError):
        return None


def describe_package(package_id, manifest_entry=None):
    """Source Document fields that follow from a govinfo package id alone --
    or, for a manually ingested document, from what was recorded at ingest."""
    info = {"package_id": package_id, "document_type": "other", "stage": None,
            "chamber": None, "congress_session": None, "bill_id": None, "report_id": None}
    if manifest_entry and manifest_entry.get("ingest_method") == "manual":
        stage = manifest_entry.get("stage")
        info.update(document_type=MANUAL_DOC_TYPES.get(manifest_entry.get("doc_type"), "other"),
                    stage=stage, chamber=STAGE_CHAMBER.get(stage),
                    bill_id=manifest_entry.get("bill_id"), report_id=manifest_entry.get("report_id"))
        return info
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


def detect_subcommittee(page_texts):
    return subcommittee_of(" ".join(page_texts[:3]))


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
    if PLACEHOLDER_RE.search(raw) and len(content) < MIN_CONTENT_CHARS:
        route, reason = "vision", "gpo_offset_folio_placeholder"
    elif PLACEHOLDER_RE.search(raw) and not ocr_tables.has_ocr_layer(page):
        # GPO's offset-folio placeholder beside a real text layer (H.Rept. 116-62's comparative statement): text
        # first; run() sends the page to vision only if the text holds no table or fails its arithmetic
        route, reason = "text", "text_layer_present (offset folio placeholder)"
    elif len(content) >= MIN_CONTENT_CHARS and ocr_tables.has_ocr_layer(page):
        # a scan with an invisible OCR text layer: read that text for free,
        # with a vision re-read only for pages that fail the arithmetic gate
        route, reason = "ocr", "ocr_text_layer_over_scan"
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


def _call_json(client, model, system, content, schema, effort, max_tokens, use_fallbacks, page_label="page"):
    if _BUDGET is not None:
        _BUDGET.spend(page_label)          # one page image per call; stops the run at the cap
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
    started = time.monotonic()
    with client.beta.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    seconds = round(time.monotonic() - started, 1)
    # the call is paid for whatever comes back: every failure carries its usage
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens,
             "seconds": seconds, "model": msg.model}
    problem = None
    if msg.stop_reason == "refusal":
        problem = f"model refused: {getattr(msg, 'stop_details', None)}"
    elif msg.stop_reason == "max_tokens":
        problem = "response truncated at max_tokens"
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if problem is None and text is None:
        problem = "no text block in response"
    if problem:
        err = VisionError(problem)
        err.usage = usage
        raise err
    return json.loads(text), usage


def _image_block(png):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(png).decode("ascii")}}


def classify_page(client, model, page, use_fallbacks):
    png, _ = render_png(page, dpi=110, rotation=0)
    result, usage = _call_json(client, model, None, [_image_block(png), {"type": "text", "text": CLASSIFY_PROMPT}],
                               CLASSIFY_SCHEMA, effort="low", max_tokens=4000,
                               use_fallbacks=use_fallbacks, page_label=f"p{page.number + 1} classify")
    return result, usage


def transcribe_page(client, model, page, rotation, dpi, use_fallbacks):
    tried = []
    total = {"input_tokens": 0, "output_tokens": 0, "seconds": 0.0}
    # The classified rotation, then its flip; then -- the low-res classify
    # pass can call a sideways page "upright" (H.Rept. 119-271 pp. 347, 358
    # between pages it read as sideways) -- the two perpendicular ones.
    for rot in (rotation, (rotation + 180) % 360, (rotation + 90) % 360, (rotation + 270) % 360):
        png, used_dpi = render_png(page, dpi=dpi, rotation=rot)
        try:
            result, usage = _call_json(
                client, model, TRANSCRIBE_SYSTEM,
                [_image_block(png), {"type": "text", "text": TRANSCRIBE_PROMPT}],
                TRANSCRIBE_SCHEMA, effort="high", max_tokens=48000, use_fallbacks=use_fallbacks,
                page_label=f"p{page.number + 1} transcribe (rotation {rot})")
        except VisionError as e:
            # count this attempt and any earlier wrong-way-up one
            for k in total:
                total[k] += (getattr(e, "usage", None) or {}).get(k, 0)
            total.update(model=(getattr(e, "usage", None) or {}).get("model", model), attempts=len(tried) + 1,
                         seconds=round(total["seconds"], 1))
            e.usage = total
            raise
        tried.append(rot)
        # A wrong-way-up attempt is still paid for: count it.
        for k in total:
            total[k] += usage.get(k, 0)
        total.update(model=usage["model"], attempts=len(tried))
        if result.get("orientation_ok"):
            total["seconds"] = round(total["seconds"], 1)
            return result, total, rot, used_dpi
    err = VisionError(f"page {page.number + 1}: no upright rendering among rotations {tried}")
    total["seconds"] = round(total["seconds"], 1)
    err.usage = total                  # those calls were paid for: the caller logs them
    raise err


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


def squash(s):
    """Letters and digits only: how a rollup label is compared with the
    section or lines it names, so OCR's stray spaces ("Expl oration",
    "Nat ional Aeronautics") don't break the match."""
    return norm_name(s).replace(" ", "")


def names_line(named, label):
    """Does a subtotal named `named` (squashed) cover this line? Its label
    starts with the name -- allowing the edit distance the account matcher
    allows for OCR noise (a misspelt "enviromental" still belongs to
    "Subtotal, Construction and environmental ...")."""
    head = squash(label)[:len(named)]
    if head == named or accounts.distance(head, named) <= accounts.allowed_distance(named):
        return True
    # "Subtotal, NCI, program level" names "National Cancer Institute (NCI)"
    acronym = ACRONYM_RE.match(label.strip())
    return bool(acronym) and squash(acronym.group(2)) == named


ACRONYM_RE = re.compile(r"^(.*?)\s*\(([A-Za-z&\u2013\u2014-]{2,12})\)\s*[.:]?\s*$")

# Parallel totals. HHS prints several totals for one thing, each with its
# scope in the label: "Total, NIH (with CURES Act funding)", "Total, NIH,
# program level (with CURES and PHS Evaluation Act Funding)", "Total, NIH
# program level (excluding ARPA-H)"; Medicaid's "program level, available
# this fiscal year" / "Total, Grants to States for Medicaid" / "..., appropriated
# in this bill". They are siblings of one family (same name once the scope is
# taken off), not children of each other to be summed. Roles:
#   view     -- another way of counting the same thing (program level adds
#               transfers that aren't this bill's budget authority; excluding,
#               discretionary, available this fiscal year): printed beside the
#               family's total, never summed into anything
#   in_bill  -- what the bill itself appropriates, advance included: replaces
#               the family's total in what its parent sums (CMS's total adds
#               Medicaid's "appropriated in this bill", not its current-year total)
#   headline -- "with CURES Act funding": the NIH / Public Health Service
#               figure the next level up sums (decision 2026-09-27: the
#               headline, with CURES also kept as its own component row)
SCOPE_RES = [
    (re.compile(r"[\s,]*\(?\s*with\s+CURES\s+Act\s+funding\s*\)?\s*$", re.I), "headline"),
    (re.compile(r"[\s,]*(?:appropriated|available)\s+in\s+this\s+bill\s*$", re.I), "in_bill"),
    (re.compile(r"[\s,]*(?:fiscal\s+year\s+)?program\s+level\b.*$", re.I), "view"),
    (re.compile(r"[\s,]*available\s+this\s+fiscal\s+year\s*$", re.I), "view"),
    (re.compile(r"[\s,]*\(?\s*excluding\b[^)]*\)?\s*$", re.I), "view"),
    (re.compile(r"[\s,]*including\b.*$", re.I), "view"),
    (re.compile(r"[\s,]*discretionary\s*$", re.I), "view"),
]
ROLLUP_PREFIX_RE = re.compile(r"^\s*(?:sub)?total\s*[,.:]?\s*", re.I)


def split_scope(label):
    """ "Total, NIH program level (excluding ARPA-H)" -> ("NIH", "program level (excluding ARPA-H)", "view");
    "Total, Current Year" -> ("", "Current Year", "view") (its family is the one printed just above);
    an unscoped label -> (name, None, None)."""
    name = ROLLUP_PREFIX_RE.sub("", label).strip().rstrip(".")
    if re.fullmatch(r"current\s+year", name, re.I):
        return "", "Current Year", "view"
    for rx, role in SCOPE_RES:
        m = rx.search(name)
        if m and m.start() > 0:
            return name[:m.start()].strip(" ,"), name[m.start():].strip(" ,"), role
    return name, None, None


def scope_component(scope, role):
    """The component a scoped total is stored under: its printed scope,
    lower-cased with punctuation as underscores. The headline scope has none."""
    if not scope or role == "headline":
        return None
    return re.sub(r"[^a-z0-9]+", "_", scope.lower().replace("\u2013", "-")).strip("_") or None


def title_key(label):
    m = TITLE_HEADING_RE.match(label or "")
    return f"title {m.group(1).lower()}" if m else None


def parse_cell(raw):
    """-> dict(kind, value, paren). kind: blank | dash | number | unparsed.
    A blank printed as dot leaders is still blank (not zero, not missing) but
    keeps leader=True, so an all-blank line item isn't mistaken for a heading."""
    s = (raw or "").strip().replace("\u2212", "-")
    if not s:
        return {"kind": "blank", "value": None, "paren": False, "raw": raw}
    if LEADER_BLANK_RE.match(s):
        return {"kind": "blank", "value": None, "paren": False, "raw": raw, "leader": True}
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
    if all(c["kind"] == "blank" and not c.get("leader") for c in cells):
        return "title_heading" if title_key(label) else "heading"
    printed = [c for c in cells if c["kind"] in ("number", "dash", "unparsed")]
    if (label.startswith("(") and label.endswith(")")) or \
            (printed and all(c["paren"] for c in printed if c["kind"] != "dash")
             and any(c["paren"] for c in printed)):
        return "memo"
    # Rollups are recognised by label text alone -- never by indentation or a
    # model-judged rule line. Checked against every House and Senate table
    # read so far: every row with one of these labels is a rollup, and no
    # rollup row lacks one except Senate's "...reclassification (emergency)"
    # (left as a line, so its parent's total fails loudly and is flagged).
    if low.startswith("grand total"):
        return "grand_total"
    if low.startswith("total"):
        return "total"
    if low.startswith("subtotal") or is_account_rollup(label):
        return "subtotal"
    return "line"


TRAILING_TOTAL_RE = re.compile(r"\S\s+total$", re.I)


def is_account_rollup(label):
    """Rollups of a single account: "Direct appropriation" (the account net of
    its own offsetting collections / transfers) and "<account> Total" (e.g.
    House "OIG Total")."""
    low = label.strip().lower()
    letters = re.sub(r"[^a-z]", "", low)          # OCR splits words: "Di rect appropriation"
    return letters == "directappropriation" or bool(TRAILING_TOTAL_RE.search(low)
                                                    and not low.startswith(("total", "subtotal", "grand total")))


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
        self.scope = self.scope_role = None     # parallel totals: see SCOPE_RES
        self.sibling_of = None     # the family member this parallel total is printed beside
        self.window_start = None   # first node id whose rows can explain it
        self.absorbed = []         # rows an in_bill / headline total took the place of

    @property
    def label(self):
        return self.row["label"].strip()


class Frame:
    def __init__(self, name, kind, complete=True):
        self.name, self.kind, self.complete = name, kind, complete
        self.items, self.run_start = [], 0


def _cell_amount(cell):
    """A cell's figure for summing: printed number; a dash or a blank is zero;
    None when it can't be read."""
    if cell["kind"] == "number":
        return cell["value"]
    if cell["kind"] in ("dash", "blank"):
        return 0
    return None


def sums_to(node, rows):
    """Do these rows add up to the node's printed figures, in every column the
    node prints a number in (value and difference columns alike)?"""
    cols = [i for i, c in enumerate(node.cells) if c["kind"] == "number"]
    if not cols:
        return False
    for i in cols:
        vals = [_cell_amount(r.cells[i]) if i < len(r.cells) else 0 for r in rows]
        if any(v is None for v in vals) or sum(vals) != node.cells[i]["value"]:
            return False
    return True


def refit_by_printed_total(node, stack, nodes):
    """
    The printed rollups are constraints on the hierarchy. When the rows the
    label rules gave a Total or Subtotal don't add up to what it prints --
    and no memo printed with them explains the difference (the validator's
    program-level case) -- take instead the run of rows printed directly
    above it that does, in every printed column: the shortest such run.
    Section headings can't tell a sibling section from a nested one
    ("DEPARTMENTAL MANAGEMENT" under an unprinted "Office of the Secretary"),
    the arithmetic can. More than one run that fits (other than by rows that
    are zero throughout) is recorded on the node, and the validator flags it
    instead of passing it.
    """
    import validate_approps
    if sums_to(node, node.children):
        return
    if node.children:
        first = min(c.id for c in node.children)
        memos = [m for m in nodes if first <= m.id < node.id and m.kind == "memo"]
        cols = [i for i, c in enumerate(node.cells) if c["kind"] == "number"]
        kid = lambda r, i: (_cell_amount(r.cells[i]) or 0) if i < len(r.cells) else 0
        gaps = [node.cells[i]["value"] - sum(kid(r, i) for r in node.children) for i in cols]
        if memos and validate_approps.signed_explanation(gaps, [(m, [kid(m, i) for i in cols]) for m in memos]):
            return
    holder = next((f for f in reversed(stack) if f.items and f.items[-1] is node), None)
    if holder is None:
        return
    pool = holder.items[:-1] + [c for c in node.children if not any(c is x for x in holder.items)]
    fits = [k for k in range(1, len(pool) + 1) if sums_to(node, pool[-k:])]
    if not fits:
        return
    k = fits[0]
    zero = lambda r: all((_cell_amount(c) or 0) == 0 for c in r.cells)
    node.fit_alternatives = [pool[-j].label for j in fits[1:] if not all(zero(r) for r in pool[-j:-k])]
    node.children = pool[-k:]
    holder.items = pool[:-k] + [node]
    holder.run_start = len(holder.items) if node.match in ("run_since_last_subtotal", "named_section", "unmatched") \
        else min(holder.run_start, len(holder.items))
    node.match += "+fit_by_printed_total"
    node.complete = True


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
    aliases = {}            # squashed acronym -> squashed name, from "NATIONAL INSTITUTES OF HEALTH (NIH)"
    families = {}           # parallel totals, by family name (split_scope)
    last_family = None

    def family_key(base):
        m = ACRONYM_RE.match(base)
        if m and m.group(1).strip():
            aliases[squash(m.group(2))] = squash(m.group(1))
            base = m.group(1)
        k = squash(base)
        return aliases.get(k, k)

    def first_id(n):
        todo, ids = [n], []
        while todo:
            x = todo.pop()
            ids.append(x.id)
            todo.extend(x.children)
        return min(ids)

    def frame_index(base, strict=False):
        """The open frame a total named `base` closes, or None. strict: the
        heading's own name (its "(NIH)" taken off), not a prefix of it --
        "Payments to States" must not close "Payments to States for Child
        Support Enforcement ..."."""
        target = squash(base)
        targets = {t for t in (target, aliases.get(target)) if t}
        tkey = title_key(base)
        for i in range(len(stack) - 1, 0, -1):
            f = stack[i]
            if f.kind == "title" and tkey and title_key(f.name) == tkey:
                return i
            fn = squash(f.name)
            if strict:
                m = ACRONYM_RE.match(f.name)
                fn = squash(m.group(1)) if m and m.group(1).strip() else fn
                if f.kind == "heading" and fn in targets:
                    return i
            elif f.kind == "heading" and fn and any(fn == t or t.startswith(fn) or fn.startswith(t) for t in targets):
                return i
        return None

    def cut_from(first):
        """Remove `first` and everything after it from whichever open frame
        holds it -> the removed rows ([] if no frame holds it)."""
        for f in reversed(stack):
            for i, x in enumerate(f.items):
                if x is first:
                    cut, f.items = f.items[i:], f.items[:i]
                    f.run_start = min(f.run_start, len(f.items))
                    return cut
        return []

    def named_rows(named, run):
        """The rows a "Subtotal, <named>" covers at the end of the run: the
        lines named for it (and lines nested under them); else, when a line
        it doesn't name was printed after its named line ("Gabriella Miller
        Kids First Research Act" after "Office of the Director"), everything
        from the nearest named line down."""
        def is_named(x):
            return x.kind == "line" and (names_line(named, x.label) or
                                         (x.parent_line is not None and names_line(named, x.parent_line.label)))
        n = 0
        while n < len(run) and is_named(run[-1 - n]):
            n += 1
        if n:
            return run[len(run) - n:], "named_run"
        j = next((i for i in range(len(run) - 1, -1, -1) if run[i].kind == "line" and names_line(named, run[i].label)), None)
        return (run[j:], "named_from_line") if j is not None else ([], None)

    def view_children(label, top):
        """A view rollup's own rows, left where they are: the lines it names
        (and lines nested under them), else -- a name that is an abbreviation
        ("Subtotal, B&F, program level" for "Buildings and Facilities") --
        the last line and its nested lines; a view total takes the run."""
        run = top.items[top.run_start:]
        if ROLLUP_PREFIX_RE.match(label) and label.lower().startswith("sub"):
            rows, how = named_rows(squash(split_scope(label)[0]), run)
            if rows:
                return rows, how
            n = 0
            while n < len(run) and run[-1 - n].kind == "line" and run[-1 - n].parent_line is not None:
                n += 1
            return (run[len(run) - n - 1:], "last_line") if n < len(run) and run[-1 - n].kind == "line" else ([], "none")
        return run, "run_since_last_subtotal"

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
            families.clear()
            last_family = None
            current_title = label
            stack.append(Frame(label, "title"))
            last = None
            continue

        if kind == "heading":
            family_key(label.rstrip(":").strip())      # registers "(NIH)" -> "NATIONAL INSTITUTES OF HEALTH"
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

        if kind in ("total", "subtotal") and not is_account_rollup(label):
            base, scope, role = split_scope(label)
            node.scope, node.scope_role = scope, role
            key = family_key(base) if base else last_family
            fam = families.get(key) if key else None
            if role and fam:
                # a parallel total of a family already printed: a sibling, not a sum
                node.sibling_of, node.window_start = fam["members"][-1], fam["window_start"]
                node.match = "parallel_" + role
                if role != "view":
                    # in_bill / headline: what the parent sums from here on,
                    # in place of the family's total (or, when that total is a
                    # side view, its rows) and every row printed since
                    rep = fam["rep"]
                    anchors = [rep] + ([rep.children[0]] if rep.children else [])
                    node.absorbed = next((c for c in (cut_from(a) for a in anchors) if c), [])
                    idx = frame_index(base, strict=True) if kind == "total" else None
                    if idx is not None:
                        node.frames = [f.name for f in stack[:idx + 1] if f.kind == "heading"]
                        node.path = node.frames + [label]
                        while len(stack) - 1 > idx:
                            fold_into_parent()
                        stack.pop()           # its rows are the family's, now represented by this total
                    stack[-1].items.append(node)
                    fam["rep"] = node
                fam["members"].append(node)
                last_family = key
                last = node
                nodes.append(node)
                continue
            if role == "view":
                # first of its family ("Subtotal, NCI, program level"; "Total,
                # Medicaid program level, available this fiscal year"): a side
                # rollup -- checked against its rows, which stay where they are
                # for the next rollup to sum; closes no frame
                node.children, how = view_children(label, top)
                node.match, node.complete = "view_" + how, top.complete and bool(node.children)
                families[key] = {"members": [node], "rep": node,
                                 "window_start": first_id(node) if node.children else node.id}
                last_family = key
                last = node
                nodes.append(node)
                continue

        if kind == "line":
            prev = top.items[-1] if top.items else None
            if prev is not None and prev.kind == "line" and row.get("indent", 0) > prev.row.get("indent", 0):
                node.parent_line = prev
                node.path = frame_path() + [prev.label, label]
            top.items.append(node)

        elif kind == "subtotal" and is_account_rollup(label):
            # One account's rollup. Its children are the lines since the last
            # heading or rollup -- unless some of those are indent-nested (the
            # House prints an account's offsets indented under it), in which
            # case only the last top-level line and the lines nested under it.
            # "<account> Total" always takes the last top-level line and its
            # nested lines. The arithmetic check on the rollup then tests the
            # grouping indentation produced.
            tail = []
            for item in reversed(top.items[top.run_start:]):
                if item.kind != "line":
                    break
                tail.insert(0, item)
            in_tail = {id(n) for n in tail}
            top_level = [i for i, n in enumerate(tail)
                         if n.parent_line is None or id(n.parent_line) not in in_tail]
            has_nesting = len(top_level) < len(tail)
            if re.sub(r"[^a-z]", "", label.lower()) == "directappropriation" and not has_nesting:
                group = tail
            else:
                group = tail[top_level[-1]:] if top_level else []
            in_group = {id(n) for n in group}
            node.children = group
            if group:
                # name it for its account: several "Direct appropriation"
                # rows can sit under one heading
                node.path = frame_path() + [group[0].label, label]
            # "_nested": the grouping came from model-read indentation, so this
            # rollup's arithmetic is what confirms (or refutes) that nesting.
            node.match = "single_account_nested" if any(
                n.parent_line is not None and id(n.parent_line) in in_group for n in group) else "single_account"
            node.complete = top.complete and bool(group)
            top.items = top.items[:len(top.items) - len(group)] + [node]

        elif kind == "subtotal":
            # "Subtotal, Exploration" (Senate) rolls up only the lines named
            # for that account directly above it ("Exploration", "Exploration
            # (emergency)"), and doesn't close the run: a later unnamed
            # "Subtotal" still includes it. A bare "Subtotal" (House) sums
            # everything since the previous bare subtotal.
            m = re.match(r"subtotal\s*[,.:]\s*(.+)", label, flags=re.I)
            named = squash(split_scope(label)[0]) if m else None
            run = top.items[top.run_start:]
            heading = ACRONYM_RE.match(top.name)
            if named and top.kind == "heading" and named in {squash(top.name), squash(heading.group(1)) if heading else None}:
                # "Subtotal, Mental Health" under the heading "Mental Health":
                # the whole section, whatever subtotals ran inside it
                children, how = top.items, "named_section"
            else:
                children, how = named_rows(named, run) if named else ([], None)
                if not children:
                    children, how = run, "run_since_last_subtotal"
            node.children, node.match, node.complete = children, how, top.complete
            if len(children) == 1 and children[0].kind == "line" and \
                    [c.get("value") for c in node.cells] != [c.get("value") for c in children[0].cells]:
                # a view of one line, not its rollup: its figures differ from
                # the line's ("Subtotal" = "Programs of Regional and National
                # Significance" + its "(Prevention and Public Health Fund)"
                # memo), so the line stays in the run for the next rollup to
                # sum. One printing the line's own figures (the CJS JES's
                # "Subtotal. Operations, research and Facilities") is its
                # rollup and takes its place, as any rollup does.
                top.run_start = len(top.items)
            else:
                keep = len(top.items) - len(children)
                top.items = top.items[:keep] + [node]
                if how in ("run_since_last_subtotal", "named_section"):
                    top.run_start = len(top.items)

        elif kind == "grand_total":
            while len(stack) > 1:
                fold_into_parent()
            # a grand total sums the title-level totals, never another grand
            # total printed above it; "Grand total excluding <X>" leaves out
            # the total named X
            node.children = [n for n in root.items if n.kind != "grand_total"]
            m = re.search(r"\bexcluding\s+(.+)$", label, flags=re.I)
            if m:
                excluded = squash(m.group(1))
                node.children = [n for n in node.children
                                 if not squash(re.sub(r"^total\s*[,.]?\s*", "", n.label, flags=re.I)).startswith(excluded)]
            node.match = "title_totals"
            node.complete = root.complete
            node.path, node.frames = [label], []
            root.items.append(node)

        elif kind == "total":
            idx = frame_index(split_scope(label)[0])
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

        if kind in ("total", "subtotal") and not is_account_rollup(label) and not node.match.startswith("view_"):
            refit_by_printed_total(node, stack, nodes)

        if kind in ("total", "subtotal") and not is_account_rollup(label):
            base = split_scope(label)[0]
            if base:        # a bare "Subtotal" names no family
                key = family_key(base)
                families[key] = {"members": [node], "rep": node, "window_start": first_id(node)}
                last_family = key
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
            # "Bill vs. Enacted"; "Senate Committee recommendation compared
            # with (+ or -) 2025 appropriation"
            parts = re.split(r"\s+(?:vs\.?|compared\s+with)\s+", hn, flags=re.I)
            parts = [re.sub(r"^\([^)]*\)\s*", "", x).strip() for x in parts]
            col["minuend"], col["subtrahend"] = (parts + [None])[:2]
        else:
            m = re.search(r"(?:FY\s*)?(\d{4})", hn)
            if m:
                col["fiscal_year"] = int(m.group(1))
            if "enacted" in low or (m and "appropriation" in low):
                # "FY 2026 Enacted"; the Senate's prior-year "2025 appropriation"
                col["stage"] = "Enacted"
            elif "request" in low or "budget estimate" in low:
                col["stage"] = "President's Budget"
                col["fiscal_year"] = col["fiscal_year"] or bill_fy
            elif low == "bill" or low.startswith(("bill", "final bill")) or "recommend" in low:
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
        # "Committee vs. Enacted" names "Committee Recommendation" (H.Rept. 119-696)
        if h == n or h.endswith(n) or n.endswith(h) or h.startswith(n + " ") or (n == "enacted" and "enacted" in h) \
                or (n == "request" and "request" in h):
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


ADVANCE_RE = re.compile(r"^\s*(?:new\s+)?advances?\b|\badvance\s+appropriations?\b", re.I)
CURES_RE = re.compile(r"\bCURES\s+Act\b", re.I)
PRIOR_YEAR_ADVANCE_RE = re.compile(r"^\s*less\s+appropriations\s+provided\s+in\s+prior\s+years?\b", re.I)


def amount_type_for(node):
    low = node.label.lower()
    t = {"amount_type": "budget authority", "offsetting_collections": False, "transfer_direction": None}
    if PRIOR_YEAR_ADVANCE_RE.search(node.label):
        # "Less appropriations provided in prior years": this fiscal year's
        # share appropriated last year as its advance, printed negative
        # (decision 2026-09-28: its own amount_type, stored as printed)
        t["amount_type"] = "prior_year_advance"
    elif ADVANCE_RE.search(node.label):
        # "New advance, 1st quarter, FY 2027": appropriated in this bill for
        # the next fiscal year (decision 2026-09-27: its own amount_type; the
        # observation's fiscal_year stays the year it was appropriated)
        t["amount_type"] = "advance"
    elif "transfer" in low:
        t["amount_type"] = "transfer"
        t["transfer_direction"] = "out" if "transfer out" in low or "transfers out" in low else "in"
    elif "rescission" in low:
        t["amount_type"] = "rescission"
    elif "offsetting" in low or "fee collection" in low:
        t["amount_type"] = "offsetting_collection"
        t["offsetting_collections"] = True
    elif re.search(r"\(\s*emergency\s*\)\s*$", low):
        # an emergency line is a separate supplemental observation, as the
        # pilot stores Exploration / CECR / R&RA
        t["amount_type"] = "supplemental"
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
    seen_keys = Counter()
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
            # the same label can repeat under one heading on one page; keep ids distinct
            seen_keys[key] += 1
            if seen_keys[key] > 1:
                key += f"|#{seen_keys[key]}"
            src = page_meta[node.page]
            t = amount_type_for(node)
            if t["amount_type"] == "advance":
                # a first-quarter advance is for the fiscal year after the one it is appropriated in
                named = re.search(r"\bFY\s*(\d{4})", node.label)
                t["advance_for_fiscal_year"] = col["fiscal_year"] + 1 if col["fiscal_year"] else None
                t["advance_evidence"] = (f"appropriated in FY{col['fiscal_year']} ({col['header']}), available FY"
                                         f"{t['advance_for_fiscal_year']}; label: {node.label!r}"
                                         + (f" (names FY{named.group(1)}, the bill year's advance)" if named else ""))
            component = scope_component(node.scope, node.scope_role) or \
                ("CURES" if node.scope is None and CURES_RE.search(node.label) else None)
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
                "chamber": STAGE_CHAMBER.get(col["stage"]),
                "bill_id": doc["bill_id"],
                "report_id": doc["report_id"],
                "column_header": col["header"],
                "column_index": col["index"],
                # amount is dollars (the Data Dictionary's amount) from here on;
                # the value in the table's own unit is kept beside it
                "amount": amount * mult if amount is not None else None,
                "amount_in_units": amount,
                "amount_as_printed": (cell["raw"] or "").strip(),
                "amount_unit": unit,
                "amount_is_dash_zero": cell["kind"] == "dash",
                **t,
                # a parallel total's printed scope; the CURES Act line (see SCOPE_RES)
                "account_component": component,
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


DIVISION_RE = re.compile(r"\bDIVISION ([A-Z])\s*[\u2014\u2013]\s*")


def division_ranges(page_texts):
    """
    The divisions of a multi-division document (an omnibus act, a
    Congressional Record explanatory statement covering several bills), as
    [(letter, name, first_page, last_page)] in page order; [] for a
    one-subcommittee document. A division starts at its own uppercase
    heading ("DIVISION B—DEPARTMENTS OF LABOR, HEALTH AND ...", which the
    Record may break across lines and hyphenate: "HOME-\nLAND") and runs to
    the page before the next one (two starting on one page share it). A
    page naming three or more divisions is a table of contents, not a
    heading.
    """
    starts = {}
    for i, raw in enumerate(page_texts):
        text = re.sub(r"\s+", " ", re.sub(r"-\s*\n\s*", "", raw or ""))
        hits = [(m.group(1), m.start(), text[m.end():m.end() + 250]) for m in DIVISION_RE.finditer(text)]
        if len({letter for letter, _, _ in hits}) >= 3:
            continue
        for letter, pos, after in hits:
            # the name runs up to the first mixed-case word ("... ACT, 2026 The explanatory ...")
            name = re.split(r"\s(?=[A-Z][a-z])", after, maxsplit=1)[0].strip()
            starts.setdefault(letter, (i + 1, name, pos))
    order = sorted(((letter, (first, name)) for letter, (first, name, pos) in starts.items()),
                   key=lambda kv: (kv[1][0], starts[kv[0]][2]))
    letters = [letter for letter, _ in order]
    # A document's own divisions run A, B, C ... through its pages (a letter
    # may be skipped: P.L. 119-75 has no Division C). Headings quoted out of
    # that order are statute text a report reprints ("changes in existing
    # law": S.Rept. 118-207 quotes Divisions H, C, J, B; S.Rept. 119-55's
    # "Other Appropriations" rows cite P.L. 117-58 Division J, then P.L.
    # 117-159 Division B, on one page), not its divisions.
    if len(order) < 2 or letters != sorted(set(letters)):
        return []
    return [(letter, name, first, max(first, order[k + 1][1][0] - 1) if k + 1 < len(order) else len(page_texts))
            for k, (letter, (first, name)) in enumerate(order)]


def resolve_division(divisions, subcommittee):
    """The one division whose heading names the subcommittee, or SystemExit:
    a multi-division document is never read from its first matching title
    heading (Division A's Title II is Defense's, not HHS's)."""
    if not subcommittee:
        raise SystemExit("multi-division document (" + ", ".join(f"{d[0]}: {d[1][:40]}" for d in divisions) +
                         ") -- say which subcommittee's division to read (--subcommittee, or the manifest's subcommittee)")
    hits = [d for d in divisions if subcommittee in subcommittees_named(d[1])]
    if len(hits) != 1:
        raise SystemExit(f"{len(hits)} divisions name {subcommittee!r}: " +
                         "; ".join(f"{d[0]}: {d[1][:60]}" for d in divisions))
    return hits[0]


def title_filter_ok(node, target_key):
    return target_key is None or title_key(node.title or "") == target_key


def source_document_fields(package_id, pdf_sha, doc, manifest_entry, page_texts):
    """Source Document row: govinfo-fetched or manually ingested (proposal
    approved 2026-09-25: ingest_method, advance_copy, confirmation_status,
    reconciled_with_document_id, ingested_by)."""
    import public_links
    m = manifest_entry or {}
    manual = m.get("ingest_method") == "manual"
    # the public link (public_links.py: checked against the file's sha256), never the API fetch
    # link, which only works with an api_key; none yet -> an identifier, not a link
    public = public_links.no_api_key(m.get("public_url"))
    return {
        "document_id": str(uuid.uuid5(OBS_NAMESPACE, f"{package_id}|{pdf_sha}")),
        "package_id": package_id,
        "source_agency": m.get("source_agency") or ("manual ingest" if manual else "GPO"),
        "url_or_identifier": public or (f"manual:{package_id}" if manual else f"govinfo:{package_id}"),
        "link_needs_review": bool(m.get("link_needs_review")),
        "content_sha256": pdf_sha,
        "document_type": doc["document_type"],
        "fiscal_year": m.get("fiscal_year"),
        "stage": doc["stage"],
        "congress_session": doc["congress_session"],
        "subcommittee": m.get("subcommittee") or detect_subcommittee(page_texts),
        "report_id": doc["report_id"],
        "bill_id": doc["bill_id"],
        "retrieval_timestamp": m.get("fetched_at") or m.get("ingested_at"),
        "ingest_method": "manual" if manual else "govinfo_api",
        "ingested_by": m.get("ingested_by"),
        "advance_copy": bool(m.get("advance_copy")),
        "confirmation_status": m.get("confirmation_status") or ("official" if not manual else None),
        "reconciled_with_document_id": m.get("reconciled_with_document_id"),
    }


def run(pdf_path, title=None, model=DEFAULT_MODEL, cache_dir=CACHE_DIR, offline=False,
        dpi=200, out_dir=OUT_DIR, use_fallbacks=True, verbose=True, live=False, manifest_path=None,
        subcommittee=None, single_division=False, max_page_images=None):
    global _BUDGET
    _BUDGET = PageImageBudget(MAX_PAGE_IMAGES if max_page_images is None else max_page_images)
    try:
        return _run(pdf_path, title, model, cache_dir, offline, dpi, out_dir, use_fallbacks, verbose, live,
                    manifest_path, subcommittee, single_division)
    finally:
        _BUDGET = None


def _run(pdf_path, title, model, cache_dir, offline, dpi, out_dir, use_fallbacks, verbose, live, manifest_path,
         subcommittee, single_division):
    pdf_path = Path(pdf_path)
    data = pdf_path.read_bytes()
    pdf_sha = hashlib.sha256(data).hexdigest()
    package_id = pdf_path.stem
    doc_pdf = pymupdf.open(stream=data, filetype="pdf")
    manifest_entry = load_manifest_entry(package_id, manifest_path or pdf_path.parent / "manifest.json")
    doc = describe_package(package_id, manifest_entry)
    cache = VisionCache(cache_dir, package_id, pdf_sha)
    if live and offline:
        raise SystemExit("--live and --offline are mutually exclusive")
    target_key = title_key(title) if title else None
    if title and not target_key:
        raise SystemExit(f"--title must look like 'TITLE III', got {title!r}")

    log = print if verbose else (lambda *a, **k: None)

    # 1. Route every page. A multi-division document is narrowed to the
    #    requested subcommittee's division first; its title is looked for
    #    only there.
    routes = [route_page(doc_pdf[i]) for i in range(len(doc_pdf))]
    division = None
    # a one-subcommittee document that reprints another act's division headings (a Senate draft quoting
    # the CR in force: "DIVISION A--CONTINUING APPROPRIATIONS ACT, 2022") is read whole
    divisions = [] if single_division else division_ranges([r["text"] for r in routes])
    if divisions:
        division = resolve_division(divisions, subcommittee or (manifest_entry or {}).get("subcommittee"))
        log(f"  {len(divisions)} divisions; reading Division {division[0]} ({division[1][:70]}), pp. {division[2]}-{division[3]}")
    in_scope = (lambda p: division[2] <= p <= division[3]) if division else (lambda p: True)
    vision_pages = [r["page"] for r in routes if r["route"] == "vision" and in_scope(r["page"])]
    text_table_pages = text_tables.find_table_pages(doc_pdf, [r["page"] for r in routes
                                                              if r["route"] == "text" and in_scope(r["page"])])
    # a placeholder page whose text layer holds no table the text path can read: its table is an image, so vision
    for r in routes:
        if r["route"] == "text" and "offset folio placeholder" in r["reason"] and in_scope(r["page"]) \
                and r["page"] not in text_table_pages:
            r["route"], r["reason"] = "vision", "gpo_offset_folio_placeholder (text layer holds no table)"
            vision_pages.append(r["page"])
    vision_pages.sort()
    ocr_page_list = [r["page"] for r in routes if r["route"] == "ocr" and in_scope(r["page"])]
    ocr_table_pages = ocr_tables.find_table_pages(doc_pdf, ocr_page_list) if ocr_page_list else []
    # the table already found in the text or OCR layer: an image-only page far from it (front matter) can't be part
    # of it -- only the pages next to the table are worth an image
    found = text_table_pages + ocr_table_pages
    if found:
        vision_pages[:] = [p for p in vision_pages if min(found) - 2 <= p <= max(found) + 2]
    log(f"{package_id}: {len(routes)} pages, {len(routes) - len(vision_pages) - len(ocr_page_list)} text, "
        f"{len(ocr_page_list)} scanned with an OCR layer, {len(vision_pages)} image-only -> vision")
    if text_table_pages:
        log(f"  text-native comparative-table pages (read from the text layer, no API call): {text_table_pages}")
    if ocr_table_pages:
        log(f"  OCR comparative-table pages (read from the OCR layer, vision only where it fails): {ocr_table_pages}")

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

    def log_failed(kind, p, err):
        """A failed call is still paid for: log what it cost (offline and
        missing-key errors carry no usage -- no call was made)."""
        if getattr(err, "usage", None):
            usage_log.append((kind, p, dict(err.usage, outcome="failed", error=str(err))))
    uncached = []
    for p in vision_pages:
        entry = (None if live else cache.load(p)) or {"meta": {"pdf_sha256": pdf_sha, "page": p}}
        if entry.get("classify") is None and entry.get("transcription") is None:
            try:
                result, usage = classify_page(get_client(), model, doc_pdf[p - 1], use_fallbacks)
            except VisionError as e:
                log_failed("classify", p, e)
                if offline:
                    uncached.append(p)
                else:
                    log(f"  p{p}: not classified ({e})")
                continue
            entry["classify"] = result
            entry["meta"].update(model=usage["model"], prompt_version=PROMPT_VERSION,
                                 source="claude_api", classified_at=_now())
            usage_log.append(("classify", p, dict(usage, outcome="ok")))
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
            log_failed("transcribe", p, e)
            log(f"  p{p}: not transcribed ({e})")
            return False
        entry["transcription"] = result
        entry["meta"].update(model=usage["model"], prompt_version=PROMPT_VERSION, source="claude_api",
                             rotation=used_rot, dpi=used_dpi, transcribed_at=_now())
        usage_log.append(("transcribe", p, dict(usage, outcome="ok")))
        cache.save(p, entry)
        return True

    done = [p for p in table_pages if ensure_transcribed(p)]

    def vision_reread(p, table_headers):
        """Re-read an OCR or text-layer page with a vision call (cached like any other: a transcription already
        on file, from an earlier vision run of the page, costs no new page image).
        -> a new entry, or a string saying why the free reading is kept."""
        entry = None if live else cache.load(p)
        if entry and entry["meta"].get("fallback_failed"):
            return f"vision re-read failed on an earlier run, not retried ({entry['meta']['fallback_failed']})"
        cached = entry and entry.get("transcription") and (entry["meta"].get("fallback_from") == "ocr_text"
                                                           or entry["meta"].get("transcribed_at"))
        if not cached and not offline and not os.environ.get("ANTHROPIC_API_KEY"):
            return "vision unavailable (no ANTHROPIC_API_KEY)"
        if not cached:
            page = doc_pdf[p - 1]
            # the text's direction is in the page's unrotated space; the
            # rendering already applies the page's own /Rotate
            direction = text_tables.page_geometry(page)["direction"]
            rot = ({(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}.get(direction, 0) - page.rotation) % 360
            try:
                result, usage, used_rot, used_dpi = transcribe_page(get_client(), model, page,
                                                                    rot, dpi, use_fallbacks)
            except VisionError as e:
                log_failed("ocr_fallback", p, e)
                if getattr(e, "usage", None):
                    cache.save(p, {"meta": {"pdf_sha256": pdf_sha, "page": p, "fallback_from": "ocr_text",
                                            "fallback_failed": str(e), "failed_at": _now()},
                                   "transcription": None})
                return f"vision unavailable ({e})"
            entry = {"meta": {"pdf_sha256": pdf_sha, "page": p, "model": usage["model"],
                              "prompt_version": PROMPT_VERSION, "source": "claude_api", "rotation": used_rot,
                              "dpi": used_dpi, "transcribed_at": _now(), "fallback_from": "ocr_text"},
                     "transcription": result}
            usage_log.append(("ocr_fallback", p, dict(usage, outcome="ok")))
            cache.save(p, entry)
        tr = entry["transcription"]
        got = [ocr_tables.header_key(h) for h in tr.get("column_headers", [])]
        want = [ocr_tables.header_key(h) for h in table_headers]
        if got != want:
            return f"vision read headers {tr.get('column_headers')} != table's {table_headers}"
        tr["column_headers"] = list(table_headers)
        return {"meta": dict(entry["meta"]), "transcription": tr}

    # 3b. Text-native table pages: every one is read (no cost); the title
    #     filter is applied to the hierarchy below, so rollups keep complete
    #     children.
    text_trs = text_tables.extract_table(doc_pdf, text_table_pages) if text_table_pages else {}
    for p, tr in text_trs.items():
        entries[p] = {"meta": {"pdf_sha256": pdf_sha, "page": p, "source": "text_layer",
                               "column_headers_from_page": tr["column_headers_from_page"]},
                      "transcription": tr}
        done.append(p)
    all_table_pages = sorted(set(all_table_pages) | set(text_table_pages))

    # 3c. OCR table pages: read from the OCR layer; the arithmetic gate below
    #     decides which of them need a vision re-read.
    ocr_issues = {}
    if ocr_table_pages:
        ocr_trs, ocr_issues = ocr_tables.extract_table(doc_pdf, ocr_table_pages)
        for p, tr in ocr_trs.items():
            entries[p] = {"meta": {"pdf_sha256": pdf_sha, "page": p, "source": "ocr_text"}, "transcription": tr}
            done.append(p)
        all_table_pages = sorted(set(all_table_pages) | set(ocr_table_pages))

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

    # 4-5. Rows -> hierarchy -> observations -> validation.
    page_texts = [r["text"] for r in routes]
    source_document = source_document_fields(package_id, pdf_sha, doc, manifest_entry, page_texts)
    if division:
        source_document["subcommittee"] = subcommittee_of(division[1]) or source_document["subcommittee"]

    def assemble():
        page_meta, rows, units_by_page, headers, table_title = {}, [], {}, None, ""
        for p in sorted(set(done)):
            entry = entries[p]
            tr = entry["transcription"]
            src = entry["meta"].get("source", "claude_api")
            method = {"manual_transcription": "human-entered", "text_layer": "text-extracted",
                      "ocr_text": "text-extracted"}.get(src, "AI-extracted")
            page_meta[p] = {"extraction_method": method,
                            "source": src, "model": entry["meta"].get("model"),
                            "units_declared": tr.get("units_declared", ""),
                            "units_parsed": parse_units(tr.get("units_declared", "")),
                            "column_headers": tr.get("column_headers", []),
                            "legibility_notes": tr.get("legibility_notes", "")}
            for k in ("column_headers_from_page", "fallback_from", "fallback_reason"):
                v = tr.get(k) if k == "column_headers_from_page" else entry["meta"].get(k)
                if v is not None:
                    page_meta[p][k] = v
            units_by_page[p] = tr.get("units_declared", "")
            if headers is not None and tr["column_headers"] != headers:
                raise SystemExit(f"p{p} column headers {tr['column_headers']} differ from {headers} "
                                 f"on an earlier page of the same table")
            headers = headers or tr["column_headers"]
            table_title = table_title or tr.get("table_title", "")
            for r in tr["rows"]:
                vals = list(r.get("values", []))[:len(tr["column_headers"])]
                vals += [""] * (len(tr["column_headers"]) - len(vals))
                rows.append((p, r, [parse_cell(v) for v in vals]))

        if not rows:
            if not vision_pages:
                raise SystemExit(f"{package_id}: no comparative statement found -- all {len(routes)} pages have a "
                                 "text layer and none matched the table's header pattern and column layout")
            raise SystemExit("nothing transcribed -- set ANTHROPIC_API_KEY or point --cache-dir at recorded pages")

        first_table_page = all_table_pages[0] if all_table_pages else None
        starts_with_title = bool(rows) and done and min(done) == first_table_page and \
            classify_row(rows[0][1], rows[0][2]) == "title_heading"
        Node._seq = 0
        nodes = build_hierarchy(rows, starts_with_title)

        m = re.search(r"BILL\s+FOR\s+(?:FISCAL\s+YEAR\s+)?(\d{4})|\bACT\s*,\s*(\d{4})", table_title, re.I)
        bill_fy = int(m.group(1) or m.group(2)) if m else (manifest_entry or {}).get("fiscal_year")
        cols = classify_columns(headers, bill_fy, doc["stage"])
        for c in cols:
            # never guessed: H.Rept. 119-271 heads FY2025 (a full-year continuing
            # resolution) "FY 2025 Estimate" -- neither Enacted nor a request
            if c["kind"] == "value" and (c["stage"] is None or c["fiscal_year"] is None):
                log(f"  WARNING: value column {c['header']!r} has no stage/fiscal year the header states "
                    f"(stage {c['stage']!r}, FY {c['fiscal_year']!r}) -- its figures are extracted, not assigned")
            if c["kind"] == "delta" and (c.get("minuend_index") is None or c.get("subtrahend_index") is None):
                log(f"  WARNING: difference column {c['header']!r} doesn't name two value columns -- not checked")

        declared_units = {parse_units(u) for u in units_by_page.values()}
        unit = next(iter(declared_units)) if len(declared_units) == 1 else None
        if unit is None:
            raise SystemExit(f"table pages disagree on units (or declare none): {units_by_page}")

        selected = [n for n in nodes if title_filter_ok(n, target_key)]
        observations = build_observations(selected, cols, unit, page_meta, doc, table_title)
        # Cells printed as dot leaders: blank, so no observation -- but recorded,
        # so "printed blank" stays distinguishable from "no such row".
        printed_blanks = [{"account_path": " / ".join(n.path), "column_header": c["header"], "source_page": n.page}
                          for n in selected for c in cols
                          if c["kind"] == "value" and c["index"] < len(n.cells) and n.cells[c["index"]].get("leader")]
        account_rows = []
        # Every path: match each row to a canonical account (accounts.py)
        # -- by edit distance, since OCR labels are noisy ("Sci e nee"),
        # and text/vision labels go through the same rule. An observation
        # needs canonical_account_id to be stored at all; the match feeds
        # the account_identity check.
        matches = accounts.match_nodes(selected)
        for o in observations:
            mt = matches.get(o["node_id"])
            if mt is not None:
                o.update(canonical_account_id=mt["canonical_account_id"], canonical_name=mt["canonical_name"],
                         account_component=mt["component"] or o.get("account_component"), account_match=mt["match"],
                         account_match_distance=mt["distance"], account_match_via=mt.get("via"),
                         account_matched_name=mt.get("matched_name"))
        for n in selected:
            mt = matches.get(n.id)
            if mt and mt["canonical_account_id"]:
                account_rows.append({"account_path": " / ".join(n.path), "source_page": n.page, "title": n.title,
                                     "canonical_account_id": mt["canonical_account_id"],
                                     "account_component": mt["component"] or scope_component(n.scope, n.scope_role),
                                     "blank_columns": [c["header"] for c in cols if c["kind"] == "value"
                                                       and (c["index"] >= len(n.cells) or n.cells[c["index"]]["kind"] == "blank")]})
        records, summary = validate_approps.validate(selected, cols, observations, page_meta, unit,
                                                     source_document=source_document)
        return {"page_meta": page_meta, "table_title": table_title, "bill_fy": bill_fy, "cols": cols,
                "unit": unit, "selected": selected, "observations": observations,
                "printed_blanks": printed_blanks, "account_rows": account_rows,
                "records": records, "summary": summary}

    built = assemble()

    # 5b. OCR gate: a page with a problem the free path can't resolve (an
    #     unparseable cell, a header with the wrong column count) or whose
    #     numbers fail the table's own arithmetic is re-read once by vision.
    ocr_fallback = {}
    gated = set(ocr_table_pages) | set(text_trs)       # text-read pages: vision only where the arithmetic fails
    if gated:
        in_selection = {n.page for n in built["selected"]}
        failing = {p: "; ".join(iss) for p, iss in ocr_issues.items() if iss and p in in_selection}
        # Only failures a page's own reading can cause: a row on it that fails
        # its own check (a delta, or a stated total that doesn't add up). A
        # page merely holding children of a failing rollup isn't re-read --
        # vision can't fix a hierarchy problem, and it would pay to find that out.
        page_of = {o["observation_id"]: int(o["source_page"]) for o in built["observations"]}
        for rec in built["records"]:
            p = page_of.get(rec["observation_id"])
            if rec["result"] == "fail" and rec["rule_applied"] in ("structural", "table_total") \
                    and p in gated and p not in failing:
                failing[p] = "arithmetic check failed on this page"
        replaced = False
        for p, reason in sorted(failing.items()):
            got = vision_reread(p, entries[p]["transcription"]["column_headers"])
            if isinstance(got, str):
                ocr_fallback[p] = {"reason": reason, "result": f"kept OCR: {got}"}
                continue
            source = "text_layer" if p in text_trs else "ocr_text"
            entries[p] = got
            entries[p]["meta"].update(fallback_from=source, fallback_reason=reason)
            ocr_fallback[p] = {"reason": reason, "result": "re-read by vision"}
            replaced = True
        if replaced:
            built = assemble()
        for p, f in ocr_fallback.items():
            log(f"  p{p}: {f['reason'][:80]} -> {f['result']}")

    page_meta, table_title, bill_fy, cols = built["page_meta"], built["table_title"], built["bill_fy"], built["cols"]
    unit, observations, printed_blanks = built["unit"], built["observations"], built["printed_blanks"]
    records, summary = built["records"], built["summary"]
    sign_check = text_tables.sign_glyph_check(text_trs, cols) if text_trs else {}
    source_document["fiscal_year"] = bill_fy

    result = {
        "source_document": source_document,
        "extraction": {
            "extracted_at": _now(),
            "model": model,
            "mode": "live" if live else ("offline" if offline else "cached"),
            "prompt_version": PROMPT_VERSION,
            "title_filter": title,
            "division": ({"letter": division[0], "name": division[1], "pages": [division[2], division[3]]}
                         if division else None),
            "table_title": table_title,
            "amount_unit_declared": unit,
            "columns": cols,
            "pages_transcribed": sorted(set(done)),
            "page_sources": {str(p): {k: v for k, v in m.items()} for p, m in page_meta.items()},
            "vision_calls_this_run": [{"pass": k, "page": p, **u} for k, p, u in usage_log],
            "vision_spend": vision_spend(usage_log),
            "page_images_this_run": {"used": _BUDGET.used, "cap": _BUDGET.cap, "pages": list(_BUDGET.pages)},
            "text_table_pages": text_table_pages,
            "ocr_table_pages": ocr_table_pages,
            "ocr_fallback": {str(p): f for p, f in ocr_fallback.items()},
            "sign_glyph_check": sign_check,
        },
        "page_routing": [{k: v for k, v in r.items() if k != "text"} for r in routes],
        "observations": observations,
        "printed_blanks": printed_blanks,
        "account_rows": built["account_rows"],
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


# List prices per million tokens (input, output), for the spend estimate
# only; a model not listed is counted in tokens with no dollar figure.
PRICE_PER_MTOK = {"claude-opus-5": (5.00, 25.00), "claude-opus-5-5": (4.00, 20.00),
                  "claude-sonnet-5": (2.00, 10.00), "claude-opus-4-8": (5.00, 25.00)}


def vision_spend(usage_log):
    """Every paid call this run -- succeeded or failed -- in calls, tokens and
    an estimated cost."""
    out = {"calls": 0, "failed_calls": 0, "input_tokens": 0, "output_tokens": 0,
           "estimated_usd": 0.0, "unpriced_calls": 0, "by_outcome": {}}
    for _, _, u in usage_log:
        attempts = u.get("attempts", 1)
        out["calls"] += attempts
        out["failed_calls"] += attempts if u.get("outcome") == "failed" else 0
        out["input_tokens"] += u.get("input_tokens", 0)
        out["output_tokens"] += u.get("output_tokens", 0)
        price = PRICE_PER_MTOK.get(re.sub(r"-\d{8}$", "", u.get("model") or ""))
        cost = (u.get("input_tokens", 0) * price[0] + u.get("output_tokens", 0) * price[1]) / 1e6 if price else 0.0
        if not price:
            out["unpriced_calls"] += attempts
        out["estimated_usd"] += cost
        b = out["by_outcome"].setdefault(u.get("outcome", "ok"), {"calls": 0, "estimated_usd": 0.0})
        b["calls"] += attempts
        b["estimated_usd"] += cost
    out["estimated_usd"] = round(out["estimated_usd"], 4)
    for b in out["by_outcome"].values():
        b["estimated_usd"] = round(b["estimated_usd"], 4)
    return out


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def ground_truth_checks(gt):
    """A ground-truth file checks one column ({"column_header", "expected"})
    or several ({"checks": [{"column_header", "series", "expected"}, ...]})."""
    if "checks" in gt:
        return gt["checks"]
    return [{"column_header": gt["column_header"], "series": gt["column_header"], "expected": gt["expected"]}]


def missing_ground_truth(gt_path):
    """Names of expected figures that haven't been filled in yet."""
    gt = json.loads(Path(gt_path).read_text())
    return [f"{c['series']}: {i['name']}" for c in ground_truth_checks(gt) for i in c["expected"]
            if i.get("amount_dollars") is None]


BLANK, NOT_PRINTED = "blank", "not printed"


def compare_ground_truth(result, gt_path):
    """
    -> (ok, [(name, want, got, match)]). got is the extracted dollar amount,
    or BLANK when the cell is printed as dot leaders, or NOT_PRINTED when the
    ground truth says the document has no such row (account_path null), or
    None when the row wasn't found. The pilot records "no funding" as 0 and
    has no blank state, so want == 0 matches BLANK and NOT_PRINTED -- callers
    that need to tell those apart from exact numeric matches look at got.
    """
    gt = json.loads(Path(gt_path).read_text())
    if gt.get("title"):
        # a ground-truth file covers one title: don't let a same-named row in
        # another title (a supplemental act's "Total, NASA") compete for a match
        tk = title_key(gt["title"])
        result = dict(result, observations=[o for o in result["observations"] if title_key(o.get("title") or "") == tk],
                      account_rows=[r for r in result.get("account_rows", []) if title_key(r.get("title") or "") == tk])
    by_key = {(o["account_path"], o["column_header"]): o for o in result["observations"]}
    blanks = {(b["account_path"], b["column_header"]) for b in result.get("printed_blanks", [])}
    # OCR documents: keyed by canonical account (+ component), since labels are noisy
    by_account, account_blanks = {}, set()
    for o in result["observations"]:
        if o.get("canonical_account_id") and o.get("account_match") in ("exact", "ocr_corrected", "inherited"):
            by_account.setdefault((o["canonical_account_id"], o.get("account_component"), o["column_header"]), []).append(o)
    for r in result.get("account_rows", []):
        for h in r["blank_columns"]:
            account_blanks.add((r["canonical_account_id"], r["account_component"], h))
    checks = ground_truth_checks(gt)
    rows, ok = [], True
    for check in checks:
        for item in check["expected"]:
            col = check["column_header"]
            if "title_total" in item:
                hits = [o for o in result["observations"] if o["column_header"] == col and o["row_kind"] == "total"
                        and title_key(re.sub(r"^total\s*[,.]?\s*", "", o["account_name_as_written"], flags=re.I))
                        == title_key(item["title_total"])]
                got = hits[0]["amount"] if len(hits) == 1 else None
            elif "canonical_account_id" in item:
                k = (item["canonical_account_id"], item.get("component"), col)
                hits = by_account.get(k, [])
                if len(hits) == 1:
                    got = hits[0]["amount"]
                elif hits:
                    got = None                    # two rows claim one account: not a match
                elif item.get("printed") is False:
                    got = NOT_PRINTED
                elif k in account_blanks:
                    got = BLANK
                else:
                    got = None
            else:
                key = (item["account_path"], col)
                o = by_key.get(key)
                if o is not None:
                    got = o["amount"]
                elif item["account_path"] is None:
                    got = NOT_PRINTED
                elif key in blanks:
                    got = BLANK
                else:
                    got = None
            want = item["amount_dollars"]
            match = want is not None and (got == want or (want == 0 and got in (BLANK, NOT_PRINTED)))
            ok &= match
            name = item["name"] if len(checks) == 1 else f"[{check['series']}] {item['name']}"
            rows.append((name, item["amount_dollars"], got, match))
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
    calls = ex["vision_calls_this_run"]
    print(f"\nVision calls this run ({ex['mode']}): {len(calls)}")
    for c in calls:
        print(f"  {c['pass']:12s} p{c['page']:<4d} in={c['input_tokens']:>7,} out={c['output_tokens']:>7,} "
              f"{c.get('seconds', 0):>6.1f}s  {c['model']}"
              + (f"  FAILED ({c.get('error', '')[:60]})" if c.get("outcome") == "failed" else ""))
    spend = ex.get("vision_spend")
    if spend and spend["calls"]:
        print(f"  spend this run: {spend['calls']} API calls ({spend['failed_calls']} failed), "
              f"in={spend['input_tokens']:,} out={spend['output_tokens']:,}, ~${spend['estimated_usd']:.2f}"
              + (f" + {spend['unpriced_calls']} calls on an unpriced model" if spend["unpriced_calls"] else ""))
    if calls:
        for kind in ("classify", "transcribe"):
            cs = [c for c in calls if c["pass"] == kind]
            if cs:
                print(f"  {kind:10s} total: {len(cs)} calls, in={sum(c['input_tokens'] for c in cs):,} "
                      f"out={sum(c['output_tokens'] for c in cs):,} {sum(c.get('seconds', 0) for c in cs):.1f}s")
    if gt_rows is not None:
        print("\nGround truth:")
        for name, want, got, match in gt_rows:
            g = f"{got:,}" if isinstance(got, int) else (got or "MISSING")
            w = f"{want:,}" if want is not None else "(not supplied)"
            tag = "OK  " if match else ("----" if want is None else "FAIL")
            print(f"  {tag} {name:65s} want {w:>16}  got {g:>16}")


def main():
    ap = argparse.ArgumentParser(description="Extract appropriations observations from a stored PDF.")
    ap.add_argument("pdf", help="PDF from document_store/, e.g. document_store/CRPT-119hrpt652.pdf")
    ap.add_argument("--title", help='Only this bill title, found by its heading, e.g. "TITLE III"')
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Use cached vision results only; never call the API")
    mode.add_argument("--live", action="store_true",
                      help="Ignore cached vision results and call the API for every image-only page")
    ap.add_argument("--no-fallbacks", action="store_true", help="Don't send the server-side refusal fallback beta")
    ap.add_argument("--subcommittee", help="In a multi-division document, whose division to read, e.g. LHHS "
                                           "(defaults to the manifest's subcommittee)")
    ap.add_argument("--ground-truth", help="JSON of expected dollar figures to diff against")
    ap.add_argument("--single-division", action="store_true",
                    help="a one-subcommittee document whose text quotes other acts' DIVISION headings: read it whole")
    ap.add_argument("--max-page-images", type=int, default=None,
                    help=f"hard cap on page images sent to vision this run (default {MAX_PAGE_IMAGES}, "
                         "APPROPS_MAX_PAGE_IMAGES); reaching it stops the run")
    args = ap.parse_args()

    result = run(args.pdf, title=args.title, model=args.model, cache_dir=args.cache_dir,
                 offline=args.offline, dpi=args.dpi, out_dir=args.out_dir,
                 use_fallbacks=not args.no_fallbacks, live=args.live, subcommittee=args.subcommittee,
                 single_division=args.single_division, max_page_images=args.max_page_images)
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
