"""
Advances read from the Budget Appendix (accounting views, component backfill): Child Support (075-1501), Foster
Care (075-1545) and Medicaid (075-0512) appropriate, besides the current year's amount, an advance 'for the first
quarter of fiscal year N+1'. scripts/tag_components.py reads advance_lines.csv:

  President's Budget, FY N   the advance the request's proposed language appropriates for the first quarter of
      FY N+1 (FY N's Appendix, the language with its bracketed deletions dropped) -> advance_enacted. Child Support
      and Foster Care only: Medicaid's request advances are held from the report tables (retagged). Whether it is
      inside the headline is read from the language: the headline equals the language's current-year amount (beside)
      or that amount plus the advance (inside); anything else keeps 'beside' with a note.
  FY2017, every stage        the advance the FY2016 law appropriated for the first quarter of FY2017, which becomes
      available in FY2017: FY2016's law is not a document we hold, so it is read from the FY2017 Appendix's
      language, where the enacted text it replaces is printed in brackets ('[$1,300,000,000] $1,400,000,000') ->
      advance_available. Later years take it from the FY N-1 law's own text (tag_components.py, no CSV row).

    python reference/review/accounting/advance_lines.py      # reads the cached HHS chapters (fetches what it lacks)

Needs pdftotext (poppler). Read from the text layer only; nothing is fitted.
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import appendix_attributes as A  # noqa: E402

OUT = Path(__file__).resolve().parent / "advance_lines.csv"
# where each account's language starts (the Appendix prints two columns, so the heading may sit apart from it)
ACCOUNTS = {"ACC-HHS-ACF-CHILD-SUPPORT": r"For carrying out, except as otherwise provided, titles I, IV-D, X, XI",
            "ACC-HHS-ACF-FOSTER-CARE": r"For carrying out, except as otherwise provided, title IV-E of the Social",
            "ACC-HHS-CMS-MEDICAID": r"For carrying out, except as otherwise provided, titles XI and XIX of the Social"}
REQUEST_ADVANCE = ("ACC-HHS-ACF-CHILD-SUPPORT", "ACC-HHS-ACF-FOSTER-CARE")
MONEY = re.compile(r"\$[\d,]+")
FIRST_Q = re.compile(r"first quarter of fiscal year", re.I)
COLUMNS = ["observation_id", "component_type", "amount", "in_headline", "source_document_id", "source_location",
           "quoted_text", "tag_rule", "notes", "request_current_year"]


def flat(s):
    return " ".join(s.replace("–", "-").split())


def dollars(s):
    return int(s.strip("$").replace(",", ""))


def outside(s):
    return re.sub(r"\[[^\]]*\]", " ", s)


def language(fy, start):
    """-> (printed page, the account's appropriations language up to the end of its advance clause)."""
    first, last, part = next((f, l, p) for y, ch, f, l, p in A.CHAPTERS if y == fy and ch == "HHS")
    pages = subprocess.run(["pdftotext", str(A.fetch(fy, part)), "-"], capture_output=True, text=True,
                           check=True).stdout.split("\f")
    for i, t in enumerate(pages):
        ft = flat(t)
        m = re.search(start, ft)
        if m is None:
            continue
        rest = ft[m.start():]
        q = FIRST_Q.search(rest)
        if q is None or q.start() > 1500:
            continue
        end = rest.find("to remain available", q.end())
        return first + i, rest[:end if end > 0 else q.end() + 160]
    return None, None


def advance(clause):
    """The advance clause -> (request amount, the bracketed enacted amount it replaces or None)."""
    q = FIRST_Q.search(clause)
    tail = clause[q.end():]
    req = MONEY.search(outside(tail))
    old = next((MONEY.search(b) for b in re.findall(r"\[([^\]]*)\]", tail) if MONEY.search(b)), None)
    return (dollars(req.group()) if req else None), (dollars(old.group()) if old else None)


def headlines(data, account):
    return sorted((o for o in data["observations"] if o["canonical_account_id"] == account and not o.get("component")
                   and o["amount_type"] == "budget authority" and o["verification_status"] != "superseded"),
                  key=lambda o: o["observation_id"])


def main():
    data = json.loads((ROOT / "data" / "staged.json").read_text())
    rows, misses = [], []
    years = sorted({y for y, ch, *_ in A.CHAPTERS if ch == "HHS"})
    for account, start in ACCOUNTS.items():
        heads = headlines(data, account)
        for fy in years:
            if account not in REQUEST_ADVANCE and fy != 2017:
                continue                # Medicaid: only the FY2016 advance (its request advances are held)
            page, text = language(fy, start)
            if text is None:
                misses.append(f"{account} FY{fy}: no advance language found in the FY{fy} Appendix")
                continue
            req, old = advance(text)
            main_amt = MONEY.search(outside(text))
            main_amt = dollars(main_amt.group()) if main_amt else None
            q = FIRST_Q.search(text)
            quote = text[max(0, q.start() - 60):].strip()
            doc = A.document_id(fy, "HHS")
            loc = f"p.{page}, proposed appropriations language"
            if account in REQUEST_ADVANCE:
                for o in heads:
                    if int(o["fiscal_year"]) != fy or o["stage"] != "President's Budget":
                        continue
                    if req is None:
                        misses.append(f"{o['observation_id']} FY{fy} President's Budget: no request advance")
                        continue
                    if o["amount"] == main_amt:
                        inside, note = "FALSE", "beside the headline: the headline is the language's current-year amount"
                    elif o["amount"] == main_amt + req:
                        inside, note = "TRUE", ("inside the headline: the headline is the language's current-year "
                                                "amount plus the advance")
                    else:
                        inside, note = "FALSE", (f"beside the headline by default: the headline ${o['amount']:,} is "
                                                 f"neither the language's current-year amount ${main_amt:,} nor that "
                                                 "plus the advance")
                    rows.append({"observation_id": o["observation_id"], "component_type": "advance_enacted",
                                 "amount": req, "in_headline": inside, "source_document_id": doc,
                                 "source_location": loc, "quoted_text": quote,
                                 "tag_rule": f"the request's language appropriates an advance for the first quarter "
                                             f"of fiscal year {fy + 1}", "notes": note,
                                 "request_current_year": main_amt})
            if fy == 2017:
                if old is None:
                    misses.append(f"{account} FY2017: no bracketed FY2016 advance in the FY2017 Appendix")
                    continue
                for o in heads:
                    if int(o["fiscal_year"]) != 2017:
                        continue
                    rows.append({"observation_id": o["observation_id"], "component_type": "advance_available",
                                 "amount": old, "in_headline": "", "source_document_id": doc,
                                 "source_location": loc, "quoted_text": quote,
                                 "tag_rule": "the FY2016 law's advance for the first quarter of fiscal year 2017 "
                                             "(the enacted text the FY2017 request's language prints in brackets), "
                                             "available in FY2017",
                                 "notes": "",   # in_headline: tag_components.py, as for every advance_available
                                 "request_current_year": main_amt if o["stage"] == "President's Budget" else ""})
    rows.sort(key=lambda r: (r["observation_id"], r["component_type"]))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} advance lines -> {OUT.relative_to(ROOT)}")
    for m in misses:
        print("not read:", m)


if __name__ == "__main__":
    main()
