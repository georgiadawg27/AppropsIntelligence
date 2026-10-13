"""
The President's Budget request language (D-2 step 1): each held account's proposed appropriations language, read
from FY N's Budget Appendix (the HHS chapter for Labor-HHS; Commerce, Justice, NASA, NSF and EOP for CJS),
FY2017-FY2027, into request_language.json, which scripts/tag_components.py reads with the phrase rules
(tag_method appendix_text).

    python reference/review/accounting/request_language.py          # reads the cached chapters (fetches what it lacks)
    python reference/review/accounting/request_language.py --check  # fail when request_language.json is not current

How the language is found (text layer only; no page images were needed: every chapter has a text layer):
  - A chapter prints, for each account, its heading in small capitals (NewCenturySchlbk), the proposed language in
    the body face (Times), then its schedules (TradeGothic). The language is the run of body lines right after a
    heading, up to the next heading, a schedule line, or a 'Note.--' paragraph. The program narrative printed after
    the schedules follows no heading and is never read as language.
  - Two columns per page, left then right; the running head (above y 48) is skipped. A word broken over two lines
    ('be-' / 'havioral') is joined.
  - The Appendix prints the enacted language it would delete in [brackets] and the new language in italics: the
    proposed language is the printed text with every bracketed span dropped (`text`); `printed` keeps both.
  - Paragraphs: a new paragraph starts at an indented first line; every paragraph under one heading is one record
    with numbered blocks, as the bill parser gives them (legislative_text.py).

An account is matched as the bill-text check matches a bill's paragraph (check_legislative_text.account_names and
heading_keys): a heading carrying one of the account's names, under the account's agency heading (the agency's
words in the headings above it, the heading itself, or the chapter). Outcomes:
  read        the account's heading and language were found (its amounts may or may not match a phrase rule)
  not found   no heading in the chapter carries the account's names under its agency; the reason names the
              chapter and any similar heading (never matched by guess)
"""

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import appendix_attributes as A  # noqa: E402
import check_legislative_text as CT  # noqa: E402
import legislative_text as L  # noqa: E402

OUT = Path(__file__).resolve().parent / "request_language.json"
STAGE = "President's Budget"
HEADER_Y = 48           # the running head and page number print above this
INDENT = 3.5            # a paragraph's first line is indented ~6pt from the column margin
TOTAL_ONLY = ("ACC-HHS-AHRQ-TOTAL", "ACC-HHS-ACL-TOTAL")    # ACL: owner, D-2 item 5c
# the page images read (D-2): each checks an amount the text layer reads oddly; each image shows the amount exactly
# as the text layer reads it (the Appendix's own misprint, or a figure that differs from the report's)
PAGE_IMAGES = [
    {"document_id": "SRC-BUDGET-APP-FY2022-NASA", "page": 1201, "amount_as_printed": "$46,00,0000",
     "account": "ACC-NASA-OIG", "finding": "printed so (misplaced commas): not read as a figure"},
    {"document_id": "SRC-BUDGET-APP-FY2024-HHS", "page": 433, "amount_as_printed": "$552,500,0000",
     "account": "ACC-HHS-SAMHSA-MENTAL-HEALTH", "finding": "printed so (misplaced commas): not read as a figure"},
    {"document_id": "SRC-BUDGET-APP-FY2027-HHS", "page": 477, "amount_as_printed": "$3,527,479,00",
     "account": "ACC-HHS-ACF-REFUGEE", "finding": "printed so (misplaced commas): not read as a figure"},
    {"document_id": "SRC-BUDGET-APP-FY2027-HHS", "page": 454, "amount_as_printed": "$833,000",
     "account": "ACC-HHS-NIH-NEI", "finding": "printed so; the report's request column prints 833,000 thousand: a "
                                              "probable misprint (owner, D-2), listed under misprinted_amounts and "
                                              "not read"},
]
# well-formed amounts the owner treats as probable misprints (D-2): (FY, chapter, printed page, amount as printed)
PROBABLE_MISPRINTS = {(2027, "HHS", 454, "$833,000")}    # an agency held only as its total, read as an account (owner, D-1 ruling 2)
CHAPTER_TITLE = {"HHS": "DEPARTMENT OF HEALTH AND HUMAN SERVICES", "COM": "DEPARTMENT OF COMMERCE",
                 "DOJ": "DEPARTMENT OF JUSTICE", "NASA": "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION",
                 "NSF": "NATIONAL SCIENCE FOUNDATION", "EOP": "EXECUTIVE OFFICE OF THE PRESIDENT"}
