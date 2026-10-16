"""
D-3c: re-cite the confirmed absences that rest on a report or explanatory statement (absence_audit.csv, D-3b part 1)
to bill or law text -- for the President's Budget, the Budget Appendix's proposed language. Rule (CLAUDE.md, Data):
a confirmed absence cites bill or law text, never a table's silence.

Each absence is checked against the text of its own stage, read by this script (never by eye):

  House / Senate Reported   the stage's bill (data/legislative_text: the Bill Report Reference's document)
  Enacted                   the stage's law, only the subcommittee's division; FY2025 (a full-year continuing
                            resolution, P.L. 119-4) is the CR and the FY2024 division it continues, both
  President's Budget        FY N's Budget Appendix chapter for the agency (request_language.chapter_paragraphs:
                            the proposed language, bracketed text dropped)

The raw text is the committed download's (its sha256 must equal data/legislative_text/<id>.json's; P.L. 119-4's,
which has no text file, is pinned below). Every heading and section is read as a unit with its heading path; an
absence's checks (CHECKS below) look in the units that name the account: under its heading (with its agency
heading above it), or quoting its name with the agency's in the same provision, or naming its Treasury symbol
(appendix_map.csv). Generic names ('Science', 'Exploration') need the agency in the heading path or the provision.

Outcomes, one per absence (d3c_absences.csv):
  confirmed   the account's heading is in the text and no unit naming it carries the amount type's marker:
              re-cited -- a new confirmed absence citing the text supersedes the old one, which goes on the retired
              list (no ID reused)
  review      the text does not settle it (the heading is not found, a unit naming the account carries a marker,
              or the text's scope is not the absence's): the absence is left as it is and listed for the owner

    python reference/review/accounting/d3c_absences.py           # check, write d3c_absences.csv, print the counts
    python reference/review/accounting/d3c_absences.py --write   # also write data/staged.json and the retired list
    python reference/review/accounting/d3c_absences.py --check   # fail when d3c_absences.csv is not current
"""

import argparse
import csv
import hashlib
import html
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))
import absence_audit as AUDIT  # noqa: E402
import ids as IDS  # noqa: E402
import legislative_text as L  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
TEXT_DIR = ROOT / "data" / "legislative_text"
CACHE = ROOT / "document_store" / "legislative_text"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
APPENDIX_MAP = HERE / "appendix_map.csv"
OUT = HERE / "d3c_absences.csv"
DATE = "2026-10-16"
TAG = "D-3c"

# the FY2025 full-year CR, which has no text file (exemptions.json): pinned here
EXTRA_TEXT = {"SRC-PLAW-119PUBL4": {
    "url": "https://www.govinfo.gov/content/pkg/PLAW-119publ4/uslm/PLAW-119publ4.xml", "format": "uslm",
    "sha256": "1806832d2d7c6563ae0cc8beb69b8b5536e89f0408682c3a250b0e452e44885e", "division": "A"}}
# FY2025 Enacted: P.L. 119-4 division A continues each FY2024 act; the CR and the continued division together
CR_CHAIN = {2025: ("SRC-PLAW-119PUBL4", {"CJS": ("SRC-PLAW-118PUBL42", "C"), "LHHS": ("SRC-PLAW-118PUBL47", "D")})}
CHAPTER = {"NASA": "NASA", "NSF": "NSF", "HHS": "HHS"}
AGENCY = {"NASA": "national aeronautics and space administration", "NSF": "national science foundation",
          "HHS": "health and human services"}
GENERIC = {"science", "exploration"}
# names a bill prints that are not in the account's historical names: H.R. 5952 (FY2019 House) spells out the
# request's 'LEO and Spaceflight Operations'
ALIASES = {"ACC-NASA-SPACEOPS": {"low earth orbit and spaceflight operations"}}

# an emergency designation, or an amount added to the account ('For an additional amount for', 'there are hereby
# appropriated an additional $'); not 'an additional amount of up to $1,000,000 each from amounts made available'
SUPP = re.compile(r"emergency requirement|251\(b\)\(2\)\(A\)|disaster relief|additional amount for|"
                  r"appropriated,? an additional \$|in addition to amounts otherwise (?:appropriated|made available)",
                  re.I)
