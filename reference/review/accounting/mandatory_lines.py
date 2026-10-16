"""
Mandatory portions read for the discretionary view (accounting views, phase 2): the mandatory money printed inside
a headline whose Budget Appendix account is 'mixed' (discretionary and mandatory budget authority in the same
year). Each becomes a mandatory_portion component inside the headline (scripts/tag_components.py reads
mandatory_lines.csv); the discretionary view of an agency or title total leaves it out.

  Promoting Safe and Stable Families (075-1512)  the bill or law text appropriates the mandatory part by
      section: 'For carrying out ... section 436 of the Social Security Act, $X and ... section 437 of such Act,
      $Y'. Section 436 is the mandatory part (House, Senate and Enacted: the committed bill and law text,
      tag_method auto_text). The President's Budget: the same phrase in the request's proposed language in FY N's
      Budget Appendix (tag_method appendix_text).
  Energy Employees Occupational Illness Compensation Program (part of CDC 075-0943)  the committee reports
      state the CDC program level 'includes $55,358,000 in mandatory funds under the terms of the Energy
      Employees Occupational Illness Compensation Program Act'. The whole line is the mandatory portion; the
      phrase is read from the headline's own document (tag_method report_text).

The Vaccine Injury Compensation Trust Fund's claims line (the mandatory part of 075-8175) is read with its
administrative line by table_lines.py.

    python reference/review/accounting/mandatory_lines.py --cache document_store     # downloads what it lacks

Needs pdftotext (poppler). Nothing is fitted: an amount that is not printed in the document, or that does not
fit inside its headline, is reported, not recorded.
"""

import argparse
import csv
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import check_legislative_text as CT  # noqa: E402
import legislative_text as L  # noqa: E402
import staged_data  # noqa: E402

OUT = Path(__file__).resolve().parent / "mandatory_lines.csv"
PSSF = "ACC-HHS-ACF-PSSF"
EEOICP = "ACC-HHS-CDC-EEOICP"
SEC436 = re.compile(r"section 436 of the Social Security Act,?\s*\[?\$\s?([\d,]+)\]?", re.I)
EEOICPA = re.compile(r"\$\s?([\d,]+)\s+in\s+mandatory\s+(?:funds|funding)\s+(?:under\s+the\s+terms\s+of\s+)?(?:the\s+)?"
                     r"Energy\s+Employees", re.I)
EEOICPA_ALT = re.compile(r"(?:recommends|includes|provides)\s+\$\s?([\d,]+)\s+(?:in\s+mandatory\s+funding\s+)?for\s+CDC\s+"
                         r"to\s+administer\s+the\s+(?:mandatory\s+)?Energy\s+Employees", re.I)
EEOICPA_SENATE = re.compile(r"includes\s+\$\s?([\d,]+)\s+for\s+the\s+Energy\s+Employees\s+Occupational\s+Illness\s+"
                            r"Compensation\s+Program\s+Act\s+\[EEOICPA\]\s*\(Pub-?\s?lic\s+Law\s+106-398\)\.\s+This\s+"
                            r"mandatory\s+funding", re.I)
COLUMNS = ["observation_id", "component_type", "amount", "source_document_id", "source_location", "quoted_text",
           "tag_method", "tag_rule", "notes"]


def fetch(url, dest):
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (approps-intelligence mandatory lines)"})
        with urllib.request.urlopen(req, timeout=120) as r:
            dest.write_bytes(r.read())
    return dest


def pdf_pages(pdf, layout=True):
    """Each page's text. The Appendix prints two columns: read it in reading order (layout=False)."""
    args = ["pdftotext"] + (["-layout"] if layout else []) + [str(pdf), "-"]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.split("\f")


def flat(s):
    return " ".join(s.replace("–", "-").split())


def headlines(data, account):
    return sorted((o for o in data["observations"] if o["canonical_account_id"] == account and not o.get("component")
                   and o["amount_type"] == "budget authority" and o["verification_status"] != "superseded"),
                  key=lambda o: o["observation_id"])


def pssf_text(data, rows, misses):
    """House, Senate and Enacted: the section 436 amount in the headline's own bill or law paragraph."""
    texts, exempt = CT.load_texts(L.TEXT_DIR), CT.load_exempt(L.TEXT_DIR)
    outcomes = CT.check(data, texts, exempt)
    for o in headlines(data, PSSF):
        if o["stage"] == "President's Budget":
            continue
        res = outcomes.get(o["observation_id"])
        para = res and res.get("paragraph")
        if not para:
            misses.append(f"{o['observation_id']} PSSF FY{o['fiscal_year']} {o['stage']}: no bill or law paragraph "
                          f"({res['result'] if res else 'not checked'})")
            continue
        hit = next((x for x in para["amounts"] if SEC436.search(x["phrase"])), None)
        if hit is None:
            misses.append(f"{o['observation_id']} PSSF FY{o['fiscal_year']} {o['stage']}: no section 436 amount in "
                          f"{res['document']}")
            continue
        add(rows, misses, o, hit["amount"], res["document"], CT.where(para), hit["phrase"], "auto_text",
            "PSSF: the bill or law text appropriates section 436 of the Social Security Act (the mandatory part) "
            "by its own amount")