NOTE = re.compile(r"^\s*Note\.\s*[—-]")
# the bill check's agency words that do not fit the Appendix: EOP's accounts are under Title III's 'Science' in the
# bill, under the chapter's own title here
APPENDIX_AGENCY_WORDS = {"Executive Office of the President": "executive office president"}
PAREN = re.compile(r"^\s*\[?\(.*\)\]?\s*$")
# a paragraph under a heading note naming a supplemental act is enacted language the Appendix reprints, and a
# legislative proposal is outside the appropriations request: neither is the account's request language
SUPPLEMENTAL = re.compile(r"Supplemental|Appropriations Act, \d{4}|Public Law|P\.L\.", re.I)
LEGISLATIVE = re.compile(r"Legislative proposal", re.I)
CAPS = re.compile(r"^[A-Z][A-Z ,.'’\-–—/&\[\]]{6,}$")     # a heading line: capitals only, no amount


def kind(span_fonts, text, size):
    """'heading' (small capitals), 'agency' (bold Times, 10pt and up), 'body' (Times text) or 'break'."""
    fonts = {f for f, _ in span_fonts}
    if all(f.startswith("NewCenturySchlbk") for f in fonts):
        return "heading"
    if all(f.startswith("TimesNewRomanPSStd-Bold") and not f.endswith("BoldI") for f in fonts) and size >= 9.9 \
            and text.strip() and text.upper() == text and not re.search(r"\[|\]", text):
        return "agency"
    if all(f.startswith("TimesNewRomanPSStd") for f in fonts) and not any(f.endswith("BoldI") for f in fonts):
        return "body"
    return "break"


def page_lines(page):
    """[(kind, text, x from the column margin, column, y)] in reading order: left column, then right."""
    width = page.rect.width
    rows = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            x0, y0 = ln["bbox"][0], ln["bbox"][1]
            if y0 < HEADER_Y:
                continue
            text = "".join(s["text"] for s in ln["spans"])
            size = max(s["size"] for s in spans)
            fonts = [(s["font"], round(s["size"], 1)) for s in spans]
            col = 0 if x0 < width / 2 else 1
            rows.append((col, round(y0, 1), x0, kind(fonts, text, size), text))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    # merge pieces printed on the same baseline in one column (a bracket in another face splits a line)
    merged = []
    for r in rows:
        if merged and merged[-1][0] == r[0] and abs(merged[-1][1] - r[1]) < 1.5 and merged[-1][3] == r[3]:
            m = merged[-1]
            merged[-1] = (m[0], m[1], min(m[2], r[2]), m[3], m[4] + r[4])
        else:
            merged.append(r)
    margins = {}
    for col in (0, 1):
        xs = [r[2] for r in merged if r[0] == col and r[3] == "body"]
        margins[col] = min(xs) if xs else 0
    return [(k, t, x - margins[c], c, y) for c, y, x, k, t in merged]


def join(lines):
    out = ""
    for t in lines:
        t = t.strip()
        if not out:
            out = t
        elif re.search(r"[A-Za-z]-$", out) and re.match(r"[a-z]", t):
            out = out[:-1] + t
        elif re.search(r"\$(?:\d{1,3}(?:,\d{3})*,?)?$", out) and re.match(r"\d", t):
            out += t                    # an amount broken over two lines ('$46,' / '000,000')
        else:
            out += " " + t
    return out


TOKEN = re.compile(r"\$[\d,]*\d")
WELL_FORMED = re.compile(r"\$\d{1,3}(?:,\d{3})*|\$\d+")
CITATION = re.compile(r"\s*\(([^()]*(?:Act|Resolution)[^()]*?,? \d{4})\.?\)\s*$")


def citations(printed):
    """The act(s) the Appendix cites after an account's language ('(Department of Health and Human Services
    Appropriations Act, 2023.)'): the law the language amends or reprints, not language."""
    out, t = [], " ".join(re.sub(r"\[[^\]]*\]", " ", printed).split())
    while (m := CITATION.search(t)):
        out.insert(0, m.group(1))
        t = t[:m.start()]
    return out