RESC = re.compile(r"\brescind|\brescission|\bcancel+(?:ed|ation)\b|permanently reduced", re.I)
EMERGENCY = re.compile(r"emergency requirement|251\(b\)\(2\)\(A\)", re.I)
# 'amounts that have expired but have not been cancelled in the ... Space Operations ... accounts shall remain
# available' (the Space Shuttle closeout provision) keeps balances available: it cancels nothing
NOT_CANCELLED = re.compile(r"ha(?:ve|s) not been cancel+ed", re.I)
MARKER_WORDS = {"supplemental": "an emergency or disaster designation or an 'additional amount'",
                "rescission": "'rescind', 'rescission', 'cancel(l)ed' or 'permanently reduced'"}


def norm(s):
    s = html.unescape(s or "").replace("’", "'").replace("`", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s.lower()).split())


# ------------------------------------------------------------------------------------------------- text units

def raw_text(doc_id):
    """(format, bytes) of a document's committed download, its sha256 checked."""
    if doc_id in EXTRA_TEXT:
        meta = EXTRA_TEXT[doc_id]
        fmt, want = meta["format"], meta["sha256"]
        path = CACHE / f"{doc_id}.{fmt}"
        if not path.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(meta["url"], headers={"User-Agent": "approps-intelligence d3c"})
            path.write_bytes(urllib.request.urlopen(req, timeout=180).read())
    else:
        rec = json.loads((TEXT_DIR / f"{doc_id}.json").read_text())
        fmt, want = rec["retrieved_from"]["format"], rec["sha256"]
        path = CACHE / f"{doc_id}.{fmt}"
        if not path.exists():
            pkg = rec["retrieved_from"]["package"]
            sub = "xml" if fmt == "bill_xml" else "uslm"
            CACHE.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(f"https://www.govinfo.gov/content/pkg/{pkg}/{sub}/{pkg}.xml",
                                         headers={"User-Agent": "approps-intelligence d3c"})
            path.write_bytes(urllib.request.urlopen(req, timeout=180).read())
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != want:
        sys.exit(f"{doc_id}: the download's sha256 {got} is not the pinned {want}")
    return fmt, raw


RANK = {"major": 1, "intermediate": 2, "small": 3}
QUOTES = ("quote", "quotedText")


def _text(el):
    """legislative_text._text, with a quoted name kept in quotation marks: bill XML prints ``Space Operations'' as
    <quote>Space Operations</quote>, whose text has no marks."""
    parts = [el.text or ""]
    for c in el:
        if L._local(c.tag) not in L.SKIP_TEXT:
            inner = _text(c)
            if L._local(c.tag) in QUOTES and not re.match(r"\s*[\"\u201c`']", inner):
                inner = "\u201c" + inner + "\u201d"
            parts.append(inner)
        parts.append(c.tail or "")
    return "".join(parts)


def units_xml(fmt, raw):
    """[unit] in document order: every appropriations heading's own text and every section, with the division,
    the title, the heading path above it and the Statutes page (USLM)."""
    root = ET.fromstring(L._xml_safe(raw))
    out, st = [], {"div": "", "title": "", "page": "", "stack": []}

    def heading_of(el):
        for c in el:
            if L._local(c.tag) in ("header", "heading"):
                return L.squash(html.unescape(_text(c)))
        return ""

    def own_text(el):
        parts = []
        for c in el:
            tag = L._local(c.tag)
            if tag in ("header", "heading", "num", "enum", "section", "page") or tag.startswith("appropriations") \
                    or tag in L.SKIP_TEXT:
                continue
            parts.append(_text(c))
        return L.squash(html.unescape(" ".join(parts)))

    def walk(el):
        tag = L._local(el.tag)
        if tag == "page":
            st["page"] = L.squash(_text(el) or "")
            return
        if tag == "division":
            st["div"] = next((L.squash(_text(c)) for c in el if L._local(c.tag) in ("enum", "num")), "")
            st["div"] = re.sub(r"(?i)^division\s+|[^A-Z0-9]", "", st["div"])
            st["stack"] = []
        elif tag == "title":
            st["title"] = heading_of(el) or st["title"]
            num = next((L.squash(_text(c)) for c in el if L._local(c.tag) in ("enum", "num")), "")
            st["title"] = L.squash(f"{num} {st['title']}")
            st["stack"] = []
        elif tag.startswith("appropriations"):
            rank = RANK.get(tag.split("-", 1)[1] if "-" in tag else el.get("level", ""), 3)
            st["stack"] = [s for s in st["stack"] if s[0] < rank] + [(rank, heading_of(el))]
            out.append({"div": st["div"], "title": st["title"], "path": [h for _, h in st["stack"]],
                        "kind": "heading", "text": own_text(el), "page": st["page"]})
        elif tag == "section":
            num = next((L.squash(_text(c)) for c in el if L._local(c.tag) in ("enum", "num")), "")
            page = st["page"]
            for c in el.iter():
                if L._local(c.tag) == "page":
                    st["page"] = L.squash(_text(c) or "")
            text = L.squash(html.unescape(_text(el)))
            out.append({"div": st["div"], "title": st["title"], "path": [h for _, h in st["stack"]],
                        "kind": "section", "sec": num, "text": text, "page": page})
            return
        for c in el:
            walk(c)

    walk(root)
    return out


