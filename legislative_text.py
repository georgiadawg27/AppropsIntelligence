"""
Bill and law text: which documents the House, Senate and Enacted figures must be checked against, and the
parser that turns each document's text into appropriation paragraphs.

    data/legislative_text/<document_id>.json   one per document (scripts/fetch_legislative_text.py writes them)
    data/legislative_text/exemptions.json      documents with no usable text, each with its written reason

A document is required for every Bill Report Reference row at a bill or law stage (House Reported, Senate
Reported, Enacted; House/Senate Passed if they appear) and every Source Document of type bill or public_law
at one of those stages. Its document_id is the Source Document's when one holds the same package (or URL),
else SRC-<govinfo package> (a Source Document row with that id is added the first time its text is fetched).

The parser (parse_* below) is deterministic: the same raw download always gives the same paragraphs. Each
text file records PARSER_VERSION -- the sha256 of this module's parser section -- so a parser change shows up
as a version mismatch in CI (scripts/check_legislative_text.py) until the files are re-parsed and committed,
i.e. as a diff, never as silent drift.

No network here: fetching is scripts/fetch_legislative_text.py's job.
"""

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT_DIR = ROOT / "data" / "legislative_text"
EXEMPTIONS = TEXT_DIR / "exemptions.json"
STAGED = ROOT / "data" / "staged.json"

BILL_STAGES = ("House Reported", "Senate Reported", "Enacted", "House Passed", "Senate Passed")
TEXT_DOC_TYPES = ("bill", "public_law")
# check type by stage: a bill's text for House and Senate, the law's for Enacted
RULE_BY_STAGE = {"House Reported": "bill_text", "Senate Reported": "bill_text", "House Passed": "bill_text",
                 "Senate Passed": "bill_text", "Enacted": "law_text"}

PACKAGE = re.compile(r"\b((?:BILLS|PLAW)-\d{3}[a-z]+\d+[a-z]*)\b")
VERSION_NAMES = {"ih": "introduced in House", "is": "introduced in Senate", "rh": "reported in House",
                 "rs": "reported in Senate", "pcs": "placed on calendar (Senate)", "eh": "engrossed in House",
                 "es": "engrossed in Senate", "enr": "enrolled", "publ": "public law"}


def package_of(url):
    """'.../BILLS-119hr5342rh.pdf' -> 'BILLS-119hr5342rh'; None for a committee-site file."""
    m = PACKAGE.search(url or "")
    return m.group(1) if m else None


def version_code(package):
    """BILLS-119hr5342rh -> 'rh'; PLAW-117publ328 -> 'publ'; None (a committee draft) -> 'committee draft'."""
    if not package:
        return "committee draft"
    if package.startswith("PLAW-"):
        return "publ"
    return re.match(r"BILLS-\d{3}[a-z]+?\d+([a-z]+)$", package).group(1)


def enrolled_package(ref):
    """The enrolled bill behind an Enacted reference: 'H.R. 6938' + PLAW-119publ74 -> BILLS-119hr6938enr."""
    m = re.match(r"(H\.R\.|S\.|H\.J\.Res\.|S\.J\.Res\.)\s*(\d+)", ref.get("vehicle_bill_id") or "")
    c = re.search(r"(?:PLAW-|/)(1\d\d)(?:publ|/plaws)", ref.get("bill_url") or "")
    if not (m and c):
        return None
    kind = {"H.R.": "hr", "S.": "s", "H.J.Res.": "hjres", "S.J.Res.": "sjres"}[m.group(1)]
    return f"BILLS-{c.group(1)}{kind}{m.group(2)}enr"


def _blank(v):
    return (v or "").strip() in ("", "N/A")


