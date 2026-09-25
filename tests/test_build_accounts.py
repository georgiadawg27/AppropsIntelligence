"""
reference/build_accounts.py: the Historical Name tab is the source of former
names; Account.historical_names is only a display list checked against it.

Run:  python -m unittest tests.test_build_accounts -v

Builds small workbooks in a temp dir (needs openpyxl, which is only required
for regenerating reference data).
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import openpyxl
except ImportError:                                  # pragma: no cover
    openpyxl = None

ACCOUNT_HEAD = ["canonical_account_id", "canonical_name", "agency", "bureau", "treasury_account_symbol", "status",
                "effective_start", "effective_end", "historical_names", "historical_identifiers", "fund_type",
                "subcommittee", "notes"]
HN_HEAD = ["historical_name_id", "canonical_account_id", "former_name", "evidence", "approved_date", "confidence",
           "human_reviewed"]


def load_builder():
    spec = importlib.util.spec_from_file_location("build_accounts", ROOT / "reference" / "build_accounts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(openpyxl, "openpyxl not installed")
class BuildAccounts(unittest.TestCase):
    def build(self, accounts, former):
        with tempfile.TemporaryDirectory() as d:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Account"
            ws.append(ACCOUNT_HEAD)
            for a in accounts:
                ws.append([a.get(k) for k in ACCOUNT_HEAD])
            hs = wb.create_sheet("Historical Name")
            hs.append(HN_HEAD)
            for h in former:
                hs.append([h.get(k) for k in HN_HEAD])
            path = Path(d) / "wb.xlsx"
            wb.save(path)
            mod = load_builder()
            mod.__file__ = str(Path(d) / "build_accounts.py")   # accounts.json is written next to the script
            mod.build(path)
            out = Path(d) / "accounts.json"
            return json.loads(out.read_text())["accounts"]

    def acct(self, aid, name, hist=None):
        return {"canonical_account_id": aid, "canonical_name": name, "agency": "NASA", "historical_names": hist,
                "notes": None}

    def hn(self, hid, aid, name, reviewed="TRUE"):
        return {"historical_name_id": hid, "canonical_account_id": aid, "former_name": name, "evidence": "e",
                "approved_date": "2026-09-25", "confidence": 1, "human_reviewed": reviewed}

    def test_names_come_from_the_tab_one_row_each(self):
        out = self.build([self.acct("A", "Exploration", "Deep Space Exploration Systems; Old Name, With Comma")],
                         [self.hn("HN-1", "A", "Deep Space Exploration Systems"),
                          self.hn("HN-2", "A", "Old Name, With Comma")])
        self.assertEqual(out[0]["historical_names"], ["Deep Space Exploration Systems", "Old Name, With Comma"])
        self.assertEqual(out[0]["historical_name_ids"], ["HN-1", "HN-2"])

    def test_unreviewed_names_are_not_matched(self):
        out = self.build([self.acct("A", "Exploration", "Deep Space Exploration Systems")],
                         [self.hn("HN-1", "A", "Deep Space Exploration Systems", reviewed="FALSE")])
        self.assertEqual(out[0]["historical_names"], [])
        self.assertEqual(out[0]["historical_names_unreviewed"], ["Deep Space Exploration Systems"])

    def test_display_list_drift_stops_the_build(self):
        with self.assertRaises(SystemExit) as cm:
            self.build([self.acct("A", "Exploration", "Deep Space Exploration Systems (FY2024 JES and earlier)")],
                       [self.hn("HN-1", "A", "Deep Space Exploration Systems")])
        self.assertIn("disagree", str(cm.exception))

    def test_unknown_account_stops_the_build(self):
        with self.assertRaises(SystemExit) as cm:
            self.build([self.acct("A", "Exploration")], [self.hn("HN-1", "B", "Something")])
        self.assertIn("not in the Account tab", str(cm.exception))


WORKBOOK = ROOT / "reference" / "CJS_Title_III_Science_Pilot_Schema_Loaded_v16.xlsx"


class CommittedReference(unittest.TestCase):
    def test_reference_carries_the_approved_former_names(self):
        accts = {a["canonical_account_id"]: a for a in json.loads((ROOT / "reference" / "accounts.json").read_text())["accounts"]}
        self.assertEqual({k: a["historical_names"] for k, a in accts.items() if a["historical_names"]}, {
            "ACC-NASA-EXPLORATION": ["Deep Space Exploration Systems"],
            "ACC-NSF-STEM-EDUCATION": ["Education and Human Resources"],
            "ACC-NASA-SPACEOPS": ["LEO and Spaceflight Operations"],
            "ACC-NASA-EXPLTECH": ["Exploration Research and Technology"],
            "ACC-NASA-STEM-ENGAGEMENT": ["Education", "STEM Opportunities formerly Education"]})
        self.assertNotIn("ACC-NASA-LEO", accts)
        self.assertFalse((ROOT / "reference" / "historical_names.json").exists())

    @unittest.skipUnless(openpyxl, "openpyxl not installed")
    def test_reference_is_in_sync_with_the_committed_workbook(self):
        # accounts.json is exactly what build_accounts.py makes from the
        # workbook the store loads -- a new workbook without a rebuild fails here
        committed = json.loads((ROOT / "reference" / "accounts.json").read_text())
        self.assertEqual(committed, load_builder().accounts_from_workbook(WORKBOOK))


if __name__ == "__main__":
    unittest.main()