def units_appendix(fy, chapter):
    import request_language as RL
    out = []
    for p in RL.chapter_paragraphs(fy, chapter):
        text = p["text"] if isinstance(p["text"], str) else " ".join(p["text"])
        out.append({"div": "", "title": p["title"], "path": [p["title"]] + list(p["heading_path"]),
                    "kind": "heading", "text": L.squash(text), "page": p["location"].get("pages", ""),
                    "appendix_account": p.get("appendix_account", "")})
    return out


# ------------------------------------------------------------------------------------------------- accounts

def account_spec(acc, amap):
    names = {norm(acc["canonical_name"])}
    for x in re.split(r";| formerly ", acc.get("historical_names") or ""):
        if norm(x):
            names.add(norm(x))
    names = {re.sub(r"\s*\((?:agency total|title ii general provision|rescission)\)$", "", n) for n in names}
    names |= ALIASES.get(acc["canonical_account_id"], set())
    codes = [amap[acc["canonical_account_id"]]] if acc["canonical_account_id"] in amap else []
    return {"names": names, "codes": codes, "agency": AGENCY.get(acc["agency_key"], "")}


def symbol_rx(code):
    """080-0115 -> any printing of the Treasury symbol: 080X0115, 080-0115, 080-25/30-0115 (dashes of any kind)."""
    a, b = code.split("-")
    return re.compile(rf"\b{a}(?:\s*[-–—]\s*|X|[-–—]\d{{2}}/\d{{2}}[-–—]){b}\b")


def under_heading(u, spec):
    """The unit is the account's own heading (its last heading carries a name), under its agency's heading."""
    if u["kind"] != "heading" or not u["path"]:
        return False
    last = norm(re.sub(r"\s*\([^)]*\)\s*$", "", u["path"][-1]))      # 'Space Operations (including transfers)'
    if last not in spec["names"]:
        return False
    return spec["agency"] in norm(" ".join(u["path"][:-1]) + " " + u["title"])


def name_rx(n):
    """A quoted account name in the original text: ``Name'', "Name", “Name”, 'Name', or Agency--Name''."""
    words = r"[\s,\-]+".join(re.escape(w) for w in n.split())
    return re.compile(rf"(?:``|''|[\"“‘']|--|[\u2014\u2013])\s*{words}\s*(?:''|[\"”’'])", re.I)


SENTENCE = re.compile(r"(?<=\.)\s+(?=[A-Z(\[])")


def window(text, start, end):
    """The sentence holding text[start:end], with the sentence before and the one after it."""
    cuts = [0] + [m.end() for m in SENTENCE.finditer(text)] + [len(text)]
    i = max(k for k in range(len(cuts) - 1) if cuts[k] <= start)
    j = min(k for k in range(1, len(cuts)) if cuts[k] >= end)
    return text[cuts[max(0, i - 1)]:cuts[min(len(cuts) - 1, j + 1)]]


