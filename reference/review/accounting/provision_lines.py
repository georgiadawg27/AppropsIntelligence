"""
Components the phrase rules cannot read (component backfill follow-ups): money a bill or law makes available to an
account where the words that make it a component sit outside the amount's own phrase window, or in a general
provision that names the account by its purpose rather than its heading. Each is read from the full text of the
bill or law and written to provision_lines.csv, which scripts/tag_components.py reads (offline).

  Nonrecurring Expenses Fund (section 223 of division G of P.L. 110-161)  'shall be derived by transfer from the
      Fund established by Public Law 110-161, division G, title II, section 223' (a proviso whose words run past the
      amount's window) and the general provisions 'Of the unobligated balances available in the "Nonrecurring
      Expenses Fund" ..., $X ... shall be available for <purpose>' -> transfer_in beside the headline of the
      account the purpose names (FY2018, FY2020, FY2021 Enacted; FY2021 and FY2023 Senate).
  Section 241 evaluation funds  '$X is available to this appropriation, for the purposes under this heading, from
      amounts provided pursuant to section 241 of the PHS Act' (FY2022 Senate: the window ends at 'purs'), and AHRQ's
      'in addition to amounts provided herein, $X shall be available from amounts available under section 241'
      (AHRQ is held only as its total, and the tagger reads no total's paragraph) -> transfer_in beside the
      headline.
  Infectious Diseases Rapid Response Reserve Fund  FY2020 Enacted sec. 239: 'Of the funds provided under the
      heading "CDC-Wide Activities and Program Support", $85,000,000 ... shall be available ... for deposit in the
      Infectious Diseases Rapid Response Reserve Fund' -> transfer_out inside the headline.

Each item names the sentence that holds it; the reader finds that sentence in the document's full text, checks the
amount is printed in it, and records the sentence as quoted_text. Nothing is fitted: an item whose sentence or
amount is not found, or whose account has no headline in the cell, is reported, not recorded.

    python reference/review/accounting/provision_lines.py --cache document_store     # downloads what it lacks

The full texts are the documents' own source_url (govinfo HTML for bills and laws; the Senate's PDF for the FY2021
Senate bill, read with pdftotext). They are cached under <cache>/provision_text/ (gitignored).
"""

import argparse
import csv
import html
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import check_legislative_text as CT  # noqa: E402
import tag_components as TC  # noqa: E402

OUT = Path(__file__).resolve().parent / "provision_lines.csv"
TEXT_DIR = ROOT / "data" / "legislative_text"
NEF = "the Nonrecurring Expenses Fund (section 223 of division G of P.L. 110-161)"
S241 = "section 241 of the PHS Act (evaluation funds)"
NEF_GP = r"Of the unobligated balances available (?:in fiscal year 2021 )?in the\W+Nonrecurring Expenses Fund\W+established"
COLUMNS = ["observation_id", "canonical_account_id", "fiscal_year", "stage", "component_type", "designation",
           "transfer_from", "amount", "in_headline", "source_document_id", "source_location", "quoted_text",
           "tag_rule", "notes"]

