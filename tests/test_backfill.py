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
        self.assertEqual(printed_pm, {2021, 2022})
        self.assertFalse([o["observation_id"] for o in heads if o["fiscal_year"] >= 2023
                          and "'Program Management'" in o["source_table_or_section"]])


if __name__ == "__main__":
    unittest.main()