def mentions(u, spec):
    """[(how, window)]: where the unit names the account -- its own heading (the whole paragraph), its Treasury
    symbol, or a quoted name with the agency's name in the provision or its heading path (needed for a generic
    name) -- each with the text around it the marker must sit in."""
    if under_heading(u, spec):
        return [("heading", u["text"])]
    text, out = u["text"], []
    for c in spec["codes"]:
        out += [("symbol", window(text, m.start(), m.end())) for m in symbol_rx(c).finditer(text)]
    context = spec["agency"] in norm(text) or spec["agency"] in norm(" ".join(u["path"]) + " " + u["title"])
    for n in spec["names"]:
        if n in GENERIC and not context:
            continue
        out += [("quoted", window(text, m.start(), m.end())) for m in name_rx(n).finditer(text)]
    return out


def reprint(u):
    """An Appendix unit that only reprints enacted supplemental language (its heading note names the act)."""
    return bool(re.match(r"\(\s*[^)]*(?:Supplemental|Appropriations Act, \d{4}|Public Law)[^)]*\)", u["text"]))


# ------------------------------------------------------------------------------------------------- documents

def doc_label(data, doc_id, division=None):
    refs = {r["reference_id"]: r for r in data["bill_report_refs"]}
    if doc_id.startswith("SRC-BUDGET-APP-"):
        fy, ch = re.match(r"SRC-BUDGET-APP-FY(\d{4})-(\w+)", doc_id).groups()
        return f"the FY{fy} Budget Appendix ({ch} chapter)"
    m = re.match(r"SRC-PLAW-(\d+)PUBL(\d+)", doc_id)
    if m:
        lab = f"P.L. {m.group(1)}-{m.group(2)}"
        return lab + (f", division {division}" if division else "")
    rec = json.loads((TEXT_DIR / f"{doc_id}.json").read_text())
    bill = refs[rec["references"][0]]["bill_id"] if rec["references"] else doc_id
    bill = re.sub(r"^(H\.R\.|S\.)\s*", r"\1 ", bill)
    return f"{bill}, {rec['version']},"


def stage_texts(data, sc, fy, stage, agency):
    """-> [(doc_id, division or None, units)] the absence's stage is printed in; [] when there is none."""
    if stage == "President's Budget":
        doc = f"SRC-BUDGET-APP-FY{fy}-{CHAPTER[agency]}"
        return [(doc, None, units_appendix(fy, CHAPTER[agency]))]
    if stage == "Enacted" and fy in CR_CHAIN:
        cr, cont = CR_CHAIN[fy]
        out = []
        for doc, div in ((cr, EXTRA_TEXT[cr]["division"]), cont[sc]):
            fmt, raw = raw_text(doc)
            out.append((doc, div, [u for u in units_xml(fmt, raw) if u["div"] == div]))
        return out
    ref = next(r for r in data["bill_report_refs"]
               if r["subcommittee"] == sc and int(r["fiscal_year"]) == fy and r["stage"] == stage)
    out = []
    for p in sorted(TEXT_DIR.glob("SRC-*.json")):
        rec = json.loads(p.read_text())
        if ref["reference_id"] not in rec["references"]:
            continue
        fmt, raw = raw_text(rec["document_id"])
        units = units_xml(fmt, raw)
        div = ref.get("division") or None
        if div and any(u["div"] for u in units):
            units = [u for u in units if u["div"] == div]
        else:
            div = None
        out.append((rec["document_id"], div, units))
    return out


# ------------------------------------------------------------------------------------------------- checks

def where(u):
    bits = []
    if u["kind"] == "section":
        bits.append("sec. " + re.sub(r"(?i)^sec\.?\s*|\.$", "", u["sec"].strip()))
    elif not u.get("page"):
        m = re.match(r"\s*(?:TITLE\s+)?([IVXL]+)\b", u["title"], re.I)
        bits.append(f"Title {m.group(1).upper()}" if m else "the bill")
    if u.get("page"):
        bits.append(u["page"] if u["page"].startswith("p.") else u["page"].replace("STAT.", "Stat."))
    return ", ".join(bits)


SMALL = {"and", "or", "of", "the", "for", "in", "to", "a", "an"}


def title_case(h):
    words = re.sub(r"\s*\([^)]*\)\s*$", "", h).lower().split()
    return " ".join(w if i and w in SMALL else "-".join(p[:1].upper() + p[1:] for p in w.split("-"))
                    for i, w in enumerate(words))


