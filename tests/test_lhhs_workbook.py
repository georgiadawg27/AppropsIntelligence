"""
The Labor-HHS Title II rows prepared for v29 (reference/review/lhhs/workbook/
lhhs_*.csv) load cleanly on top of the reference workbook.

Run:  python -m unittest tests.test_lhhs_workbook -v
"""

import csv
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WB = ROOT / "reference" / "review" / "lhhs" / "workbook"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WB))

import approps_store as S  # noqa: E402
import merge_check  # noqa: E402


def rows(name):
    return list(csv.DictReader(open(WB / f"lhhs_{name}.csv", newline="")))


class LhhsRowsLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with redirect_stdout(io.StringIO()):
            cls.report = merge_check.main([])

    def test_loads_with_no_warnings(self):
        self.assertEqual(self.report["warnings"], [])
        self.assertEqual(self.report["rows"]["account"], 30 + len(rows("account")))
        self.assertEqual(self.report["rows"]["appropriations_observation"], 870 + len(rows("observation")))

    def test_scope(self):
        accts = {a["canonical_account_id"]: a for a in rows("account")}
        # 10 agency totals, the title total, 9 accounts, AHA, 4 General Provisions lines
        self.assertEqual(len(accts), 25)
        self.assertEqual({a["subcommittee"] for a in accts.values()}, {"Labor-HHS-Education"})
        self.assertEqual(sorted(int(a["display_order"]) for a in accts.values()), list(range(1, 26)))
        gp = [a for a in accts.values() if a["bureau"] == "General Provisions"]
        self.assertEqual(len(gp), 4)
        self.assertTrue(all(not S.rollup_scope(a) for a in gp))       # accounts, not rollups
        aha = accts["ACC-HHS-AHA-TOTAL"]
        self.assertEqual((aha["status"], aha["effective_start"]), ("proposed", "2025-10-01"))
        self.assertEqual({o["stage"] for o in rows("observation") if o["canonical_account_id"] == "ACC-HHS-AHA-TOTAL"},
                         {"President's Budget"})

    def test_title_ii_reconciles_in_the_store(self):
        # with the General Provisions lines as accounts, every recorded Title II total is its agency
        # totals + the General Provisions lines (a rescission signed) - CURES, from the store alone
        cells = self.report["title_ii"]
        self.assertEqual(len(cells), 13)
        self.assertEqual({r["reconciles_through_rollups"] for r in cells}, {"yes"})

    def test_senate_rescissions_are_bill_level(self):
        # the Senate reports print the HHS rescissions after the grand total, outside Title II
        nef = [o for o in rows("observation") if o["canonical_account_id"] == "ACC-HHS-GP-NEF-RESCISSION"
               and o["stage"] == "Senate Reported"]
        self.assertEqual({(o["fiscal_year"], o["amount"], o["component"]) for o in nef},
                         {("2025", "-1656000000", ""), ("2025", "0", "emergency"), ("2026", "-1613000000", "")})
        self.assertTrue(all("outside Title II" in o["source_table_or_section"] for o in nef))

    def test_jes_hand_reads_are_human_entered(self):
        jes = [o for o in rows("observation") if o["source_document_id"] == "SRC-EXPL-LHHS-FY2026-ENACTED"]
        self.assertTrue(jes)
        self.assertEqual({o["extraction_method"] for o in jes}, {"human_entered"})

    def test_decisions(self):
        obs = rows("observation")
        nih = [o for o in obs if o["canonical_account_id"] == "ACC-HHS-NIH-TOTAL" and o["component"] == "CURES"]
        self.assertTrue(nih and all(o["headline_observation_id"] for o in nih))
        med = {(o["amount_type"], o["component"]) for o in obs if o["canonical_account_id"] == "ACC-HHS-CMS-MEDICAID"}
        self.assertEqual(med, {("budget authority", ""), ("budget authority", "program_level_available_this_fiscal_year"),
                               ("budget authority", "appropriated_in_this_bill"), ("advance", ""),
                               ("prior_year_advance", "")})
        self.assertTrue(all(int(o["amount"]) < 0 for o in obs if o["amount_type"] == "prior_year_advance"))
        # the FY2025 "Estimate" column is held back, not stored
        self.assertFalse(any(o["fiscal_year"] == "2025" and o["stage"] == "Enacted" for o in obs))
        self.assertTrue(rows("held_fy2025_estimate"))


if __name__ == "__main__":
    unittest.main()