def required_documents(data):
    """{document_id: requirement} for every bill or law stage we hold, plus [(reference_id, why)] for stages with
    no document at all (to be exempted). A requirement: document_id, url, package, version, kind, stage,
    fiscal_year, subcommittees, divisions ({subcommittee: division}), references, enrolled (fallback package),
    source_doc (True when a Source Document row already holds it)."""
    docs = data["source_docs"]
    by_pkg = {package_of(d["url_or_identifier"]): d for d in docs if package_of(d["url_or_identifier"])}
    by_url = {d["url_or_identifier"]: d for d in docs}
    out, nodoc = {}, []

    def add(doc_id, url, pkg, kind, stage, fy, sc=None, division=None, ref=None, enrolled=None, source_doc=False):
        r = out.setdefault(doc_id, {"document_id": doc_id, "url": url, "package": pkg, "version": version_code(pkg),
                                    "kind": kind, "stage": stage, "fiscal_year": int(fy), "subcommittees": [],
                                    "divisions": {}, "references": [], "enrolled": enrolled,
                                    "source_doc": source_doc})
        r["source_doc"] = r["source_doc"] or source_doc
        if sc and sc not in r["subcommittees"]:
            r["subcommittees"].append(sc)
        if sc and division:
            r["divisions"][sc] = division
        if ref and ref not in r["references"]:
            r["references"].append(ref)
        if enrolled and not r["enrolled"]:
            r["enrolled"] = enrolled

    for ref in data["bill_report_refs"]:
        if ref["stage"] not in BILL_STAGES:
            continue
        url = ref.get("bill_url") or ""
        if _blank(url):
            nodoc.append((ref["reference_id"], f"{ref['subcommittee']} FY{ref['fiscal_year']} {ref['stage']}: "
                                               "the reference names no bill or law"))
            continue
        pkg = package_of(url)
        d = by_pkg.get(pkg) if pkg else by_url.get(url)
        if d:
            doc_id = d["document_id"]
        elif pkg:
            doc_id = "SRC-" + pkg.upper()
        else:
            stage = {"House Reported": "HOUSE", "Senate Reported": "SENATE", "Enacted": "ENACTED"}.get(
                ref["stage"], ref["stage"].upper().replace(" ", "-"))
            doc_id = f"SRC-BILL-{ref['subcommittee']}-FY{ref['fiscal_year']}-{stage}"
        kind = "public_law" if (pkg or "").startswith("PLAW-") or ref["stage"] == "Enacted" else "bill"
        add(doc_id, d["url_or_identifier"] if d else url, pkg, kind, ref["stage"], ref["fiscal_year"],
            ref["subcommittee"], (ref.get("division") or "").strip() or None, ref["reference_id"],
            enrolled_package(ref) if ref["stage"] == "Enacted" else None, source_doc=bool(d))
    for d in docs:
        if d["document_type"] in TEXT_DOC_TYPES and d["stage"] in BILL_STAGES and d["document_id"] not in out:
            pkg = package_of(d["url_or_identifier"])
            add(d["document_id"], d["url_or_identifier"], pkg, d["document_type"], d["stage"], d["fiscal_year"],
                source_doc=True)
    return out, nodoc


def load_exemptions():
    if not EXEMPTIONS.exists():
        return {}
    return {e["id"]: e for e in json.loads(EXEMPTIONS.read_text())["exemptions"]}


def text_path(document_id):
    return TEXT_DIR / f"{document_id}.json"


def load_text(document_id):
    p = text_path(document_id)
    return json.loads(p.read_text()) if p.exists() else None


def dump(obj):
    """The one serialization of a text file (stable: a re-parse that changes nothing changes no byte)."""
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------------------------------------
# PARSER (everything from here to END PARSER is hashed into PARSER_VERSION)
# ------------------------------------------------------------------------------------------------------------

AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?!\s*(?:million|billion))")
PHRASE_BEFORE, PHRASE_AFTER = 160, 100


def squash(s):
    return " ".join((s or "").split())