def check_marked(units, spec, marker):
    """(heading units, hits, skipped): the account's heading units; every place a unit names the account with the
    marker in the text around it; and the Appendix units skipped as reprinted enacted language."""
    heads = [u for u in units if under_heading(u, spec)]
    hits, skipped = [], []
    for u in units:
        if reprint(u):
            skipped.append(u)
            continue
        for how, text in mentions(u, spec):
            if marker.search(NOT_CANCELLED.sub("", text)):
                hits.append((how, u, text))
    return heads, hits, skipped


def agency_marked(units, spec, marker, every_spec):
    """Units of the agency (under its heading, or naming it) carrying the marker but naming none of its held
    accounts: listed with the absence (a marker the account check could not place)."""
    out = []
    for u in units:
        if reprint(u):
            continue
        ctx = spec["agency"] in norm(" ".join(u["path"]) + " " + u["title"] + " " + u["text"])
        if ctx and marker.search(NOT_CANCELLED.sub("", u["text"])) and not any(mentions(u, s) for s in every_spec):
            out.append(u)
    return out


def check_terms(units, rx, marker=None):
    """Units whose text or heading matches rx (and the marker, when one is given)."""
    out = []
    for u in units:
        blob = " ".join(u["path"]) + " " + u["text"]
        if rx.search(blob) and (marker is None or marker.search(u["text"])):
            out.append(u)
    return out


# Labor-HHS accounts: what each absence means in bill or law text, searched over the whole document or division.
# (term regex, marker or None, the claim in words); an account not here is routed to review.
LHHS_TERMS = {
    "ACC-HHS-AHA-TOTAL": (re.compile(r"Healthy America", re.I), None,
                          "no 'Administration for a Healthy America' heading or text"),
    "ACC-HHS-NIH-ARPA-H": (re.compile(r"Advanced Research Projects Agency for Health|ARPA-H", re.I), None,
                           "no 'Advanced Research Projects Agency for Health' (ARPA-H) heading or text"),
    "ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION": (re.compile(r"adoption", re.I), RESC,
                                                  "no rescission or cancellation of Adoption Incentives (or other "
                                                  "adoption) funds"),
    "ACC-HHS-GP-MEDICARE-OPERATIONS": (re.compile(r"may transfer up to \$[\d,]+ to such account from the Federal "
                                                  r"Hospital", re.I), None,
                                       "no section letting the Secretary transfer Medicare trust fund amounts to "
                                       "'Centers for Medicare & Medicaid Services, Program Management'"),
    "ACC-HHS-GP-NEF-RESCISSION": (re.compile(r"Nonrecurring Expenses Fund", re.I), RESC,
                                  "no emergency-designated rescission of Nonrecurring Expenses Fund balances"),
}
LHHS_SEARCHED = {"ACC-HHS-AHA-TOTAL": "/Healthy America/",
                 "ACC-HHS-NIH-ARPA-H": "/Advanced Research Projects Agency for Health|ARPA-H/",
                 "ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION": "'adoption' in a provision that rescinds or cancels",
                 "ACC-HHS-GP-MEDICARE-OPERATIONS": "/may transfer up to $... to such account from the Federal "
                                                   "Hospital/",
                 "ACC-HHS-GP-NEF-RESCISSION": "'Nonrecurring Expenses Fund' in a provision that rescinds or "
                                              "cancels, for an emergency designation"}
# an account the absence's year is before: the law that first prints its heading (the store reads 'did not exist'
# as a year outside the account's life, approps_store.history_grid)
LHHS_FIRST = {"ACC-HHS-NIH-ARPA-H": ("SRC-PLAW-117PUBL103", "H", "FY2022",
                                     re.compile(r"advanced research projects agency for health", re.I))}
# accounts whose absence bill or law text cannot settle: the reason, for review
LHHS_REVIEW = {
    # the stage's text prints no ASPR heading, but appropriates ASPR's programs under another heading: the absence
    # is a question of the account's scope (which headings make up ASPR before FY2024), not of the text's silence
    "ACC-HHS-ASPR-TOTAL": (re.compile(r"PUBLIC HEALTH AND SOCIAL SERVICES EMERGENCY FUND", re.I),
                           re.compile(r"Biomedical Advanced Research and Development Authority", re.I)),
}