def proposed(printed):
    """The proposed language: the printed text with every bracketed (to be deleted) span dropped, and the act the
    Appendix cites after it."""
    t = " ".join(re.sub(r"\[[^\]]*\]", " ", printed).split())
    while (m := CITATION.search(t)):
        t = t[:m.start()]
    return t.strip()


def chapter_paragraphs(fy, chapter):
    """-> [paragraph] for the chapter: the language under each heading, in the bill parser's shape."""
    import pymupdf
    first, last, part = next((f, l, p) for y, ch, f, l, p in A.CHAPTERS if y == fy and ch == chapter)
    doc = pymupdf.open(str(A.fetch(fy, part)))
    title = CHAPTER_TITLE[chapter]
    agency, heading, notes = None, [], []
    paras, cur, block, reading, pending_head, last_kind, paren, last_head = [], None, None, False, [], None, None, None

    def close_block():
        nonlocal block
        if block and cur is not None:
            cur["_blocks"].append((block["page"], block["lines"]))
        block = None

    def close_para():
        nonlocal cur
        close_block()
        if cur is not None and cur["_blocks"]:
            paras.append(cur)
        cur = None

    for i, page in enumerate(doc):
        pno = first + i
        for k, text, x, col, y in page_lines(page):
            if k == "agency":
                close_para()
                reading = False
                pending_head = []
                t = " ".join(text.split())
                if t != title:
                    # an agency heading printed on two lines ('SUBSTANCE ABUSE AND MENTAL HEALTH SERVICES' /
                    # 'ADMINISTRATION') is one heading
                    agency = f"{agency} {t}" if last_kind == "agency" and agency else t
                last_kind = k
                continue
            last_kind = k
            if k != "body":
                paren = None
            if k == "heading":
                close_para()
                t = " ".join(text.split())
                if PAREN.match(t):
                    notes.append(t)
                else:
                    if not pending_head:
                        notes = []
                    if pending_head and not (last_head and last_head[:2] == (pno, col) and 0 < y - last_head[2] < 12):
                        # not the next line of the same heading: a heading with no language of its own, then the
                        # next heading
                        pending_head = []
                        notes = []
                    pending_head.append(t)
                    last_head = (pno, col, y)
                reading = True
                continue
            if k == "break":
                m = A.CODE.search(text)
                if m:
                    # the schedule after a heading's language names the Appendix account it is language for
                    code = f"{m.group(1)}-{m.group(2)}"
                    for q in paras:
                        q.setdefault("code", code)
                    if cur is not None:
                        cur.setdefault("code", code)
                close_para()
                reading = False
                pending_head = []
                continue
            # body
            if not reading:
                continue
            if NOTE.match(text):
                close_para()
                reading = False
                pending_head = []
                continue
            t = " ".join(text.split())
            if paren is not None:
                paren += " " + t
                if paren.rstrip("]").endswith(")"):
                    notes.append(paren)
                    paren = None
                continue
            if pending_head and re.match(r"^\[?\(", t):
                # '(INCLUDING TRANSFER OF FUNDS)', '(Legislative proposal, not subject to PAYGO)', '(Disaster
                # Relief Supplemental Appropriations Act, 2023.)' set in the body face, maybe over two lines: a note
                if t.rstrip("]").endswith(")"):
                    notes.append(t)
                else:
                    paren = t
                continue
            if CAPS.match(t) and not cur:
                # a heading set in the body face ('GENERAL DEPARTMENTAL MANAGEMENT' under OFFICE OF THE SECRETARY)
                pending_head = [t]
                continue
            if CAPS.match(t) and cur is not None:
                close_para()
                pending_head, notes = [t], []
                continue
            if pending_head:
                heading = [" ".join(pending_head)]
                pending_head = []
                cur = {"heading": heading[0], "agency": agency, "notes": list(notes), "_blocks": []}
            if cur is None:
                continue
            if block is None or x > INDENT:
                close_block()
                block = {"page": pno, "lines": []}
            block["lines"].append(text)
    close_para()
    out = []
    for n, p in enumerate(paras, 1):
        printed = [join(lines) for _, lines in p["_blocks"]]
        pages = sorted({pg for pg, _ in p["_blocks"]})
        amounts, misprints = [], []
        for b, t in enumerate(printed, 1):
            pt = proposed(t)
            bad = {m.start() for m in TOKEN.finditer(pt) if not WELL_FORMED.fullmatch(m.group().rstrip("."))}
            for m, x in zip(L.AMOUNT.finditer(pt), L.amounts_in(pt, block=b)):
                block_page = p["_blocks"][b - 1][0]
                if m.start() in bad:
                    # printed with misplaced commas ('$46,00,0000'): no dollar figure can be read from it
                    misprints.append(next(t_.group() for t_ in TOKEN.finditer(pt) if t_.start() == m.start()))
                elif (fy, chapter, block_page, m.group(0)) in PROBABLE_MISPRINTS:
                    # well formed but a probable misprint (owner, D-2): not read, so no component or view uses it
                    misprints.append(m.group(0))
                else:
                    amounts.append(x)
        # a heading the request renames or deletes prints its old words in brackets: the proposed heading drops
        # them; the heading as enacted keeps their words (matched too, as the report's table may use either)
        head = proposed(p["heading"]) or p["heading"].replace("[", "").replace("]", "")
        path = [h for h in (p["agency"], head) if h]
        pg = f"p.{pages[0]}" if len(pages) == 1 else f"pp.{pages[0]}-{pages[-1]}"
        notes = " ".join(p["notes"])
        kind_ = ("supplemental" if SUPPLEMENTAL.search(notes) else
                 "legislative proposal" if LEGISLATIVE.search(notes) else "request")
        out.append({"paragraph_id": f"A{n:04d}", "division": "", "title": title, "heading_path": path,
                    "account_heading": head, "heading_printed": p["heading"], "language": kind_,
                    "appendix_account": p.get("code"),
                    "heading_notes": p["notes"], "blocks": len(printed),
                    "printed": printed, "text": [proposed(t) for t in printed],
                    "cites": sorted({c for t in printed for c in citations(t)}), "amounts": amounts,
                    "misprinted_amounts": misprints,
                    "location": {"position": f"Budget Appendix {pg}", "pages": pg}})
    return out