def amounts_in(text, block=1):
    """Every dollar amount in a paragraph's text, in order, with the phrase around it, and whether it sits in a
    proviso ("Provided, That ...") rather than in the appropriating clause."""
    out = []
    provided = re.search(r"\bProvided\b", text)
    for m in AMOUNT.finditer(text):
        before = text[max(0, m.start() - PHRASE_BEFORE):m.start()]
        after = text[m.end():m.end() + PHRASE_AFTER]
        out.append({"amount": int(m.group(1).replace(",", "")), "block": block,
                    "proviso": bool(provided and m.start() > provided.start()),
                    "phrase": squash(before) + " [" + m.group(0) + "] " + squash(after)})
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


SKIP_TEXT = {"page", "sidenote", "footnote", "note", "notes"}


def _text(el):
    """An element's text, without page markers, side notes or footnotes."""
    parts = [el.text or ""]
    for c in el:
        if _local(c.tag) not in SKIP_TEXT:
            parts.append(_text(c))
        parts.append(c.tail or "")
    return "".join(parts)


def _heading(s):
    return squash(html.unescape(s)).upper()


def _keep_division(enum, divisions):
    return not divisions or enum in divisions


ADDITIONAL = re.compile(r"\b(?:an additional|additional amount)\b", re.I)
QUOTE_TAGS = ("quote", "quotedText", "quotedContent")
GP = "GENERAL PROVISION"


def provision_para(el, enum):
    """A general provision that appropriates an additional amount under another heading ("In addition to amounts
    otherwise appropriated by this Act under the heading 'Office of the Secretary--General Departmental
    Management' there are hereby appropriated an additional $350,000,000"): kept as a paragraph headed
    GENERAL PROVISION > SEC. n, the quoted headings in its heading notes; None for any other section."""
    text = squash(html.unescape(_text(el)))
    quotes = [squash(html.unescape(_text(q))) for q in el.iter() if _local(q.tag) in QUOTE_TAGS]
    if not quotes or not ADDITIONAL.search(text) or not re.search(r"\bappropriated\b", text, re.I):
        return None
    amounts = amounts_in(text, 1)
    if not amounts:
        return None
    sec = f"SEC. {enum}".rstrip(".")
    return {"heading_path": [GP, sec], "account_heading": sec, "heading_notes": [f"under the heading: {q}" for q in quotes],
            "blocks": 1, "amounts": amounts}


def _content_blocks(content):
    """A USLM <content>'s blocks: each <p> starts one (a separate appropriating paragraph); anything else inside it
    (an enumerated list, a continuation after the list) belongs to the block it follows, so the paragraph ends only
    at the next heading or section."""
    if content.find("{*}p") is None:
        return [squash(html.unescape(_text(content)))]
    blocks = [squash(html.unescape(content.text or ""))] if squash(content.text or "") else []
    for c in content:
        t = squash(html.unescape((_text(c) if _local(c.tag) not in SKIP_TEXT else "") + (c.tail or "")))
        if not t and _local(c.tag) != "p":
            continue
        if _local(c.tag) == "p" or not blocks:
            blocks.append(t)
        else:
            blocks[-1] = squash(blocks[-1] + " " + t)
    return blocks