# (document, account, fiscal year, stage, component_type, transfer_from, amount, in_headline,
#  the sentence's start (regex), the sentence's end (regex, searched after the start), why this account)
ITEMS = [
    ("SRC-PLAW-115PUBL141", "ACC-HHS-CDC-BUILDINGS-FACILITIES", 2018, "Enacted", "transfer_in", NEF, 240000000,
     "FALSE", r"Provided, That in addition to the amount provided, \$240,000,000 shall be for a CDC biosafety",
     r": Provided further",
     "the proviso of the Buildings and Facilities paragraph: in addition to the amount provided, derived by transfer "
     "from the Fund"),
    ("SRC-PLAW-116PUBL94", "ACC-HHS-NIH-BUILDINGS-FACILITIES", 2020, "Enacted", "transfer_in", NEF, 225000000,
     "FALSE", r"Sec\. 237\. " + NEF_GP, r"Sec\. 238\.",
     "sec. 237 makes it available 'for buildings and facilities at the National Institutes of Health', in addition "
     "to any funds otherwise made available"),
    ("SRC-PLAW-116PUBL94", "ACC-HHS-CDC-BUILDINGS-FACILITIES", 2020, "Enacted", "transfer_in", NEF, 225000000,
     "FALSE", r"Sec\. 238\. " + NEF_GP, r"Sec\. 239\.",
     "sec. 238 makes it available for acquisition of real property, construction and renovation of facilities for "
     "the CDC's Chamblee Campus: the Buildings and Facilities account's purposes"),
    ("SRC-PLAW-116PUBL94", "ACC-HHS-CDC-PROGRAM-SUPPORT", 2020, "Enacted", "transfer_out", "", -85000000,
     "TRUE", r"Sec\. 239\. Of the funds provided under the heading\W+CDC-Wide Activities and Program Support",
     r"\(rescission\) Sec\. 240\.|Sec\. 240\.",
     "sec. 239: of the funds provided under the CDC-Wide Activities and Program Support heading, for deposit in the "
     "Infectious Diseases Rapid Response Reserve Fund (a fund we don't hold)"),
    ("SRC-PLAW-116PUBL260", "ACC-HHS-NIH-BUILDINGS-FACILITIES", 2021, "Enacted", "transfer_in", NEF, 225000000,
     "FALSE", r"Sec\. 237\. " + NEF_GP, r"\(rescission\) Sec\. 238\.|Sec\. 238\.",
     "sec. 237 makes it available for facilities of or used by the National Institutes of Health, in addition to "
     "any funds otherwise made available"),
    *[("SRC-BILL-LHHS-FY2021-SENATE", acc, 2021, "Senate Reported", "transfer_in", NEF, amt, "FALSE",
       r"SEC\. 237\. " + NEF_GP, r"SEC\. 238\.", why)
      for acc, amt, why in (
          ("ACC-HHS-HRSA-PRIMARY-CARE", 87000000,
           "sec. 237(1): 'for activities in the Health Center program under section 330 of the PHS Act' (Primary Health Care)"),
          ("ACC-HHS-HRSA-RYAN-WHITE", 50000000,
           "sec. 237(2): 'for activities in the Ryan White HIV/AIDS Program'"),
          ("ACC-HHS-CDC-HIV-HEPATITIS-STD-TB", 60000000,
           "sec. 237(3): 'for activities in CDC HIV prevention programs'"),
          ("ACC-HHS-NIH-BUILDINGS-FACILITIES", 229000000,
           "sec. 237(4): 'for buildings and facilities at the National Institutes of Health'"),
          ("ACC-HHS-OS-PHSSEF", 24000000, "sec. 237(5): 'for the Hospital Preparedness Program under section "
                                          "319C-2' (a PHSSEF activity)"),
          ("ACC-HHS-OS-PHSSEF", 50000000, "sec. 237(6): 'for expenses necessary to carry out section 319F-2(a)' (the "
                                          "Strategic National Stockpile), a PHSSEF activity"),
          ("ACC-HHS-OS-PHSSEF", 50000000, "sec. 237(7): section 319L of the PHS Act (BARDA advanced research and "
                                          "development), a PHSSEF activity"),
          ("ACC-HHS-OS-PHSSEF", 50000000, "sec. 237(8): 'for expenses necessary for the Secretary to prepare for or "
                                          "respond to an influenza pandemic' (PHSSEF)"),
      )],
    ("SRC-BILLS-117S4659IS", "ACC-HHS-CDC-BUILDINGS-FACILITIES", 2023, "Senate Reported", "transfer_in", NEF,
     37500000, "FALSE", r"Sec\. 235\. " + NEF_GP, r"Sec\. 236\.",
     "sec. 235 makes it available for acquisition of real property, design, construction and renovation for the "
     "CDC's replacement Lake Lynn Experimental Mine and Laboratory: the Buildings and Facilities account's purposes"),
    ("SRC-BILLS-117S3062IS", "ACC-HHS-CDC-SCIENTIFIC-SERVICES", 2022, "Senate Reported", "transfer_in", S241,
     132000000, "FALSE", r"Provided, That in addition to amounts provided herein, \$132,000,000 is available",
     r"(?<=PHS Act)", "the Public Health Scientific Services paragraph's proviso"),
    ("SRC-BILLS-117S3062IS", "ACC-HHS-CDC-ENVIRONMENTAL-HEALTH", 2022, "Senate Reported", "transfer_in", S241,
     7000000, "FALSE", r"Provided, That in addition to amounts provided herein, \$7,000,000 is available",
     r"(?<=PHS Act)", "the Environmental Health paragraph's proviso"),
    ("SRC-BILLS-117S3062IS", "ACC-HHS-AHRQ-TOTAL", 2022, "Senate Reported", "transfer_in", S241, 27000000, "FALSE",
     r"Provided further, That, in addition to amounts provided herein, \$27,000,000 shall be available",
     r"(?<=PHS Act)", "the Healthcare Research and Quality paragraph's proviso (AHRQ is held only as its total)"),
    ("SRC-BILLS-117HR4502RH", "ACC-HHS-AHRQ-TOTAL", 2022, "House Reported", "transfer_in", S241, 129208000, "FALSE",
     r"Provided, That in addition to amounts provided herein \$129,208,000 shall be available", r"(?<=PHS Act)",
     "the Healthcare Research and Quality paragraph's proviso (AHRQ is held only as its total)"),
    ("SRC-BILLS-116HR7614RH", "ACC-HHS-AHRQ-TOTAL", 2021, "House Reported", "transfer_in", S241, 199909000, "FALSE",
     r"Provided, That in addition to amounts provided herein, \$199,909,000 shall be available", r"(?<=PHS Act)",
     "the Healthcare Research and Quality paragraph's proviso (AHRQ is held only as its total)"),
    ("SRC-BILLS-116HR2740RH", "ACC-HHS-AHRQ-TOTAL", 2020, "House Reported", "transfer_in", S241, 18408000, "FALSE",
     r"Provided, That in addition to amounts provided herein, \$18,408,000 shall be available", r"(?<=PHS Act)",
     "the Healthcare Research and Quality paragraph's proviso (AHRQ is held only as its total)"),
]