def heading_keys(para):
    """The bill check's heading keys for the proposed heading and for the heading with its bracketed words kept."""
    old = para["heading_printed"].replace("[", "").replace("]", "")
    return CT.heading_keys(para) | CT.heading_keys(dict(para, heading_path=para["heading_path"][:-1] + [old]))


def in_appendix_context(acc, para):
    """The account's agency words among the chapter title, the agency heading and the heading itself (EOP's OSTP is
    its own heading; a bureau's accounts sit under its agency heading)."""
    ctx = set(CT.norm(" ".join(para["heading_path"] + [para["title"]])).split())
    words_ = APPENDIX_AGENCY_WORDS.get(acc["agency"]) or CT.agency_words(acc)
    return any(all(w in ctx for w in words.split())
               for words in [words_] + CT.AGENCY_ALIASES.get(acc["agency"], []))


def held_requests(data):
    """{(account, fy): headline observation} for every President's Budget headline of an account (and AHRQ's total)."""
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    out = {}
    for o in data["observations"]:
        a = acc[o["canonical_account_id"]]
        if (o["stage"] != STAGE or o["amount_type"] != "budget authority" or o.get("component")
                or o["verification_status"] == "superseded"):
            continue
        if a.get("total_scope") and a["canonical_account_id"] not in TOTAL_ONLY:
            continue
        out.setdefault((a["canonical_account_id"], int(o["fiscal_year"])), o)
    return out