def scope_reason(data, texts, heading_rx, text_rx):
    found = [f"{doc_label(data, d, div).rstrip(',')}, {where(u)} ('{title_case(u['path'][-1])}', under "
             f"'{title_case(u['path'][-2]) if len(u['path']) > 1 else u['title']}')"
             for d, div, units in texts for u in units
             if u["kind"] == "heading" and u["path"] and heading_rx.search(u["path"][-1]) and text_rx.search(u["text"])]
    if not found:
        return "no text check defined for the account"
    return ("the text prints no 'Administration for Strategic Preparedness and Response' heading, but appropriates "
            "ASPR's programs (the Biomedical Advanced Research and Development Authority among them) under "
            + "; ".join(found) + ": whether the account is absent is a question of its scope before FY2024 "
            "(which headings make up ASPR), not one the text's silence settles")


def check(data, row, acc, amap, agency_accounts=()):
    sc = acc["subcommittee"]
    fy, stage, typ = int(row["fiscal_year"]), row["stage"], row["amount_type"]
    aid = row["canonical_account_id"]
    texts = stage_texts(data, sc, fy, stage, acc["agency_key"])
    if not texts:
        return {"outcome": "review", "reason": "no bill or law text for the stage"}
    labels = [doc_label(data, d, div) for d, div, _ in texts]
    docs = [d for d, _, _ in texts]
    scope = " and ".join(labels)
    if fy in CR_CHAIN and stage == "Enacted":
        scope = f"{labels[0]} (the full-year CR, continuing {labels[1]})"

    if sc == "LHHS":
        if aid in LHHS_REVIEW:
            return {"outcome": "review", "reason": scope_reason(data, texts, *LHHS_REVIEW[aid]), "docs": docs}
        if aid not in LHHS_TERMS:
            return {"outcome": "review", "reason": "no text check defined for the account", "docs": docs}
        rx, marker, claim = LHHS_TERMS[aid]
        hits = []
        for doc, div, units in texts:
            for u in check_terms(units, rx, marker):
                if aid == "ACC-HHS-GP-NEF-RESCISSION" and not (row["component"] == "emergency"
                                                               and EMERGENCY.search(u["text"])):
                    continue
                hits.append((doc, u))
        if hits:
            return {"outcome": "review", "docs": docs,
                    "reason": "the text names it: " + "; ".join(f"{d} {where(u)}: {u['text'][:200]}"
                                                               for d, u in hits[:3])}
        lead = "the proposed language of " if stage == "President's Budget" else ""
        ev = (f"{lead}{scope}: {claim} (searched {LHHS_SEARCHED[aid]} over every heading and section: none).")
        if aid in LHHS_FIRST:
            doc, div, first_fy, hx = LHHS_FIRST[aid]
            fmt, raw = raw_text(doc)
            u = next(u for u in units_xml(fmt, raw) if u["div"] == div and u["kind"] == "heading" and u["path"]
                     and hx.fullmatch(u["path"][-1].strip()))
            ev += (f" The account did not exist in FY{fy}: its heading is first printed in {first_fy}'s "
                   f"{doc_label(data, doc, div)}, under '{title_case(u['path'][-2])}', {where(u)}.")
        return {"outcome": "confirmed", "docs": docs, "source": docs[0],
                "evidence": ev[0].upper() + ev[1:]}

    spec = account_spec(acc, amap)
    marker = SUPP if typ == "supplemental" else RESC if typ == "rescission" else None
    if marker is None:
        return {"outcome": "review", "reason": f"no text check defined for amount type {typ!r}", "docs": docs}
    every = [account_spec(a, amap) for a in agency_accounts]
    heads, hits, skipped, loose = [], [], [], []
    for doc, div, units in texts:
        h, x, sk = check_marked(units, spec, marker)
        heads += [(doc, u) for u in h]
        hits += [(doc, how, u, t) for how, u, t in x]
        skipped += [(doc, u) for u in sk]
        loose += [(doc, u) for u in agency_marked(units, spec, marker, every)]
    if not heads:
        return {"outcome": "review", "docs": docs,
                "reason": "the account's heading is not found under its agency in " + scope}
    if hits:
        return {"outcome": "review", "docs": docs,
                "reason": "a provision naming the account carries the marker: " + "; ".join(
                    f"{d} {where(u)} ({how}): {' '.join(t.split())[:300]}" for d, how, u, t in hits[:3])}
    if loose:
        return {"outcome": "review", "docs": docs,
                "reason": "a provision of the agency carries the marker without naming an account: " + "; ".join(
                    f"{d} {where(u)}: {u['text'][:300]}" for d, u in loose[:3])}
    name = title_case(heads[0][1]["path"][-1])
    at = "; ".join(sorted({f"{doc_label(data, d)}: {where(u)}" if len(texts) > 1 else where(u)
                           for d, u in heads}))
    sym = f" or its Treasury symbol {spec['codes'][0]}" if spec["codes"] else ""
    lead = "the proposed language of " if stage == "President's Budget" else ""
    if typ == "supplemental":
        claim = (f"designates no amount under the heading '{name}' ({at}) as an emergency requirement and "
                 f"appropriates no additional amount for it")
    else:
        claim = f"rescinds or cancels nothing from '{name}' ({at})"
    ev = (f"{lead}{scope} {claim} (searched every heading and section naming the account{sym} for "
          f"{MARKER_WORDS[typ]}: none).")
    return {"outcome": "confirmed", "docs": docs, "source": docs[0],
            "evidence": ev[0].upper() + ev[1:]}