def fetch(url, dest):
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (approps-intelligence provision lines)"})
        with urllib.request.urlopen(req, timeout=180) as r:
            dest.write_bytes(r.read())
    return dest


def full_text(doc, cache):
    """-> (the document's text with whitespace collapsed, [(offset, page label)]) from its source_url."""
    meta = json.loads((TEXT_DIR / f"{doc}.json").read_text())
    url = meta["source_url"]
    cache.mkdir(parents=True, exist_ok=True)
    if "govinfo.gov" in url:
        url = url.replace("/pdf/", "/html/").removesuffix(".pdf") + ".htm"
        raw = fetch(url, cache / f"{doc}.htm").read_bytes().decode("utf-8", "replace")
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
        pages = [(m.start(), m.group(1)) for m in re.finditer(r"\[\[Page (\d+ STAT\. \d+)\]\]", text)]
        return text, pages, url
    pdf = fetch(url, cache / f"{doc}.pdf")
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, check=True).stdout.decode()
    text, pages = "", []
    for n, page in enumerate(out.split("\f"), 1):
        page = re.sub(r"\n\s*\d+\s+", " ", page)        # the bill's line numbers
        pages.append((len(text), f"PDF p.{n}"))
        page = re.sub(r"\s+", " ", page)
        text += page + " "
    # the running heads ('November 6, 2020 (10:26 a.m.) U:\\...\\LHHSFY21.xml SEN. APPRO.'), blanked in place
    text = re.sub(r"\w+ \d+, \d{4} \(\d+:\d+ [ap]\.m\.\)\s+U:\\\S+\s+SEN\. APPRO\.(?:\s+\d+\b)?",
                  lambda m: " " * len(m.group(0)), text)
    return text, pages, url