def build(data):
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    former = collections.defaultdict(list)
    for h in data.get("historical_names_tab", []):
        former[h["canonical_account_id"]].append(h["former_name"])
    by_acc = collections.defaultdict(list)
    for o in data["observations"]:
        by_acc[o["canonical_account_id"]].append(o)
    want = held_requests(data)
    maprows = A.read_map()
    docs, accounts, cache = {}, [], {}

    def chapter(fy, ch):
        if (fy, ch) not in cache:
            cache[(fy, ch)] = chapter_paragraphs(fy, ch)
        return cache[(fy, ch)]

    def request(p):
        return p["language"] == "request"

    # pass 1: by the account's names, as the bill check matches (every account, so pass 2 knows what is claimed)
    named, claimed = {}, collections.defaultdict(set)
    for (account, fy) in want:
        a = acc[account]
        names = CT.account_names(a, by_acc[account], former)
        hits = [(ch, p) for ch in A.SUBCOMMITTEE_CHAPTERS[a["subcommittee"]] for p in chapter(fy, ch)
                if request(p) and heading_keys(p) & names and in_appendix_context(a, p)]
        named[(account, fy)] = (names, hits)
        for ch, p in hits:
            claimed[(fy, ch)].add(p["paragraph_id"])
    fallback = {}
    for (account, fy) in want:
        a = acc[account]
        chapters = A.SUBCOMMITTEE_CHAPTERS[a["subcommittee"]]
        names, hits = named[(account, fy)]
        how = "by the account's name"
        if not hits:
            # pass 2, a heading no held account claims by name: the account's Appendix account (appendix_map.csv,
            # the request's basis) when one heading's language precedes its schedule, the map's activity (a
            # program activity pattern) naming the heading where the account shares the Appendix account; and an
            # agency held only as its total (AHRQ), its agency's one heading
            for r in A.map_rows(maprows, account, fy, "request"):
                cand = [(r["chapter"], p) for p in chapter(fy, r["chapter"]) if request(p)
                        and p["appendix_account"] == r["appendix_account"]
                        and p["paragraph_id"] not in claimed[(fy, r["chapter"])]
                        and (not r["activity"] or re.search(r["activity"], p["account_heading"], re.I))]
                if len({p["account_heading"] for _, p in cand}) == 1:
                    hits, how = cand, (f"by its Appendix account {r['appendix_account']} (appendix_map.csv"
                                       + (f", activity /{r['activity']}/" if r["activity"] else "") + ")")
                    break
            if not hits and account in TOTAL_ONLY:
                cand = [(ch, p) for ch in chapters for p in chapter(fy, ch) if request(p)
                        and CT.norm(p["heading_path"][0]) == CT.norm(a["agency"])]
                if len({p["account_heading"] for _, p in cand}) == 1:
                    hits, how = cand, f"the one heading under '{a['agency']}' (an agency held only as its total)"
        fallback[(account, fy)] = (hits, how)
    # a heading reached by its Appendix account from more than one held account is no one's (PHSSEF and ASPR
    # Operations share 075-0140 in FY2024-FY2025)
    reach = collections.Counter((fy, ch, p["paragraph_id"]) for (account, fy), (hits, how) in fallback.items()
                                if not named[(account, fy)][1] for ch, p in hits)
    for (account, fy), o in sorted(want.items(), key=lambda kv: (acc[kv[0][0]]["subcommittee"], kv[0][1], kv[0][0])):
        a = acc[account]
        chapters = A.SUBCOMMITTEE_CHAPTERS[a["subcommittee"]]
        names = named[(account, fy)][0]
        hits, how = fallback[(account, fy)]
        shared = [p for ch, p in hits if reach[(fy, ch, p["paragraph_id"])] > 1] if not named[(account, fy)][1] else []
        if shared:
            others = sorted(x for (x, y), (h, _) in fallback.items() if y == fy and x != account
                            and any(q["paragraph_id"] == shared[0]["paragraph_id"] for _, q in h))
            rec = {"account": account, "subcommittee": a["subcommittee"], "fiscal_year": fy,
                   "headline_observation_id": o["observation_id"], "status": "not found", "document_id": None,
                   "paragraph": None,
                   "reason": f"no heading carries the account's names; the heading '{shared[0]['account_heading']}' "
                             f"of its Appendix account ({shared[0]['appendix_account']}) is reached the same way by "
                             f"{', '.join(others)}, so it is no one account's request"}
            accounts.append(rec)
            continue
        near = []
        if not hits:
            for ch in chapters:
                near += [f"{ch}: " + x for x in CT.similar_headings(a, names, [p for p in chapter(fy, ch)
                                                                             if in_appendix_context(a, p)])]
        rec = {"account": account, "subcommittee": a["subcommittee"], "fiscal_year": fy,
               "headline_observation_id": o["observation_id"]}
        if not hits:
            rec.update(status="not found", document_id=None, paragraph=None,
                       reason=f"no heading in the FY{fy} Budget Appendix ({', '.join(chapters)} chapter"
                              f"{'s' if len(chapters) > 1 else ''}) carries the account's names "
                              f"({'; '.join(sorted(names))}) under '{a['agency']}', and its Appendix account has "
                              "no heading of its own" + (f"; similar, not matched: {'; '.join(near[:3])}"
                                                          if near else ""))
            accounts.append(rec)
            continue
        ch = hits[0][0]
        paras = [p for c, p in hits if c == ch]
        live = [p for p in paras if any(t.strip() for t in p["text"])]
        # the enacted language the request deletes (a supplemental act's paragraph reprinted in brackets) is not the
        # request's, when the heading has language of its own
        paras = live or paras
        if len(paras) == 1:
            para = paras[0]
        else:       # one heading printed more than once in the chapter: its paragraphs together, as numbered blocks
            para = dict(paras[0], paragraph_id="+".join(p["paragraph_id"] for p in paras),
                        blocks=sum(p["blocks"] for p in paras), printed=[t for p in paras for t in p["printed"]],
                        text=[t for p in paras for t in p["text"]],
                        cites=sorted({c for p in paras for c in p["cites"]}),
                        misprinted_amounts=[x for p in paras for x in p["misprinted_amounts"]],
                        amounts=[dict(x, block=x["block"] + sum(q["blocks"] for q in paras[:i]))
                                 for i, p in enumerate(paras) for x in p["amounts"]],
                        location={"position": "; ".join(p["location"]["position"] for p in paras),
                                  "pages": "; ".join(p["location"]["pages"] for p in paras)})
        doc_id = A.document_id(fy, ch)
        if doc_id not in docs:
            first, last, part = next((f, l, p) for y, c, f, l, p in A.CHAPTERS if y == fy and c == ch)
            pdf = A.fetch(fy, part)
            docs[doc_id] = {"fiscal_year": fy, "chapter": ch, "chapter_title": A.CHAPTER_NAMES[ch],
                            "printed_pages": f"{first}-{last}", "source_url": A.url(fy, part),
                            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "read_from": "text layer"}
        if not any(t.strip() for t in para["text"]):
            # every word under the heading is in brackets: the request deletes the account's own language (CDC's
            # FY2024 accounts, requested under one CDC-wide heading); what it requests for the account is not in
            # language of its own, so nothing is read (not computable)
            rec.update(status="not found", document_id=doc_id, paragraph=para,
                       reason=f"the FY{fy} request deletes the heading's language (all of it printed in brackets, "
                              f"{A.CHAPTER_NAMES[ch]} chapter {para['location']['pages']}): the account's request "
                              "is not in language of its own")
            accounts.append(rec)
            continue
        rec.update(status="read", document_id=doc_id, paragraph=para,
                   reason=f"the heading '{para['account_heading']}' under '{para['heading_path'][0]}', "
                          f"{A.CHAPTER_NAMES[ch]} chapter {para['location']['pages']}, matched {how}")
        accounts.append(rec)
    return {"note": "The President's Budget request language for each held account, read from FY N's Budget Appendix "
                    "text layer by reference/review/accounting/request_language.py (D-2 step 1). `printed` is the "
                    "text as printed ([brackets] = enacted language the request would delete); `text` and `amounts` "
                    "are the proposed language (brackets dropped). The text layer read every chapter; page images "
                    "were read only to check the amounts listed in page_images, each printed as the text layer reads "
                    "it. An amount printed with misplaced commas, or one the owner treats as a probable misprint (NEI FY2027's '$833,000'), is listed under misprinted_amounts and not read.",
            "page_images": PAGE_IMAGES, "documents": dict(sorted(docs.items())), "accounts": accounts}


def summary(out):
    c = collections.Counter((r["subcommittee"], r["fiscal_year"], r["status"]) for r in out["accounts"])
    rows = sorted({(sc, fy) for sc, fy, _ in c})
    lines = ["| subcommittee | FY | read | not found |", "|---|---|---|---|"]
    for sc, fy in rows:
        lines.append(f"| {sc} | {fy} | {c[(sc, fy, 'read')]} | {c[(sc, fy, 'not found')]} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads((ROOT / "data" / "staged.json").read_text())
    out = build(data)
    text = json.dumps(out, indent=1, ensure_ascii=False) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit(f"{OUT.name} is not current: run this script")
        return
    OUT.write_text(text)
    print(summary(out))
    for r in out["accounts"]:
        if r["status"] == "not found":
            print(f"- {r['account']} FY{r['fiscal_year']}: {r['reason']}")


if __name__ == "__main__":
    main()
