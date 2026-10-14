"""
D-3b (follow-ups from #72): records changed in data/staged.json. Supersede, never overwrite; no ID reused.

Part 1, CA-LHHS-0037 (CDC-Wide Activities and Program Support, FY2023 President's Budget): the absence rested on the
House report's table (H.Rept. 117-403 p.813), which a confirmed absence may not. The FY2023 Budget Appendix's
proposed language for the heading 'CDC-WIDE ACTIVITIES AND PROGRAM SUPPORT' (HHS chapter pp.437-438, read with
request_language.chapter_paragraphs) appropriates $9,620,961,000 "to carry out titles II, III, IV, VII, XI, XV, XVII,
XIX, XXI, XXIII, XXVI, and XXVIII of the PHS Act ..." -- one appropriation for all of CDC (the report's CDC total for
the request, 9,676,319 thousand, less EEOICP's mandatory 55,358 thousand, is the same 9,620,961 thousand), not the
account's scope. It neither prints a request amount for the account nor confirms none, so the absence is retired
(the brief's third outcome): the cell becomes missing, and the request column's $160,000,000 Fund line under the
account (pphf_table.csv) stays reported, not recorded (tag_components' 'PPHF rows with no headline'), for review
(reference/review/accounting/review-D3b.md).

    python reference/review/accounting/d3b_records.py           # what would change
    python reference/review/accounting/d3b_records.py --write   # write it (idempotent)
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGED = ROOT / "data" / "staged.json"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"

RETIRE_ABSENCES = {
    "CA-LHHS-0037": "CDC-Wide Activities FY2023 President's Budget: rested on a table's silence (H.Rept. 117-403 "
                    "p.813); the FY2023 Budget Appendix's language under the heading (HHS chapter pp.437-438) "
                    "appropriates $9,620,961,000 for all of CDC, not the account's scope: it neither prints the "
                    "account's request nor confirms none, so the cell is missing (D-3b, 2026-10-14)",
}


def retire_absences(data, retired):
    gone = [c for c in data["confirmed_absences"] if c["confirmed_absence_id"] in RETIRE_ABSENCES]
    for c in gone:
        print(f"retire {c['confirmed_absence_id']}: {RETIRE_ABSENCES[c['confirmed_absence_id']]}")
    data["confirmed_absences"] = [c for c in data["confirmed_absences"]
                                  if c["confirmed_absence_id"] not in RETIRE_ABSENCES]
    retired["confirmed_absences"].update(RETIRE_ABSENCES)
    return bool(gone)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    data = json.loads(STAGED.read_text())
    retired = json.loads(RETIRED.read_text())
    changed = retire_absences(data, retired)
    if not changed:
        print("nothing to change")
    if args.write and changed:
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        RETIRED.write_text(json.dumps(retired, indent=1) + "\n")
        print("data/staged.json and retired_ids.json updated")


if __name__ == "__main__":
    sys.exit(main())