def parsed_where(doc, amount, before):
    """The committed parsed text's location for the amount (check_legislative_text.where), when a paragraph prints it
    after the same words; else ''."""
    t = json.loads((TEXT_DIR / f"{doc}.json").read_text())
    tail = re.sub(r"\W+", " ", before[-40:]).strip().lower()
    for p in t["paragraphs"]:
        for a in p["amounts"]:
            b, _ = CT.split_phrase(a)
            if a["amount"] == amount and tail and tail in re.sub(r"\W+", " ", b).lower():
                return CT.where(p)
    return ""


def page_at(pages, i):
    label = ""
    for off, p in pages:
        if off > i:
            break
        label = p
    return label


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", default=str(ROOT / "document_store"), help="where the full texts are cached")
    args = ap.parse_args(argv)
    cache = Path(args.cache) / "provision_text"
    data = json.loads((ROOT / "data" / "staged.json").read_text())
    heads = TC.headline_of(data)
    texts, rows, problems = {}, [], []
    for doc, acc, fy, stage, typ, frm, amt, inside, start, end, why in ITEMS:
        if doc not in texts:
            texts[doc] = full_text(doc, cache)
        text, pages, url = texts[doc]
        m = re.search(start, text)
        if m is None:
            problems.append(f"{doc} {acc} FY{fy} {stage}: the sentence is not found ({start})")
            continue
        e = re.search(end, text[m.end():])
        sentence = text[m.start(): m.end() + (e.start() if e else 600)]
        sentence = re.sub(r"\s+", " ", re.sub(r"\[\[Page \d+ STAT\. \d+\]\]", " ", sentence)).strip()
        printed = f"${abs(amt):,}"
        item = re.match(r"sec\. 237\((\d)\)", why)
        if item:                                # one numbered item of a list: '(n) $X ...' up to the next item
            k = int(item.group(1))
            im = re.search(rf"\({k}\) ?\$[\d,]+[^;]*", sentence)
            sentence = sentence[:sentence.find(":") + 1] + " ... " + im.group(0) if im else ""
        if printed not in sentence:
            problems.append(f"{doc} {acc} FY{fy} {stage}: {printed} is not printed in the sentence")
            continue
        h = TC.pick_headline(heads[(acc, fy, stage)], "") if heads[(acc, fy, stage)] else None
        if h is None:
            problems.append(f"{doc} {acc} FY{fy} {stage}: no headline in the cell")
            continue
        sec = re.match(r"(Sec\.|SEC\.) (\d+)\.", sentence)
        where = (f"sec. 237({item.group(1)})" if item else f"sec. {sec.group(2)}" if sec else "proviso") + (f" ({page_at(pages, m.start())})"
                                                                  if page_at(pages, m.start()) else "")
        k = sentence.find(printed)
        parsed = parsed_where(doc, abs(amt), sentence[:k]) if k > 0 else ""
        if parsed:
            where += "; " + parsed
        rows.append({"observation_id": h["observation_id"], "canonical_account_id": acc, "fiscal_year": fy,
                     "stage": stage, "component_type": typ, "designation": "", "transfer_from": frm, "amount": amt,
                     "in_headline": inside, "source_document_id": doc, "source_location": where,
                     "quoted_text": sentence[:900], "tag_rule": "read from the full text (provision_lines.py): " + why,
                     "notes": ("beside the headline: in addition to the amount the paragraph appropriates"
                               if inside == "FALSE" else "inside the headline: part of the heading's own funds")})
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {OUT.relative_to(ROOT)}")
    for p in problems:
        print("  not recorded:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