# ------------------------------------------------------------------------------------------------- run

FIELDS = ["confirmed_absence_id", "canonical_account_id", "fiscal_year", "stage", "amount_type", "component",
          "old_source_document_id", "outcome", "new_confirmed_absence_id", "new_source_document_id", "documents_read",
          "evidence_or_reason"]
# the note a re-cited absence's evidence ends with: the old ID and the report it cited
SUPERSEDES = re.compile(rf"\({TAG}; supersedes ([A-Z]+(?:-[A-Z]+)?-\d+), which cited ([\w-]+)\)$")


# owner ruling (2026-10-16, option 2 of review-D3c.md): the ASPR FY2023 absences are retired, the cells become
# missing -- ASPR was not an entity apart from the Office of the Secretary until FY2024. Kept here as they were, so
# a re-run still checks them and the output stays the same.
OWNER_RETIRED = {
    "CA-LHHS-0030": ("ACC-HHS-ASPR-TOTAL", 2023, "President's Budget", "SRC-CRPT-117HRPT403"),
    "CA-LHHS-0031": ("ACC-HHS-ASPR-TOTAL", 2023, "House Reported", "SRC-CRPT-117HRPT403"),
    "CA-LHHS-0033": ("ACC-HHS-ASPR-TOTAL", 2023, "Senate Reported", "SRC-EXPL-LHHS-FY2023-SENATE"),
}
RETIRED_REASON = ("ASPR FY2023: rested on a report's table; the FY2023 text funds ASPR's programs under the Office "
                  "of the Secretary's Public Health and Social Services Emergency Fund, and ASPR was not an entity "
                  f"apart from the Office of the Secretary until FY2024 (owner, {DATE}): retired, the cell is "
                  f"missing ({TAG})")


def targets(data):
    """[(row, live re-cited absence or None)]: the absences citing a report (absence_audit), and the ones this
    script already re-cited, rebuilt as they were (so a re-run checks them again and the output stays the same)."""
    out = [(r, None) for r in AUDIT.audit(data)]
    for c in data["confirmed_absences"]:
        m = SUPERSEDES.search(c["evidence"])
        if m:
            out.append(({**c, "confirmed_absence_id": m.group(1), "source_document_id": m.group(2)}, c))
    live = {c["confirmed_absence_id"] for c in data["confirmed_absences"]}
    for cid, (aid, fy, stage, doc) in OWNER_RETIRED.items():
        if cid not in live:
            out.append(({"confirmed_absence_id": cid, "canonical_account_id": aid, "fiscal_year": fy, "stage": stage,
                         "amount_type": "budget authority", "component": "", "source_document_id": doc}, None))
    return sorted(out, key=lambda t: IDS.parse(t[0]["confirmed_absence_id"])[1:])


def evidence_of(r, old_id, old_doc):
    return f"{r['evidence_or_reason']} ({TAG}; supersedes {old_id}, which cited {old_doc})"


