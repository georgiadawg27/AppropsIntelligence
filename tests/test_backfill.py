"""The Labor-HHS backfill's rules that are not figures (reference/review/lhhs/backfill)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference" / "review" / "lhhs" / "backfill"))
_argv, sys.argv = sys.argv, sys.argv[:1]
import build_year as B  # noqa: E402
sys.argv = _argv


class FormerNamesByYear(unittest.TestCase):
    """HRSA's 'Program Management' is ACC-HHS-HRSA-PROGRAM-SUPPORT's headline through FY2022 and a line inside it
    from FY2023 (owner, 2026-10-08): the name resolves by year, not by name."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data" / "staged.json").read_text())

    def test_the_name_resolves_differently_by_year(self):
        pm = ("ACC-HHS-HRSA-PROGRAM-SUPPORT", "program management")
        for fy in (2017, 2021, 2022):
            self.assertIn(pm, B.former_names(self.data, {}, fy), fy)
        for fy in (2023, 2026):
            self.assertNotIn(pm, B.former_names(self.data, {}, fy), fy)
        # an unbounded former name applies in every year
        self.assertIn(("ACC-HHS-NIH-NICHD", "national institute of child health and human development"),
                      B.former_names(self.data, {}, 2026))

    def test_the_data_follows_it(self):
        heads = [o for o in self.data["observations"] if o["canonical_account_id"] == "ACC-HHS-HRSA-PROGRAM-SUPPORT"
                 and not o["component"] and o["verification_status"] != "superseded"]
        printed_pm = {o["fiscal_year"] for o in heads if "printed as 'Program Management'" in o["source_table_or_section"]}
        self.assertEqual(printed_pm, {y for y in {o["fiscal_year"] for o in heads} if y <= 2022})
        self.assertFalse([o["observation_id"] for o in heads if o["fiscal_year"] >= 2023
                          and "'Program Management'" in o["source_table_or_section"]])


def published(sc="LHHS"):
    g = json.loads((ROOT / "docs" / "data" / "subcommittees" / f"{sc}.json").read_text())
    return {r["account"]["canonical_account_id"]: r for r in g["rows"]}


def head(cell):
    return next(l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"])


class OldStructure(unittest.TestCase):
    """ASPR's accounts split from PHSSEF (REL-LHHS-0009/0010/0016, confirmed 2026-10-08): a document that funds the
    programs inside PHSSEF -- every one before the split's effective year, FY2024 -- has no ASPR figure. Before the
    first figure (FY2023 Enacted, restated by S.Rept. 118-84) the cells read 'no figure'; the FY2023 House, request
    and Senate cells too, with the note 'Funded within PHSSEF in this document.'"""

    @classmethod
    def setUpClass(cls):
        cls.rows = published()

    def test_aspr(self):
        for aid in ("ACC-HHS-ASPR-TOTAL", "ACC-HHS-ASPR-RDP", "ACC-HHS-ASPR-OPER"):
            cells = self.rows[aid]["cells"]
            for key, cell in cells.items():
                y, st = int(key.split("|")[0]), key.split("|")[1]
                h = head(cell)
                if y < 2023:
                    # (a stage no committee reported -- FY2020 Senate -- reads 'not reported': no document at all)
                    self.assertIn((h["state"], h.get("note")), (("no_figure", None), ("not_reported", None)), (aid, key))
                    self.assertTrue(h["state"] == "no_figure" or (y, st) == (2020, "Senate Reported"), (aid, key))
                elif y == 2023 and st != "Enacted":
                    self.assertEqual((h["state"], h.get("note")), ("no_figure", "Funded within PHSSEF in this document."),
                                     (aid, key))
            first = head(cells["2023|Enacted"])
            self.assertEqual(first["state"], "value")
            self.assertEqual(first["observations"][0]["cell_note"], "Funded within PHSSEF before FY2023.")
        # the account it split from is untouched
        self.assertEqual(head(self.rows["ACC-HHS-OS-PHSSEF"]["cells"]["2022|Enacted"])["state"], "value")


class PhsEvaluationSetAside(unittest.TestCase):
    """A 0 headline with a program-level line from the PHS evaluation set-aside carries the note (ONC FY2022)."""

    def test_onc_fy2022(self):
        cells = published()["ACC-HHS-OS-ONC"]["cells"]
        for st, amount in (("House Reported", 86_614_000), ("President's Budget", 86_614_000),
                           ("Senate Reported", 86_614_000), ("Enacted", 64_238_000)):
            h = head(cells[f"2022|{st}"])
            o = h["observations"][0]
            self.assertEqual(o["amount"], 0, st)
            self.assertEqual(o["cell_note"], f"Funded through the PHS evaluation set-aside (${amount:,}), not new "
                                             "budget authority.", st)
            pl = [l for l in cells[f"2022|{st}"]["lines"] if l["component"] == "program_level"]
            self.assertEqual([l["observations"][0]["amount"] for l in pl], [amount], st)


class RetiredIds(unittest.TestCase):
    """A retired ID (reference/review/lhhs/retired_ids.json) is gone from the data and never reused."""

    def test_never_reused(self):
        data = json.loads((ROOT / "data" / "staged.json").read_text())
        retired = json.loads((ROOT / "reference" / "review" / "lhhs" / "retired_ids.json").read_text())
        live = {x["confirmed_absence_id"] for x in data["confirmed_absences"]} | \
            {r["relationship_id"] for r in data["relationships"]}
        gone = {i for v in retired.values() if isinstance(v, dict) for i in v}
        self.assertFalse(live & gone)
        self.assertIn("CA-LHHS-0075", gone)
        # the Title XVIII limitation's absences are retired: its cells before FY2026 read missing
        self.assertFalse([x for x in data["confirmed_absences"]
                          if x["canonical_account_id"] == "ACC-HHS-GP-MEDICARE-LIMITATION"])


class NotReported(unittest.TestCase):
    """A stage no committee reported -- no bill, no report, no draft (Bill Report Reference not_reported, FY2020
    Senate) -- reads 'not reported' in every account's cell, never missing."""

    def test_fy2020_senate(self):
        rows = published()
        states = {line["state"] for r in rows.values() for line in r["cells"].get("2020|Senate Reported", {}).get("lines", [])}
        self.assertEqual(states, {"not_reported"})
        counts = json.loads((ROOT / "docs" / "data" / "subcommittees" / "LHHS.json").read_text())["state_counts"]
        self.assertGreaterEqual(counts["not_reported"], len(rows))           # (and the Title II total row)


if __name__ == "__main__":
    unittest.main()
