"""
Reconcile each printed "Total, Title II, Department of Health and Human
Services" against the same column's own printed agency totals, as printed.

    python reference/review/lhhs/title_ii_totals.py > reference/review/lhhs/title_ii_totals.csv

The rule, found from the documents (S.Rept. 118-84, 118-207, 119-55; the
FY2026 JES by hand) rather than assumed:

  Title II = HRSA + CDC + NIH (with CURES Act funding) + SAMHSA + AHRQ
             + CMS + ACF + ACL + ASPR + Office of the Secretary
             + every line under "General Provisions" (signed: a rescission counts negative)
             - the CURES Act line printed under the title total

  -- the CURES Act money is inside the NIH (and Public Health Service) total
     but outside the title total;
  -- general-provision lines (Medicare Operations, Sec. 227; the FY2026
     Nonrecurring Expenses Fund rescission, Sec. 237) belong to no agency
     total and count toward the title;
  -- where a Public Health Service rollup is printed it equals HRSA + CDC +
     NIH (with CURES) + SAMHSA + AHRQ, checked here too; S.Rept. 119-55 prints none.

A CJS-style check ("the title total is the sum of its agency totals, plus
some of the other lines") cannot find this: one term is subtracted.
"""

import csv
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

PHS_AGENCIES = [("HRSA", r"^Total, Health Resources and Services Administration$"),
                ("CDC", r"^Total, Centers for Disease Control and Prevention$"),
                ("NIH", r"^Total, National Institutes of Health (\(NIH\) with CURES Act funding|\(with CURES Act funding\))$"),
                ("SAMHSA", r"^Total, (SAMHSA|Substance Abuse and Mental Health Services Administration)$"),
                ("AHRQ", r"^Total, (AHRQ|Agency for Healthcare Research and Quality)$")]
OTHER_AGENCIES = [("CMS", r"^Total, Centers for Medicare (and|&) Medicaid Services$"),
                  ("ACF", r"^Total, Administration for Children and Families$"),
                  ("ACL", r"^Total, Administration for Community Living$"),
                  ("ASPR", r"^Total, (Administration|Office of the Assistant Secretary) for (Strategic )?Preparedness and Response$"),
                  ("OS", r"^Total, Office of the Secretary$")]
PHS = r"^Total, Public Health Service( with CURES Act funding)?$"
TITLE = r"^Total, Title II, Department of Health and Human Services$"
CURES = r"^\(?CURES Act\)?$"


def reconcile(path):
    d = json.load(open(path))
    pkg = d["source_document"]["package_id"]
    out = []
    for col in (c for c in d["extraction"]["columns"] if c["kind"] == "value"):
        rows = [o for o in d["observations"] if o["column_header"] == col["header"] and o["amount"] is not None]

        def one(rx, memo=False):
            hits = [o for o in rows if re.match(rx, o["account_name_as_written"].strip()) and bool(o["is_memo"]) == memo]
            return hits[0]["amount"] if len(hits) == 1 else (None if not hits else "ambiguous")
        title = one(TITLE)
        if title is None:
            continue
        parts = {k: one(rx) for k, rx in PHS_AGENCIES + OTHER_AGENCIES}
        gp = [o for o in rows if o["account_path"].startswith("GENERAL PROVISIONS") and not o["is_memo"]]
        cures = one(CURES, memo=True)
        missing = [k for k, v in parts.items() if not isinstance(v, int)]
        phs_printed = one(PHS)
        phs_sum = sum(parts[k] for k, _ in PHS_AGENCIES if isinstance(parts[k], int))
        total = sum(v for v in parts.values() if isinstance(v, int)) + sum(o["amount"] for o in gp) - (cures or 0)
        k = lambda v: "" if v is None else f"{v // 1000}"
        out.append({"document": pkg, "column_header": col["header"], "fiscal_year": col["fiscal_year"], "stage": col["stage"],
                    "printed_title_ii_thousands": k(title),
                    **{f"{a}_thousands": k(v) if isinstance(v, int) else (v or "not printed") for a, v in parts.items()},
                    "general_provisions_thousands": "; ".join(f"{o['account_name_as_written']} {o['amount'] // 1000}" for o in gp),
                    "cures_act_subtracted_thousands": k(cures),
                    "computed_thousands": k(total),
                    "reconciles": "yes" if not missing and total == title else ("no" if not missing else "missing: " + ", ".join(missing)),
                    "phs_rollup_printed_thousands": k(phs_printed) if isinstance(phs_printed, int) else "not printed",
                    "phs_rollup_equals_its_agencies": "" if not isinstance(phs_printed, int) else ("yes" if phs_printed == phs_sum else "NO")})
    return out


def main():
    rows = [r for p in sorted(glob.glob(str(ROOT / "extractions" / "*.title-ii.json"))) for r in reconcile(p)]
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main()
