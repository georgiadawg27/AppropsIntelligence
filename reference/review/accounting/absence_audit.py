"""
D-3b part 1: every confirmed absence whose citation is not bill or law text (for the President's Budget, the Budget
Appendix's proposed language). Rule (CLAUDE.md, Data): a confirmed absence cites bill or law text, never a table's
silence. The citation is the absence's source_document_id; its document_type decides:

  bill, public_law          bill or law text: kept
  budget_appendix           the request's proposed language (President's Budget only): kept
  committee_report,         a report or explanatory statement: listed (rests on a table or the report's text),
  explanatory_statement     with whether the evidence also names a bill or law (evidence_names_bill_or_law)

Writes absence_audit.csv (the list) and prints the counts; nothing in data/staged.json changes.

    python reference/review/accounting/absence_audit.py
    python reference/review/accounting/absence_audit.py --check   # fail when absence_audit.csv is not current
"""

import argparse
import collections
import csv
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import staged_data  # noqa: E402
STAGED = ROOT / "data" / "staged"
OUT = Path(__file__).resolve().parent / "absence_audit.csv"
TEXT_TYPES = {"bill", "public_law"}
REQUEST_TYPES = {"budget_appendix"}
# the evidence names a bill or law (a hint for the reviewer: the citation is still the report)
NAMES_TEXT = re.compile(r"\b(?:H\.R\.|S\.) ?\d+ as reported|govinfo BILLS-|\bP\.L\. ?\d+-\d+|Public Law \d+")
FIELDS = ["confirmed_absence_id", "canonical_account_id", "fiscal_year", "stage", "amount_type", "component",
          "source_document_id", "document_type", "evidence_names_bill_or_law", "evidence"]


def audit(data):
    types = {d["document_id"]: d["document_type"] for d in data["source_docs"]}
    rows = []
    for c in data["confirmed_absences"]:
        t = types.get(c["source_document_id"], "")
        if t in TEXT_TYPES or (t in REQUEST_TYPES and c["stage"] == "President's Budget"):
            continue
        rows.append({**{k: c[k] for k in FIELDS if k in c}, "document_type": t,
                     "evidence_names_bill_or_law": "TRUE" if NAMES_TEXT.search(c["evidence"]) else "FALSE"})
    return rows


def render(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    data = staged_data.load()
    rows = audit(data)
    text = render(rows)
    total = len(data["confirmed_absences"])
    print(f"confirmed absences: {total}; citing bill/law text or Appendix language: {total - len(rows)}; "
          f"citing a report or explanatory statement: {len(rows)}")
    for k, n in sorted(collections.Counter((r["document_type"], r["stage"]) for r in rows).items()):
        print(f"  {k[0]}, {k[1]}: {n}")
    print(f"  of which the evidence names a bill or law (a hint for review; the citation is still the report): "
          f"{sum(r['evidence_names_bill_or_law'] == 'TRUE' for r in rows)}")
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("absence_audit.csv is not current: run reference/review/accounting/absence_audit.py")
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