def parse_uslm(raw, divisions=None):
    """USLM XML (govinfo PLAW and newer BILLS): nested <appropriations level=...> with <heading>, <subheading>,
    <content>; <page> markers give the Statutes at Large page."""
    root = ET.fromstring(raw)
    paras, state = [], {"page": None, "division": "", "division_heading": "", "title": ""}

    def walk(el, path, in_div):
        tag = _local(el.tag)
        if tag == "page":
            state["page"] = squash(_text(el) or el.text or "")
            return
        if tag == "division":
            num = el.find("{*}num")
            enum = (num.get("value") if num is not None else "") or ""
            state["division"] = enum
            hd = el.find("{*}heading")
            state["division_heading"] = _heading(_text(hd)) if hd is not None else ""
            state["title"] = ""
            keep = _keep_division(enum, divisions)
            for c in el:
                walk(c, [], keep)
            return
        if tag == "section" and not any(_local(x.tag) == "appropriations" for x in el.iter()):
            num = el.find("{*}num")
            enum = ((num.get("value") if num is not None else "") or squash(_text(num) if num is not None else "")
                    ).replace("SEC.", "").strip()
            start_page = state["page"]
            for pg in el.iter():
                if _local(pg.tag) == "page":
                    state["page"] = squash(pg.text or "")
            p = provision_para(el, enum) if in_div else None
            if p:
                p.update(division=state["division"], title=state["title"],
                         location={"stat_page": state["page"] if start_page in (None, state["page"])
                                   else f"{start_page} - {state['page']}"})
                paras.append(p)
            return
        if tag == "title":
            num, hd = el.find("{*}num"), el.find("{*}heading")
            state["title"] = squash(" ".join(_heading(_text(x)) for x in (num, hd) if x is not None))
            for c in el:
                walk(c, [], in_div)
            return
        if tag == "appropriations":
            hd = el.find("{*}heading")
            here = [_heading(_text(hd))] if hd is not None and squash(_text(hd)) else []
            notes = []
            for sh in el.findall("{*}subheading"):
                s = _heading(_text(sh))
                (notes if s.startswith("(") else here).append(s)
            # the paragraph: a <content> (its <p>s are separate appropriating paragraphs), or a <chapeau> with
            # <paragraph>s and a <continuation> (one sentence)
            body = [c for c in el if _local(c.tag) not in ("heading", "subheading", "appropriations", "num", "page")]
            full = path + here
            start_page = state["page"]
            for c in body:
                for pg in c.iter():
                    if _local(pg.tag) == "page":
                        state["page"] = squash(pg.text or "")
            for c in el:
                if _local(c.tag) == "page":
                    state["page"] = squash(c.text or "")
            if body and in_div:
                if len(body) == 1 and _local(body[0].tag) == "content":
                    blocks = _content_blocks(body[0])
                else:
                    blocks = [squash(html.unescape(" ".join(_text(c) for c in body)))]
                blocks = [b for b in blocks if b]
                amounts = []
                for i, b in enumerate(blocks, 1):
                    amounts += amounts_in(b, i)
                loc = {"stat_page": state["page"] if start_page in (None, state["page"])
                       else f"{start_page} - {state['page']}"}
                if blocks:
                    paras.append({"division": state["division"], "title": state["title"], "heading_path": full,
                                  "account_heading": full[-1] if full else "", "heading_notes": notes,
                                  "blocks": len(blocks), "amounts": amounts, "location": loc})
            for c in el:
                if _local(c.tag) == "appropriations":
                    walk(c, full, in_div)
            return
        for c in el:
            walk(c, path, in_div)

    walk(root, [], not divisions)
    return paras


ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _xml_safe(raw):
    """Bill DTD XML may use HTML named entities its DTD defines; resolve them so expat can read the file."""
    s = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    s = re.sub(r"<!DOCTYPE[^>]*>", "", s, count=1)
    return ENTITY.sub(lambda m: m.group(0) if m.group(1) in XML_ENTITIES else html.unescape(m.group(0)), s)


def _bill_blocks(el):
    """A bill-XML heading's paragraph text, as blocks: each <text> starts one (a separate appropriating paragraph);
    everything else inside the heading -- an enumerated list's <paragraph>s, a <continuation-text> after the list, a
    quoted block -- belongs to the block it follows. The paragraph ends only at the next heading or section."""
    blocks = []
    for c in el:
        tag = _local(c.tag)
        if tag in ("header", "enum") or tag.startswith("appropriations-") or tag == "section" or tag in SKIP_TEXT:
            continue
        t = squash(html.unescape(_text(c)))
        if tag == "text" or not blocks:
            blocks.append(t)
        else:
            blocks[-1] = squash(blocks[-1] + " " + t)
    return [b for b in blocks if b]


