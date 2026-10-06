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
        # v32: + the 35 FY2025 Enacted rows (H.Rept. 119-271's "FY 2025 Estimate" column), sent outside these CSVs
        self.assertEqual(self.report["rows"]["appropriations_observation"], 870 + len(rows("observation")) + 35)

    def test_scope(self):
        accts = {a["canonical_account_id"]: a for a in rows("account")}
        # 10 agency totals, the title total, 9 accounts, AHA, 4 General Provisions lines
        self.assertEqual(len(accts), 25)
        self.assertEqual({a["subcommittee"] for a in accts.values()}, {"LHHS"})
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
        self.assertEqual(len(cells), 14)                              # v32: FY2025 Enacted
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
        self.assertEqual({o["extraction_method"] for o in jes}, {"human-entered"})    # the Dictionary's spelling
        # the export emits only Data Dictionary values
        self.assertLessEqual({o["extraction_method"] for o in rows("observation")},
                             {"AI-extracted", "human-entered", "hybrid", "text-extracted"})

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


class ContainedAndViewLinesAreNeverAdded(unittest.TestCase):
    """headline_observation_id: a contained line (CURES, inside NIH's 'with
    CURES Act funding' headline) or a view (another printed scope of the
    headline) shows next to its headline and never enters a sum; a part does."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        wb, db = Path(cls.tmp.name) / "v29_candidate.xlsx", Path(cls.tmp.name) / "approps.db"
        merge_check.merged(S.reference_workbook(), wb)
        with redirect_stdout(io.StringIO()):
            S.load(wb, db)
        cls.db = db
        cls.conn = S.connect(db, readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def cell(self, account, fy, stage):
        return [dict(r) for r in self.conn.execute(
            "SELECT o.*, c.kind AS component_kind FROM appropriations_observation o "
            "LEFT JOIN component c ON c.component_id = o.component WHERE canonical_account_id = ? AND fiscal_year = ? "
            "AND stage = ? AND amount_type = 'budget authority'", (account, fy, stage))]

    def test_nih_total_is_its_headline_not_headline_plus_cures(self):
        lines = self.cell("ACC-HHS-NIH-TOTAL", 2024, "Senate Reported")
        head = next(o for o in lines if o["component"] is None)
        cures = next(o for o in lines if o["component"] == "CURES")
        self.assertEqual((head["amount"], cures["amount"]), (47_811_518_000, 407_000_000))   # S.Rept. 118-84, as printed
        self.assertEqual(cures["headline_observation_id"], head["observation_id"])
        self.assertEqual(S.cell_total(self.conn, "ACC-HHS-NIH-TOTAL", 2024, "Senate Reported"), head["amount"])
        self.assertNotIn(cures, S.additive_lines(lines))
        # and as the grid shows it: next to the headline, marked not added
        grid = S.history_grid(S.history(self.conn, "ACC-HHS-NIH-TOTAL"))
        row = next(r for r in grid["rows"] if r["fiscal_year"] == 2024)
        line = next(l for l in row["cells"]["Senate Reported"] if l["component"] == "CURES")
        self.assertEqual((line["component_kind"], line["component_label"], line["adds_to_headline"]),
                         ("contained", "CURES Act", False))

    def test_a_view_never_enters_any_sum(self):
        views = [dict(r) for r in self.conn.execute(
            "SELECT * FROM appropriations_observation WHERE component = 'program_level_excluding_arpa_h'")]
        self.assertTrue(views)
        for v in views:
            head = self.conn.execute("SELECT amount FROM appropriations_observation WHERE observation_id = ?",
                                     (v["headline_observation_id"],)).fetchone()[0]
            self.assertNotEqual(v["amount"], head)                       # a different figure, so a sum would show it
            self.assertEqual(S.cell_total(self.conn, v["canonical_account_id"], v["fiscal_year"], v["stage"]), head)
            self.assertNotIn(v["observation_id"],
                             [o["observation_id"] for o in S.additive_lines(self.cell(v["canonical_account_id"],
                                                                                      v["fiscal_year"], v["stage"]))])
            # the title reconciliation's base lines never include one either
            sys.path.insert(0, str(ROOT / "reference" / "review"))
            import title_totals as T
            base, _, via = T.cell_lines(self.conn, "Title II", v["fiscal_year"], v["stage"], "LHHS")
            self.assertNotIn(v["observation_id"], [r["observation_id"] for r in base + via])
        # nor is it ever "missing" where a document doesn't print that scope
        grid = S.history_grid(S.history(self.conn, "ACC-HHS-NIH-TOTAL"))
        self.assertFalse([l for r in grid["rows"] for ls in r["cells"].values() for l in ls
                          if l["component_kind"] in ("contained", "view") and l["state"] == "missing"])

    def test_the_title_ii_total_is_the_titles_total_not_a_bill_total(self):
        grid = S.subcommittee_grid(self.conn, "LHHS")
        self.assertIsNone(grid["bill_total"])                         # no Labor-HHS bill total is on file
        self.assertEqual([(t["title"], t["total"]["account"]["canonical_account_id"]) for t in grid["titles"]],
                         [("Title II", "ACC-HHS-TITLE-II-TOTAL")])
        # ... whatever its notes say: total_scope 'title' decides it, even under a note that reads as a bill total
        import shutil, sqlite3, tempfile
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "notes.db"
            shutil.copy(self.db, db)
            c = sqlite3.connect(db)
            c.execute("UPDATE account SET notes = 'Derived rollup -- bill total (Grand total)' "
                      "WHERE canonical_account_id = 'ACC-HHS-TITLE-II-TOTAL'")
            c.commit()
            c.close()
            conn = S.connect(db, readonly=True)
            g = S.subcommittee_grid(conn, "LHHS")
            conn.close()
        self.assertIsNone(g["bill_total"])
        self.assertEqual(g["titles"][0]["total"]["account"]["canonical_account_id"], "ACC-HHS-TITLE-II-TOTAL")
        t = g["titles"][0]["total"]["cells"]["2024|Senate Reported"]["lines"]
        head = next(l for l in t if l["component"] is None)["observations"][0]
        self.assertEqual((head["observation_id"], head["amount"]), ("OBS-LHHS-0436", 1_266_744_593_000))

    def test_a_part_still_adds(self):
        # NSF Research and Related Activities: its defense line is a part of the account's figure
        row = self.conn.execute("SELECT canonical_account_id, fiscal_year, stage FROM appropriations_observation "
                                "WHERE component = 'defense' LIMIT 1").fetchone()
        lines = self.cell(*row)
        self.assertEqual(S.cell_total(self.conn, *row), sum(o["amount"] for o in lines))
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