def pssf_appendix(data, rows, misses):
    """The President's Budget: the section 436 amount in the request's proposed language (FY N's Appendix)."""
    import appendix_attributes as A
    chapters = {(fy, ch): (first, last, part) for fy, ch, first, last, part in A.CHAPTERS}
    for o in headlines(data, PSSF):
        if o["stage"] != "President's Budget":
            continue
        fy = int(o["fiscal_year"])
        first, last, part = chapters[(fy, "HHS")]
        pdf = A.CACHE / f"BUDGET-{fy}-APP-{part}.pdf"
        pages = pdf_pages(pdf, layout=False)
        found = None
        for i, t in enumerate(pages):
            ft = flat(t)
            if "PROMOTING SAFE AND STABLE FAMILIES" not in t.upper():
                continue
            m = SEC436.search(ft)
            if m:
                found = (i + 1, int(m.group(1).replace(",", "")) * 1, ft[max(0, m.start() - 40):m.end() + 120])
                break
        if found is None:
            misses.append(f"{o['observation_id']} PSSF FY{fy} President's Budget: no section 436 language in the FY{fy} "
                          "Appendix")
            continue
        pdf_page, amt, quote = found
        add(rows, misses, o, amt, A.document_id(fy, "HHS"), f"p.{first + pdf_page - 1}, proposed appropriations language",
            quote, "appendix_text", "PSSF: the request's proposed language appropriates section 436 of the Social "
            "Security Act (the mandatory part) by its own amount")


def eeoicp(data, docs, cache, rows, misses):
    """The committee report states the EEOICPA amount is mandatory: the whole line is the mandatory portion."""
    texts = {}
    for o in headlines(data, EEOICP):
        doc = o["source_document_id"]
        if doc not in texts:
            pdf = fetch(docs[doc]["url_or_identifier"], cache / f"{doc}.pdf")
            texts[doc] = pdf_pages(pdf)
        found = None
        for i, t in enumerate(texts[doc]):
            ft = flat(t)
            for rx in (EEOICPA, EEOICPA_ALT, EEOICPA_SENATE):
                m = rx.search(ft)
                if m:
                    found = (i + 1, int(m.group(1).replace(",", "")), ft[max(0, m.start() - 60):m.end() + 80])
                    break
            if found:
                break
        if found is None:
            misses.append(f"{o['observation_id']} EEOICP FY{o['fiscal_year']} {o['stage']}: {doc} states no mandatory "
                          "EEOICPA amount")
            continue
        page, amt, quote = found
        if amt != o["amount"]:
            misses.append(f"{o['observation_id']} EEOICP FY{o['fiscal_year']} {o['stage']}: {doc} p.{page} states "
                          f"${amt:,} mandatory, the line is ${o['amount']:,}")
            continue
        add(rows, misses, o, amt, doc, f"PDF p.{page}, Centers for Disease Control and Prevention narrative", quote,
            "report_text", "EEOICP: the report states the Energy Employees Occupational Illness Compensation "
            "Program Act funds are mandatory; the whole line is the mandatory portion")


def add(rows, misses, o, amount, doc, location, quote, method, rule):
    if not 0 < amount <= o["amount"]:
        misses.append(f"{o['observation_id']}: ${amount:,} does not fit inside the headline ${o['amount']:,}")
        return
    rows.append({"observation_id": o["observation_id"], "component_type": "mandatory_portion", "amount": amount,
                 "source_document_id": doc, "source_location": location, "quoted_text": flat(quote),
                 "tag_method": method, "tag_rule": rule,
                 "notes": "inside the headline: the mandatory part of a mixed account"})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / "document_store")
    a = ap.parse_args(argv)
    a.cache.mkdir(exist_ok=True)
    data = staged_data.load()
    docs = {d["document_id"]: d for d in data["source_docs"]}
    rows, misses = [], []
    pssf_text(data, rows, misses)
    pssf_appendix(data, rows, misses)
    eeoicp(data, docs, a.cache, rows, misses)
    rows.sort(key=lambda r: r["observation_id"])
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} mandatory portions -> {OUT.relative_to(ROOT)}")
    for m in misses:
        print("not read:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