def parse_bill_xml(raw, divisions=None):
    """Bill DTD XML (govinfo BILLS): flat <appropriations-major|intermediate|small> headings, each followed by
    its <text> paragraphs, inside <division> / <title>. No page numbers: the location is the division, title and
    the heading's position."""
    root = ET.fromstring(_xml_safe(raw))
    paras, state = [], {"division": "", "title": "", "major": "", "inter": "", "keep": not divisions, "n": 0}
    LEVEL = {"appropriations-major": 0, "appropriations-intermediate": 1, "appropriations-small": 2}

    def new_para(path, hdr, texts, notes):
        state["n"] += 1
        amounts = []
        for i, t in enumerate(texts, 1):
            amounts += amounts_in(t, i)
        p = {"division": state["division"], "title": state["title"], "heading_path": path, "account_heading": hdr,
             "heading_notes": notes, "blocks": len(texts), "amounts": amounts,
             "location": {"position": f"{'div. ' + state['division'] + ', ' if state['division'] else ''}"
                                      f"{state['title'] or 'no title'}, heading {state['n']}"}}
        paras.append(p)
        return p

    def walk(el):
        tag = _local(el.tag)
        if tag == "division":
            e = el.find("enum")
            enum = squash(_text(e)).rstrip(".").replace("DIVISION", "").strip() if e is not None else ""
            state.update(division=enum, title="", major="", inter="", keep=_keep_division(enum, divisions), last=None)
        elif tag == "title" and (el.find("header") is not None or el.find("enum") is not None):
            e, h = el.find("enum"), el.find("header")
            enum = _heading(_text(e)) if e is not None else ""
            if re.fullmatch(r"[IVXL]+", enum):
                enum = "TITLE " + enum
            state.update(title=squash(" ".join(x for x in (enum, _heading(_text(h)) if h is not None else "") if x)),
                         major="", inter="", last=None)
        elif tag == "section" and not any(_local(x.tag) in LEVEL for x in el.iter()):
            e = el.find("enum")
            p = provision_para(el, squash(_text(e)) if e is not None else "") if state["keep"] else None
            if p:
                state["n"] += 1
                p.update(division=state["division"], title=state["title"],
                         location={"position": f"{'div. ' + state['division'] + ', ' if state['division'] else ''}"
                                               f"{state['title'] or 'no title'}, {p['account_heading']}"})
                paras.append(p)
            return
        elif tag in LEVEL:
            h = el.find("header")
            hdr = _heading(_text(h)) if h is not None else ""
            texts = _bill_blocks(el)
            level = LEVEL[tag]
            if (hdr.startswith("(") or not hdr) and state.get("last") and state["keep"]:
                last = state["last"]                  # "(including transfer of funds)": a note on the heading above
                notes = [hdr] if hdr else []
                if last["para"] is None:
                    if texts:
                        last["para"] = new_para(last["path"], last["hdr"], texts, last.get("notes", []) + notes)
                    else:
                        last["notes"] = last.get("notes", []) + notes
                else:
                    prev = last["para"]
                    prev["heading_notes"] += notes
                    for t in texts:
                        prev["blocks"] += 1
                        prev["amounts"] += amounts_in(t, prev["blocks"])
                return
            if level == 0:
                state.update(major=hdr, inter="")
            elif level == 1:
                state["inter"] = hdr
            path = [x for x in ([state["major"]] if level >= 1 else []) + ([state["inter"]] if level == 2 else []) if x]
            if hdr:
                path.append(hdr)
            state["last"] = {"path": path, "hdr": hdr,
                             "para": new_para(path, hdr, texts, []) if texts and state["keep"] else None}
            return
        for c in el:
            walk(c)

    walk(root)
    return paras


CAPS_LINE = re.compile(r"^[A-Z][A-Z0-9 ,.'’&;:\-—–()/]+$")
LINE_NO = re.compile(r"^\d{1,2}$")
TITLE_LINE = re.compile(r"^TITLE [IVXL]+\b")