def run(data):
    acc = {a["canonical_account_id"]: dict(a) for a in data["accounts"]}
    for a in acc.values():
        a["agency_key"] = a["canonical_account_id"].split("-")[1]
    amap = {}
    with APPENDIX_MAP.open() as f:
        for r in csv.DictReader(f):
            amap.setdefault(r["canonical_account_id"], r["appendix_account"])
    rows, drift = [], []
    for row, live in targets(data):
        a = acc[row["canonical_account_id"]]
        res = check(data, row, a, amap, [b for b in acc.values() if b["agency_key"] == a["agency_key"]
                                         and "(agency total)" not in b["canonical_name"]])
        if row["confirmed_absence_id"] in OWNER_RETIRED and res["outcome"] == "review":
            res = {**res, "outcome": "retired", "reason": res["reason"] + f". Owner ruling ({DATE}): retired"}
        out = {"confirmed_absence_id": row["confirmed_absence_id"],
               "canonical_account_id": row["canonical_account_id"], "fiscal_year": row["fiscal_year"],
               "stage": row["stage"], "amount_type": row["amount_type"], "component": row.get("component", ""),
               "old_source_document_id": row["source_document_id"], "outcome": res["outcome"],
               "new_confirmed_absence_id": live["confirmed_absence_id"] if live else "",
               "new_source_document_id": res.get("source", ""), "documents_read": " ".join(res.get("docs", [])),
               "evidence_or_reason": res.get("evidence") or res.get("reason", "")}
        if live and (res["outcome"] != "confirmed" or live["source_document_id"] != out["new_source_document_id"]
                     or live["evidence"] != evidence_of(out, row["confirmed_absence_id"], row["source_document_id"])):
            drift.append(live["confirmed_absence_id"])
        rows.append(out)
    return rows, drift


def render(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def apply(data, retired, rows):
    """Supersede each confirmed row's absence not yet re-cited: a new one citing the text; the old ID to the
    retired list. Fills each row's new_confirmed_absence_id."""
    old = {c["confirmed_absence_id"]: c for c in data["confirmed_absences"]}
    docs = {s["document_id"] for s in data["source_docs"]}
    alloc = IDS.Allocator([c["confirmed_absence_id"] for c in data["confirmed_absences"]]
                          + list(retired.get("confirmed_absences", {})))
    sc = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    new, gone = [], {}
    for r in rows:
        if r["outcome"] != "confirmed" or r["new_confirmed_absence_id"]:
            continue
        c = old[r["confirmed_absence_id"]]
        assert r["new_source_document_id"] in docs, r
        nid = alloc.next("CA", sc[c["canonical_account_id"]])
        new.append({**c, "confirmed_absence_id": nid, "source_document_id": r["new_source_document_id"],
                    "evidence": evidence_of(r, c["confirmed_absence_id"], c["source_document_id"]),
                    "confirmed_date": DATE})
        gone[c["confirmed_absence_id"]] = (f"rested on a report or explanatory statement ({c['source_document_id']}); "
                                           f"re-cited to bill or law text as {nid} ({TAG}, {DATE})")
        r["new_confirmed_absence_id"] = nid
    for r in rows:
        if r["outcome"] == "retired" and r["confirmed_absence_id"] in old:
            gone[r["confirmed_absence_id"]] = RETIRED_REASON
    data["confirmed_absences"] = [c for c in data["confirmed_absences"] if c["confirmed_absence_id"] not in gone] + new
    retired["confirmed_absences"].update(gone)
    return new, gone


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    rows, drift = run(data)
    n = sum(r["outcome"] == "confirmed" for r in rows)
    k = sum(r["outcome"] == "retired" for r in rows)
    print(f"absences checked: {len(rows)}; confirmed by text: {n}; retired (owner ruling): {k}; "
          f"for review: {len(rows) - n - k}")
    if drift:
        print(f"re-cited absences the text no longer gives the same evidence for: {', '.join(drift)}")
    if args.write:
        retired = json.loads(RETIRED.read_text())
        new, gone = apply(data, retired, rows)
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        RETIRED.write_text(json.dumps(retired, indent=1, ensure_ascii=False) + "\n")
        print(f"retired {len(gone)} absences (superseded or by ruling); added {len(new)}")
    text = render(rows)
    if args.check:
        if drift or not OUT.exists() or OUT.read_text() != text:
            print("d3c_absences.csv is not current: run reference/review/accounting/d3c_absences.py")
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