def parse_pdf_text(pages, divisions=None):
    """A PDF's text layer, one string per page (committee-site draft bills). A heading is a line in capitals
    (no dollar sign, not a running head); the paragraph is the text up to the next heading. Location: PDF page."""
    paras, title, cur, path = [], "", None, []
    # running heads and footers: a line printed on a quarter or more of the pages
    seen = {}
    for page in pages:
        for x in {squash(x) for x in page.splitlines()}:
            seen[x] = seen.get(x, 0) + 1
    running = {x for x, n in seen.items() if len(pages) >= 8 and n >= len(pages) / 4}
    for pno, page in enumerate(pages, 1):
        lines = [squash(x) for x in page.splitlines()]
        lines = [x for x in lines if x and not LINE_NO.match(x) and x not in running]
        for i, ln in enumerate(lines):
            if TITLE_LINE.match(ln) and ln == ln.upper():
                title, path = ln, []
                cur = None
                continue
            if CAPS_LINE.match(ln) and "$" not in ln and len(ln) > 3 and re.search(r"[A-Z]{3,}", ln) \
                    and not ln.endswith(").") and not re.match(r"^(SEC|S\. |H\. ?R\.)", ln) \
                    and not re.search(r"\d{3,}", ln):
                if ln.startswith("(") and cur is not None:
                    cur["heading_notes"].append(ln)
                    continue
                if cur is not None and not cur["_text"]:
                    path.append(ln)             # a heading over a heading: the path grows
                else:
                    path = path[:1] + [ln] if path else [ln]
                cur = {"division": "", "title": title, "heading_path": list(path), "account_heading": ln,
                       "heading_notes": [], "blocks": 1, "amounts": [], "location": {"pdf_page": pno}, "_text": "",
                       "_blocks": [""]}
                paras.append(cur)
                continue
            if cur is not None:
                if re.match(r"^SEC\. \d+", ln):
                    cur = None
                    continue
                cur["_text"] += " " + ln
                # a new appropriating paragraph: "For ..." (or "In addition ...") after a finished sentence
                if re.match(r"^(For|In addition)\b", ln) and cur["_blocks"][-1].rstrip().endswith("."):
                    cur["_blocks"].append("")
                cur["_blocks"][-1] += " " + ln
    out = []
    for p in paras:
        p.pop("_text")
        blocks = [re.sub(r"(\w)- (\w)", r"\1\2", squash(b)) for b in p.pop("_blocks")]   # words hyphenated across lines
        blocks = [b for b in blocks if b]
        if blocks:
            p["blocks"] = len(blocks)
            p["amounts"] = [a for i, b in enumerate(blocks, 1) for a in amounts_in(b, i)]
            out.append(p)
    return out


def parse(fmt, raw, divisions=None):
    """raw: the bytes of the download, exactly as fetched (their sha256 is the text file's sha256)."""
    if fmt == "uslm":
        paras = parse_uslm(raw, divisions)
    elif fmt == "bill_xml":
        paras = parse_bill_xml(raw, divisions)
    elif fmt == "htm_text":
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        paras = parse_pdf_text([html.unescape(re.sub(r"<[^>]+>", "", text))], divisions)
    elif fmt == "pdf_text":
        import pymupdf                             # the PDF's own text layer; never page images
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            pages = [pg.get_text() for pg in pdf]
        paras = parse_pdf_text(pages, divisions)
    else:
        raise ValueError(fmt)
    for i, p in enumerate(paras, 1):
        p["paragraph_id"] = f"P{i:04d}"
    return [{k: p[k] for k in ("paragraph_id", "division", "title", "heading_path", "account_heading",
                               "heading_notes", "blocks", "amounts", "location")} for p in paras]


# END PARSER
# ------------------------------------------------------------------------------------------------------------

def parser_version():
    src = Path(__file__).read_text()
    body = src[src.index("# PARSER (everything"):src.index("# END PARSER")]
    return hashlib.sha256(body.encode()).hexdigest()[:16]
